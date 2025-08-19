from __future__ import annotations
import json, re, sys, threading, time, logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
import fal_client

from archi3d.adapters.base import (
    ModelAdapter, Token, ExecResult,
    AdapterTransientError, AdapterPermanentError
)
from archi3d.utils.text import slugify

# A..Z ordering helper (mirrors the Tripo3D adapter logic you approved)
_SUFFIX_RE = re.compile(r"_([A-Z])(?:\.[^.]+)$", re.IGNORECASE)
def _order_by_letter(files: List[str]) -> List[int]:
    def key(idx: int) -> Tuple[int, str]:
        name = Path(files[idx]).name
        m = _SUFFIX_RE.search(name)
        if m:
            rank = ord(m.group(1).upper()) - ord("A")
            if 0 <= rank <= 25:
                return (0, f"{rank:02d}")
        return (1, name.lower())
    return sorted(range(len(files)), key=key)

class Hunyuan3DMultiviewV2Adapter(ModelAdapter):
    """
    Adapter for 'fal-ai/hunyuan3d/v2/multi-view'.
    We map A,B,C → front,left,back deterministically and upload via fal CDN.
    """

    def _upload_images(self, abs_image_paths: List[Path]) -> List[str]:
        return [fal_client.upload_file(p) for p in abs_image_paths]  # Path-safe on Windows

    def _assign_views(self, image_urls: List[str], rel_files: List[str]) -> Dict[str, str]:
        """
        Deterministic mapping using filename letters when present:
          A -> front_image_url
          B -> left_image_url
          C -> back_image_url
        If more than 3 images are passed (shouldn't happen via batching), we take the first 3 after ordering.
        """
        if len(image_urls) < 3:
            raise AdapterPermanentError("Hunyuan v2 multi requires exactly 3 images (front/left/back).")

        idx = _order_by_letter(rel_files)
        ordered = [image_urls[i] for i in idx]
        front = ordered[0]
        left  = ordered[1]
        back  = ordered[2]
        return {
            "front_image_url": front,
            "left_image_url":  left,
            "back_image_url":  back,
        }

    def execute(self, token: Token, deadline_s: int = 480) -> ExecResult:
        cfg = self.cfg
        endpoint = str(cfg["endpoint"])
        log_file = self.logs_dir / f"{slugify(token.product_id)}_{slugify(token.algo)}_{token.job_id[:8]}.log"

        # SETUP ADAPTER-SPECIFIC FILE HANDLER
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        self.logger.addHandler(file_handler)

        # 1) Resolve absolute paths and upload
        abs_paths = [self.workspace / rel for rel in token.image_files]
        try:
            image_urls = self._upload_images(abs_paths)
        except BaseException as e:
            msg = f"[ERROR] Upload failed: {e!r}"
            self.logger.error(msg)
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
            if "FAL_KEY" in str(e) or "MissingCredentialsError" in e.__class__.__name__:
                raise AdapterPermanentError("Missing fal.ai credentials (FAL_KEY or FAL_KEY_ID/FAL_KEY_SECRET)") from e
            raise AdapterTransientError(f"Upload failed: {e}") from e

        # 2) Build arguments (defaults + mapped views). 'textured_mesh' true per request.
        defaults: Dict[str, Any] = dict(cfg.get("defaults") or {})
        view_args = self._assign_views(image_urls, token.image_files)
        arguments = {**defaults, **view_args}  # seed omitted (provider default)

        # 3) Subscribe with logs; show only last line on console; write full log to file.
        result_container: Dict[str, Any] = {}
        err_container: Dict[str, BaseException | None] = {"e": None}

        def on_queue_update(update):
            if isinstance(update, fal_client.InProgress) and update.logs:
                for log in update.logs:
                    if "message" in log:
                        self.logger.info(log["message"])
                last = update.logs[-1]
                if "message" in last:
                    msg = last["message"].strip()
                    sys.stdout.write(f"\r\033[K> {msg}")
                    sys.stdout.flush()

        def _runner():
            try:
                res = fal_client.subscribe(
                    endpoint,
                    arguments=arguments,
                    with_logs=True,
                    on_queue_update=on_queue_update,
                )
                result_container.update(res if isinstance(res, dict) else {"_raw": res})
            except BaseException as e:
                err_container["e"] = e

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=deadline_s)

        # Clear transient console line
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

        if t.is_alive():
            msg = f"[ERROR] Deadline exceeded ({deadline_s}s); cancelling locally."
            self.logger.error(msg)
            raise AdapterTransientError(f"Timeout after {deadline_s}s")
        if err_container["e"] is not None:
            self.logger.error(f"Provider error: {err_container['e']!s}")
            raise AdapterTransientError(str(err_container["e"]))

        # 4) Expect model_mesh.url per API
        result = result_container
        mesh = result.get("model_mesh") if isinstance(result, dict) else None
        if isinstance(mesh, dict) and "url" in mesh:
            exec_result = ExecResult(
                glb_path=str(mesh["url"]),
                timings=result.get("timings") or {},
                request_id=result.get("request_id"),
            )
        else:
            self.logger.error(f"[ERROR] Unexpected response: {json.dumps(result)[:2000]}")
            raise AdapterPermanentError("Unexpected output format (missing model_mesh.url)")

        # IMPORTANT: CLEAN UP HANDLER
        self.logger.removeHandler(file_handler)
        file_handler.close()

        return exec_result
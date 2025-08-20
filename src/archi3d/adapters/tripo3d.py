from __future__ import annotations
import json, re, threading, time, sys, logging
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import requests
import fal_client

from archi3d.adapters.base import (
    ModelAdapter, Token, ExecResult,
    AdapterTransientError, AdapterPermanentError
)
from archi3d.utils.text import slugify

# Extract trailing _A.jpg / _B.png etc. (case-insensitive)
_SUFFIX_RE = re.compile(r"_([A-Z])(?:\.[^.]+)$", re.IGNORECASE)

def _order_by_letter(files: List[str]) -> List[int]:
    """Return indices that sort files by trailing letter A..Z if present; else stable by name."""
    def key(idx: int) -> Tuple[int, str]:
        name = Path(files[idx]).name
        m = _SUFFIX_RE.search(name)
        if m:
            # Map A..Z -> 0..25; anything else to large number
            rank = ord(m.group(1).upper()) - ord("A")
            if 0 <= rank <= 25:
                return (0, f"{rank:02d}")
        return (1, name.lower())
    return sorted(range(len(files)), key=key)

class Tripo3DMultiV2p5Adapter(ModelAdapter):
    """
    Adapter for 'tripo3d/tripo/v2.5/multiview-to-3d'.
    Maps up to four images to front/left/back/right in a stable order (A,B,C,D or filename).
    """

    def _upload_images(self, abs_image_paths: List[Path]) -> List[str]:
        urls: List[str] = []
        for p in abs_image_paths:
            # Use Path object (Windows-safe) -> fal client handles it.
            urls.append(fal_client.upload_file(p))
        return urls

    def _assign_views(self, image_urls: List[str], rel_files: List[str]) -> Dict[str, str]:
        """
        Deterministic mapping:
          front <- 1st, left <- 2nd, back <- 3rd, right <- 4th (after A..D ordering).
        If fewer than 4, fill what exists; we do NOT invent URLs.
        """
        indices = _order_by_letter(rel_files)
        ordered = [image_urls[i] for i in indices]
        out: Dict[str, str] = {}
        if len(ordered) >= 1: out["front_image_url"] = ordered[0]
        if len(ordered) >= 2: out["left_image_url"]  = ordered[1]
        if len(ordered) >= 3: out["back_image_url"]  = ordered[2]
        if len(ordered) >= 4: out["right_image_url"] = ordered[3]
        return out

    def execute(self, token: Token, deadline_s: int = 480) -> ExecResult:
        cfg = self.cfg
        endpoint = str(cfg["endpoint"])
        log_file = self.logs_dir / f"{slugify(token.product_id)}_{slugify(token.algo)}_{token.job_id[:8]}.log"

        # --- SETUP ADAPTER-SPECIFIC FILE HANDLER ---
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        self.logger.addHandler(file_handler)
        # ---------------------------------------------

        # 1) Resolve absolute paths and upload to fal CDN
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

        # 2) Build arguments
        defaults: Dict[str, Any] = dict(cfg.get("defaults") or {})
        # Your policy ensures correct n_images; still, we construct robustly
        view_args = self._assign_views(image_urls, token.image_files)
        arguments = {**defaults, **view_args}  # seed omitted (provider default)

        # 3) Subscribe with logs and deadline
        result_container: Dict[str, Any] = {}
        err_container: Dict[str, BaseException | None] = {"e": None}

        def on_queue_update(update):
            if isinstance(update, fal_client.InProgress) and update.logs:
                # Write all logs to the file for complete history
                for log in update.logs:
                    if "message" in log:
                        self.logger.info(log["message"])
                
                # Get the last message to display on the console
                last_log = update.logs[-1]
                if "message" in last_log:
                    # Strip newlines to prevent flooding the console
                    msg = last_log["message"].strip()
                    # Print to console, overwriting previous line
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

        # Clear the line after the process is finished
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        
        if t.is_alive():
            msg = f"[ERROR] Deadline exceeded ({deadline_s}s); cancelling locally."
            self.logger.error(msg)
            raise AdapterTransientError(f"Timeout after {deadline_s}s")
        if err_container["e"] is not None:
            self.logger.error(f"Provider error: {err_container['e']!s}")
            raise AdapterTransientError(str(err_container["e"]))

        # 4) Prefer pbr_model → model_mesh → base_model
        result = result_container
        exec_result = None
        for key in ("pbr_model", "model_mesh", "base_model"):
            f = result.get(key) if isinstance(result, dict) else None
            if isinstance(f, dict) and "url" in f:
                exec_result = ExecResult(glb_path=str(f["url"]), timings=result.get("timings") or {}, request_id=result.get("task_id"))
                break
        
        if exec_result is None:
            self.logger.error(f"[ERROR] Unexpected response: {json.dumps(result)[:2000]}")
            raise AdapterPermanentError("No usable model URL in response (pbr_model/model_mesh/base_model missing)")

        # --- IMPORTANT: CLEAN UP HANDLER ---
        self.logger.removeHandler(file_handler)
        file_handler.close()
        # ------------------------------------
        
        return exec_result
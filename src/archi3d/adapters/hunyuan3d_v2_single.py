# src/archi3d/adapters/hunyuan3d_v2_single.py
from __future__ import annotations

import json
import threading
import sys
import logging
from pathlib import Path
from typing import Any, Dict

import requests
import fal_client

from archi3d.adapters.base import (
    ModelAdapter, Token, ExecResult,
    AdapterTransientError, AdapterPermanentError,
)
from archi3d.utils.text import slugify
from archi3d.utils.uploads import upload_file_safely


class Hunyuan3DSingleV2Adapter(ModelAdapter):
    """
    Single-image adapter for fal-ai/hunyuan3d/v2.

    Input:
      - input_image_url (string) — required single image URL.  

    Defaults applied here:
      - textured_mesh=True (other params at provider defaults).  

    Output:
      - model_mesh.url — generated 3D object file (GLB).  
    """

    def _upload_image(self, abs_image_path: Path) -> str:
        # Uploads to fal temporary storage and returns a signed URL
        return upload_file_safely(abs_image_path)

    def _download_file(self, url: str, out_path: Path) -> None:
        # Not used currently; helper retained for parity and potential future caching
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with out_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=812):
                    if chunk:
                        f.write(chunk)

    def execute(self, token: Token, deadline_s: int = 600) -> ExecResult:
        cfg = self.cfg or {}
        endpoint = cfg.get("endpoint")
        if not endpoint:
            raise AdapterPermanentError(
                "Missing configuration for 'hunyuan3d_v2_single': add a block in adapters.yaml with "
                "endpoint: 'fal-ai/hunyuan3d/v2', unit_price_usd, price_source, and defaults."
            )

        log_file = self.logs_dir / f"{slugify(token.product_id)}_{slugify(token.algo)}_{token.job_id[:8]}.log"
        
        # --- SETUP ADAPTER-SPECIFIC FILE HANDLER ---
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        self.logger.addHandler(file_handler)
        # ---------------------------------------------

        # 1) Expect exactly one image
        if not token.image_files:
            raise AdapterPermanentError("No image provided to single-image adapter")
        abs_path = self.workspace / token.image_files[0]

        # 2) Upload (surface early errors to stderr, classify credentials as permanent)
        try:
            image_url = self._upload_image(abs_path)
        except BaseException as e:
            msg = f"[ERROR] Upload failed: {e!r}"
            self.logger.error(msg)
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
            if "FAL_KEY" in str(e) or "MissingCredentialsError" in e.__class__.__name__:
                raise AdapterPermanentError("Missing fal.ai credentials (FAL_KEY or FAL_KEY_ID/FAL_KEY_SECRET)") from e
            raise AdapterTransientError(f"Upload failed: {e}") from e

        # 3) Build arguments:
        #    Hunyuan v2 uses 'input_image_url'; we enable textured_mesh in defaults.  
        defaults: Dict[str, Any] = dict(cfg.get("defaults") or {})
        arguments: Dict[str, Any] = {**defaults, "input_image_url": image_url}

        # 4) Subscribe with provider logs; persist all, show only last line live
        result_container: Dict[str, Any] = {}
        err_container: Dict[str, BaseException | None] = {"e": None}

        def on_queue_update(update):
            if isinstance(update, fal_client.InProgress) and update.logs:
                for log in update.logs:
                    if "message" in log:
                        self.logger.info(log["message"])
                last_msg = update.logs[-1].get("message", "").strip()
                if last_msg:
                    sys.stdout.write(f"\r\033[K> {last_msg}")
                    sys.stdout.flush()

        def _runner():
            try:
                res = fal_client.subscribe(
                    str(endpoint),
                    arguments=arguments,
                    with_logs=True,
                    on_queue_update=on_queue_update,
                )
                if isinstance(res, dict):
                    result_container.update(res)
                else:
                    result_container["_raw"] = res
            except BaseException as e:
                err_container["e"] = e

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=deadline_s)

        # Clear the live console line
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

        if t.is_alive():
            msg = f"[ERROR] Deadline exceeded ({deadline_s}s); cancelling locally."
            self.logger.error(msg)
            raise AdapterTransientError(f"Timeout after {deadline_s}s")

        if err_container["e"] is not None:
            self.logger.error(f"Provider error: {err_container['e']!s}")
            sys.stderr.write(f"[ERROR] Provider error: {err_container['e']!r}\n")
            sys.stderr.flush()
            raise AdapterTransientError(str(err_container["e"]))

        # 5) Parse output (expect model_mesh.url)  
        result = result_container
        mesh = result.get("model_mesh")
        url = mesh.get("url") if isinstance(mesh, dict) else None
        if url:
            exec_result = ExecResult(
                glb_path=str(url),
                timings=result.get("timings") or {},
                request_id=result.get("request_id") or result.get("task_id"),
            )
        else:
            self.logger.error(f"[ERROR] Unexpected response: {json.dumps(result)[:2000]}")
            raise AdapterPermanentError("Unexpected output format (missing model_mesh.url)")

        # --- IMPORTANT: CLEAN UP HANDLER ---
        self.logger.removeHandler(file_handler)
        file_handler.close()
        # ------------------------------------

        return exec_result
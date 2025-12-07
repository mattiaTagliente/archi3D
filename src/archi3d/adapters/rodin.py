# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

from __future__ import annotations
import json, threading, time, sys, logging
from pathlib import Path
from typing import Any, Dict, List

import requests
import fal_client

from archi3d.adapters.base import (
    ModelAdapter, Token, ExecResult,
    AdapterTransientError, AdapterPermanentError
)
from archi3d.utils.text import slugify
from archi3d.utils.uploads import upload_file_safely

class RodinMultiAdapter(ModelAdapter):
    """
    Adapter for Hyper3D Rodin (multi-image) -> 'fal-ai/hyper3d/rodin'.
    We pass all views via `input_image_urls` (order-agnostic per API).
    """

    def _upload_images(self, abs_image_paths: List[Path]) -> List[str]:
        urls: List[str] = []
        for p in abs_image_paths:
            urls.append(upload_file_safely(p))  # Path-safe on Windows
        return urls

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

        # 2) Build arguments from config defaults + uploads
        defaults: Dict[str, Any] = dict(cfg.get("defaults") or {})
        arguments: Dict[str, Any] = {**defaults, "input_image_urls": image_urls}
        # seed intentionally omitted → provider default
        # geometry_file_format left to default "glb"

        # 3) Subscribe with logs and deadline; display only last line on console
        result_container: Dict[str, Any] = {}
        err_container: Dict[str, BaseException | None] = {"e": None}

        def on_queue_update(update):
            if isinstance(update, fal_client.InProgress) and update.logs:
                # persist full log
                for log in update.logs:
                    if "message" in log:
                        self.logger.info(log["message"])
                # show last line on console
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

        # clear console line
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

        if t.is_alive():
            msg = f"[ERROR] Deadline exceeded ({deadline_s}s); cancelling locally."
            self.logger.error(msg)
            raise AdapterTransientError(f"Timeout after {deadline_s}s")
        if err_container["e"] is not None:
            self.logger.error(f"Provider error: {err_container['e']!s}")
            raise AdapterTransientError(str(err_container["e"]))

        # 4) Expect `model_mesh.url` (per API)
        result = result_container
        mesh = result.get("model_mesh") if isinstance(result, dict) else None
        if isinstance(mesh, dict) and "url" in mesh:
            exec_result = ExecResult(
                glb_path=str(mesh["url"]),
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
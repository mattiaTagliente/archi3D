# src/archi3d/adapters/hunyuan3d_v2p1_single.py
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


class Hunyuan3DSingleV2p1Adapter(ModelAdapter):
    """
    Single-image adapter for fal-ai/hunyuan3d-v21.

    Input:
      - input_image_url (string) — single image URL.  # API schema
    Defaults applied here:
      - textured_mesh=True (others at provider defaults).
    Output preference:
      - model_glb_pbr.url > model_glb.url > model_mesh.url  # API schema
    """

    # ---- helpers ------------------------------------------------------------
    def _upload_image(self, abs_image_path: Path) -> str:
        # Uploads to fal temporary storage and returns a signed URL
        return fal_client.upload_file(abs_image_path)

    def _download_file(self, url: str, out_path: Path) -> None:
        # Not used currently; kept for potential local caching
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with out_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

    # ---- main ---------------------------------------------------------------
    def execute(self, token: Token, deadline_s: int = 600) -> ExecResult:
        cfg = self.cfg or {}
        endpoint = cfg.get("endpoint")
        if not endpoint:
            raise AdapterPermanentError(
                "Missing configuration for 'hunyuan3d_v2p1_single': add a block in adapters.yaml with "
                "endpoint: 'fal-ai/hunyuan3d-v21', unit_price_usd, price_source, and defaults."
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

        # 2) Upload (surface early errors to stderr; classify credentials as permanent)
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
        #    Hunyuan3D v2.1 uses 'input_image_url'; 'textured_mesh' is enabled via defaults.
        defaults: Dict[str, Any] = dict(cfg.get("defaults") or {})
        arguments: Dict[str, Any] = {**defaults, "input_image_url": image_url}

        # 4) Subscribe with provider logs; persist all, show only the last line live
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
                    str(endpoint),  # "fal-ai/hunyuan3d-v21"
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

        # Clear live console line
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

        # 5) Parse output (prefer PBR GLB, then GLB, then mesh ZIP)
        result = result_container

        def _pick_url(d: Dict[str, Any] | None) -> str | None:
            return d.get("url") if isinstance(d, dict) else None

        url = (
            _pick_url(result.get("model_glb_pbr"))  # optional PBR GLB
            or _pick_url(result.get("model_glb"))   # required GLB
            or _pick_url(result.get("model_mesh"))  # assets ZIP
        )
        if url:
            exec_result = ExecResult(
                glb_path=str(url),
                timings=result.get("timings") or {},
                request_id=result.get("request_id") or result.get("task_id"),
            )
        else:
            self.logger.error(f"[ERROR] Unexpected response: {json.dumps(result)[:2000]}")
            raise AdapterPermanentError("Unexpected output format (missing model_glb/_pbr/model_mesh URL)")

        # --- IMPORTANT: CLEAN UP HANDLER ---
        self.logger.removeHandler(file_handler)
        file_handler.close()
        # ------------------------------------
        
        return exec_result
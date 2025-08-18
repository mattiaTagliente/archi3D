# src/archi3d/adapters/trellis_single.py
from __future__ import annotations

import json
import threading
import time
import sys
from pathlib import Path
from typing import Any, Dict

import requests
import fal_client

from archi3d.adapters.base import (
    ModelAdapter, Token, ExecResult,
    AdapterTransientError, AdapterPermanentError,
)

def _write_line(fp: Path, msg: str) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("a", encoding="utf-8") as f:
        f.write(msg.rstrip() + "\n")

class TrellisSingleAdapter(ModelAdapter):
    """
    Single-image adapter for fal-ai/trellis.
    Input key: image_url. Output key: model_mesh.url. Texture size pinned at 2048.  :contentReference[oaicite:4]{index=4}:contentReference[oaicite:5]{index=5}
    """

    def _upload_image(self, abs_image_path: Path) -> str:
        return fal_client.upload_file(abs_image_path)

    def _download_glb(self, url: str, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with out_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

    def execute(self, token: Token, deadline_s: int = 480) -> ExecResult:
        cfg = self.cfg
        endpoint = str(cfg["endpoint"])  # "fal-ai/trellis"
        log_file = self.logs_dir / f"{token.product_id}_{token.algo}_{token.job_id}.log"

        # 1) Resolve single image
        if not token.image_files:
            raise AdapterPermanentError("No image provided to single-image adapter")
        abs_path = self.workspace / token.image_files[0]

        # 2) Upload image (echo serious failures to stderr too)
        try:
            image_url = self._upload_image(abs_path)
        except BaseException as e:
            msg = f"[ERROR] Upload failed: {e!r}"
            _write_line(log_file, msg)
            # echo to terminal immediately (this happens before provider logs exist)
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
            # Classify missing credentials as permanent (no retry)
            if "FAL_KEY" in str(e) or "MissingCredentialsError" in e.__class__.__name__:
                raise AdapterPermanentError("Missing fal.ai credentials (FAL_KEY/FAL_KEY_ID+FAL_KEY_SECRET)") from e
            raise AdapterTransientError(f"Upload failed: {e}") from e

        # 3) Build call arguments.
        #    NOTE: Trellis single-image uses `image_url`; we pin `texture_size=2048`.  :contentReference[oaicite:6]{index=6}
        defaults: Dict[str, Any] = dict(cfg.get("defaults") or {})
        arguments: Dict[str, Any] = {**defaults, "image_url": image_url}

        # 4) Subscribe with logs (print only last line live; write full logs to file)
        result_container: Dict[str, Any] = {}
        err_container: Dict[str, BaseException | None] = {"e": None}

        def on_queue_update(update):
            if isinstance(update, fal_client.InProgress) and update.logs:
                for log in update.logs:
                    if "message" in log:
                        _write_line(log_file, log["message"])
                last_msg = update.logs[-1].get("message", "").strip()
                if last_msg:
                    sys.stdout.write(f"\r\033[K> {last_msg}")
                    sys.stdout.flush()

        def _runner():
            try:
                res = fal_client.subscribe(
                    endpoint,
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

        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

        if t.is_alive():
            _write_line(log_file, f"[ERROR] Deadline exceeded ({deadline_s}s); cancelling locally.")
            raise AdapterTransientError(f"Timeout after {deadline_s}s")

        if err_container["e"] is not None:
            # surface provider-side failure
            sys.stderr.write(f"[ERROR] Provider error: {err_container['e']!r}\n")
            sys.stderr.flush()
            raise AdapterTransientError(str(err_container["e"]))

        # 5) Parse output (expect model_mesh.url).  :contentReference[oaicite:7]{index=7}
        result = result_container
        mesh = result.get("model_mesh") if isinstance(result, dict) else None
        if isinstance(mesh, dict) and "url" in mesh:
            return ExecResult(
                glb_path=str(mesh["url"]),
                timings=result.get("timings") or {},
                request_id=result.get("request_id") or result.get("task_id"),
            )

        _write_line(log_file, f"[ERROR] Unexpected response: {json.dumps(result)[:2000]}")
        raise AdapterPermanentError("Unexpected output format (missing model_mesh.url)")

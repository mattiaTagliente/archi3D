# src/archi3d/adapters/trellis_single.py
from __future__ import annotations

import json
import threading
import time
import sys
from pathlib import Path
from typing import Any, Dict, List

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
    - Upload exactly one image to fal storage
    - Call endpoint with {"image_url": <uploaded_url>, **defaults}
    - Stream logs: write full provider logs to file, show only the last line live
    - Return model_mesh.url (remote GLB)
    """

    # ---- helpers ------------------------------------------------------------

    def _upload_image(self, abs_image_path: Path) -> str:
        # Path-safe on Windows; returns a signed URL hosted by fal
        return fal_client.upload_file(abs_image_path)

    def _download_glb(self, url: str, out_path: Path) -> None:
        # (Not used currently: we return the remote URL; keep helper for parity)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with out_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

    # ---- main hook ----------------------------------------------------------

    def execute(self, token: Token, deadline_s: int = 480) -> ExecResult:
        cfg = self.cfg
        endpoint = str(cfg["endpoint"])  # expected: "fal-ai/trellis"
        log_file = self.logs_dir / f"{token.product_id}_{token.algo}_{token.job_id}.log"

        # 1) Resolve absolute image path from workspace (batch policy guarantees 1 image)
        if not token.image_files:
            raise AdapterPermanentError("No image provided to single-image adapter")
        abs_path = self.workspace / token.image_files[0]

        # 2) Upload image to fal CDN
        start_upload = time.monotonic()
        try:
            image_url = self._upload_image(abs_path)
        except BaseException as e:
            _write_line(log_file, f"[ERROR] Upload failed: {e!r}")
            raise AdapterTransientError(f"Upload failed: {e}") from e
        _ = time.monotonic() - start_upload  # reserved for possible timing

        # 3) Build arguments from config defaults + uploaded URL
        # NOTE: Input key is `image_url`, texture size configured via defaults. :contentReference[oaicite:1]{index=1}
        defaults: Dict[str, Any] = dict(cfg.get("defaults") or {})
        arguments: Dict[str, Any] = {**defaults, "image_url": image_url}

        # 4) Subscribe with logs and deadline; display only last line on console
        result_container: Dict[str, Any] = {}
        err_container: Dict[str, BaseException | None] = {"e": None}

        def on_queue_update(update):
            if isinstance(update, fal_client.InProgress) and update.logs:
                # Persist all logs to file
                for log in update.logs:
                    if "message" in log:
                        _write_line(log_file, log["message"])

                # Show only the last message live
                last_log = update.logs[-1]
                if "message" in last_log:
                    msg = last_log["message"].strip()
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

        # Clear the live console line after finishing
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

        if t.is_alive():
            _write_line(log_file, f"[ERROR] Deadline exceeded ({deadline_s}s); cancelling locally.")
            raise AdapterTransientError(f"Timeout after {deadline_s}s")

        if err_container["e"] is not None:
            raise AdapterTransientError(str(err_container["e"]))

        # 5) Parse output (expect model_mesh.url) :contentReference[oaicite:2]{index=2}
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
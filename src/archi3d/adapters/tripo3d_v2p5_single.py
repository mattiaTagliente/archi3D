# src/archi3d/adapters/tripo3d_v2p5_single.py
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

class Tripo3DSingleV2p5Adapter(ModelAdapter):
    """
    Single-image adapter for tripo3d/tripo/v2.5/image-to-3d.

    Input:
      - image_url (string)  — single image URL.  :contentReference[oaicite:2]{index=2}
      - pbr=True, texture="HD" (others default).  Note: provider states that when pbr=True,
        texture is effectively enabled; we still pass "HD" as requested.  :contentReference[oaicite:3]{index=3}

    Output preference: pbr_model.url > model_mesh.url > base_model.url.  :contentReference[oaicite:4]{index=4}
    """

    def _upload_image(self, abs_image_path: Path) -> str:
        # Returns a signed URL on fal storage
        return fal_client.upload_file(abs_image_path)

    def _download_file(self, url: str, out_path: Path) -> None:
        # Not used currently; helper retained for parity
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with out_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

    def execute(self, token: Token, deadline_s: int = 600) -> ExecResult:
        cfg = self.cfg
        endpoint = str(cfg["endpoint"])  # expected: "tripo3d/tripo/v2.5/image-to-3d"
        log_file = self.logs_dir / f"{token.product_id}_{token.algo}_{token.job_id}.log"

        # 1) One image is expected (batch policy guarantees it)
        if not token.image_files:
            raise AdapterPermanentError("No image provided to single-image adapter")
        abs_path = self.workspace / token.image_files[0]

        # 2) Upload (echo auth/config errors to stderr too)
        try:
            image_url = self._upload_image(abs_path)
        except BaseException as e:
            msg = f"[ERROR] Upload failed: {e!r}"
            _write_line(log_file, msg)
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
            if "FAL_KEY" in str(e) or "MissingCredentialsError" in e.__class__.__name__:
                raise AdapterPermanentError("Missing fal.ai credentials (FAL_KEY or FAL_KEY_ID/FAL_KEY_SECRET)") from e
            raise AdapterTransientError(f"Upload failed: {e}") from e

        # 3) Build arguments from config defaults + uploaded URL
        #    Input key is 'image_url'.  :contentReference[oaicite:5]{index=5}
        defaults: Dict[str, Any] = dict(cfg.get("defaults") or {})
        arguments: Dict[str, Any] = {**defaults, "image_url": image_url}

        # 4) Subscribe with log streaming (persist all; show only last line)
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
            sys.stderr.write(f"[ERROR] Provider error: {err_container['e']!r}\n")
            sys.stderr.flush()
            raise AdapterTransientError(str(err_container["e"]))

        # 5) Parse output (prefer pbr_model.url)  :contentReference[oaicite:6]{index=6}
        result = result_container
        def _pick_url(d: Dict[str, Any] | None) -> str | None:
            return d.get("url") if isinstance(d, dict) else None

        url = _pick_url(result.get("pbr_model")) or _pick_url(result.get("model_mesh")) or _pick_url(result.get("base_model"))
        if url:
            return ExecResult(
                glb_path=str(url),
                timings=result.get("timings") or {},
                request_id=result.get("request_id") or result.get("task_id"),
            )

        _write_line(log_file, f"[ERROR] Unexpected response: {json.dumps(result)[:2000]}")
        raise AdapterPermanentError("Unexpected output format (missing pbr_model/model_mesh/base_model URL)")

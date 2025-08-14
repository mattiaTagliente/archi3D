from __future__ import annotations
import time, json, threading
from pathlib import Path
from typing import Any, Dict, List
import requests
import fal_client
from archi3d.adapters.base import ModelAdapter, Token, ExecResult, AdapterTransientError, AdapterPermanentError

def _write_line(fp: Path, msg: str) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("a", encoding="utf-8") as f:
        f.write(msg.rstrip() + "\n")

class TrellisMultiAdapter(ModelAdapter):
    """Adapter for fal-ai/trellis/multi (multi-image)."""

    def _upload_images(self, abs_image_paths: List[Path]) -> List[str]:
        urls: List[str] = []
        for p in abs_image_paths:
            urls.append(fal_client.upload_file(p))
        return urls

    def _download_glb(self, url: str, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with out_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

    def execute(self, token: Token, deadline_s: int = 480) -> ExecResult:
        algo_cfg = self.cfg  # already resolved to the specific key
        log_file = self.logs_dir / f"{token.product_id}_{token.algo}_{token.job_id}.log"

        # 1) Resolve absolute image paths (workspace is the root).
        abs_paths = [self.workspace / rel for rel in token.image_files]

        # 2) Upload images to fal CDN.
        start_upload = time.monotonic()
        image_urls = self._upload_images(abs_paths)
        upload_s = time.monotonic() - start_upload

        # 3) Build arguments from config defaults + uploads
        endpoint = str(algo_cfg["endpoint"])
        defaults: Dict[str, Any] = dict(algo_cfg.get("defaults") or {})
        defaults["image_urls"] = image_urls
        
        # 4) Invoke with logs; enforce deadline via a worker thread
        result_container: Dict[str, Any] = {}
        err_container: Dict[str, BaseException | None] = {"e": None}

        def on_queue_update(update):
            if isinstance(update, fal_client.InProgress):
                if update.logs:
                    for log in update.logs:
                        if "message" in log:
                            _write_line(log_file, log["message"])

        def _runner():
            try:
                res = fal_client.subscribe(endpoint, arguments=defaults, with_logs=True, on_queue_update=on_queue_update)
                result_container.update(res if isinstance(res, dict) else {"_raw": res})
            except BaseException as e:
                err_container["e"] = e

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=deadline_s)

        if t.is_alive():
            _write_line(log_file, f"[ERROR] Deadline exceeded ({deadline_s}s); cancelling locally.")
            raise AdapterTransientError(f"Timeout after {deadline_s}s")

        if err_container["e"] is not None:
            raise AdapterTransientError(str(err_container["e"]))

        result = result_container
        mesh = result.get("model_mesh") if isinstance(result, dict) else None
        if not isinstance(mesh, dict) or "url" not in mesh:
            _write_line(log_file, f"[ERROR] Unexpected response: {json.dumps(result)[:2000]}")
            raise AdapterPermanentError("Unexpected output format (missing model_mesh.url)")

        # 5) Return the remote URL as a string
        glb_url = mesh["url"]
        return ExecResult(glb_path=glb_url, timings=result.get("timings") or {}, request_id=result.get("request_id"))

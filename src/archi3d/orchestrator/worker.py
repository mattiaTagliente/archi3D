# archi3d/orchestrator/worker.py
from __future__ import annotations

import json
import os
import re
import time
import getpass
import logging
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd
import requests
from filelock import FileLock

from archi3d import __version__
from archi3d.adapters.base import (
    AdapterPermanentError,
    AdapterTransientError,
    Token,
)
from archi3d.adapters.registry import REGISTRY
from archi3d.config.adapters_cfg import load_adapters_cfg
from archi3d.config.paths import PathResolver
# Import the canonical function from batch.py
from archi3d.orchestrator.batch import _compose_job_id


# Matches trailing "_A.jpg" / "_B.png" (case-insensitive)
_SUFFIX_RE = re.compile(r"_([A-Z])(?:\.[^.]+)$", re.IGNORECASE)


@dataclass
class ExecResult:
    job_id: str
    run_id: str
    product_id: str
    variant: str
    algo: str
    image_set: str
    n_images: int
    img_suffixes: str
    status: str  # "completed" | "failed"
    started_at: str
    finished_at: str
    duration_s: float
    output_glb_relpath: str
    worker: str
    error_msg: str
    lpips: Optional[float]
    fscore: Optional[float]
    unit_price_usd: float
    estimated_cost_usd: float
    price_source: str


# ---------------------------
# Utilities
# ---------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str, max_len: int = 32) -> str:
    """
    Lowercase, keep [a-z0-9-], collapse dashes, trim to max_len.
    """
    t = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    t = re.sub(r"-{2,}", "-", t)
    return t[:max_len]


def _derive_variant_slug(product_id: str, first_image_rel: str) -> str:
    """
    first_image_rel looks like 'dataset/<folder_name>/images/<file>'
    If <folder_name> is '12345 - Curved backrest' we extract 'Curved backrest' as variant.
    If it's just '12345', variant is empty -> slug ''.
    """
    try:
        p = Path(first_image_rel)
        # Expected structure has at least 2 parts: 'dataset', '<folder_name>'
        if len(p.parts) < 2:
            logging.warning(
                f"Could not derive variant slug: path '{first_image_rel}' has fewer than 2 parts."
            )
            return ""

        folder_name = p.parts[1]  # dataset/<folder_name>/images/...

    except IndexError:
        # This will catch cases where the path is malformed, e.g., not containing enough parts.
        logging.warning(
            f"Could not derive variant slug from path '{first_image_rel}'. "
            "Path structure is not as expected ('dataset/<folder>/...').",
            exc_info=True,  # exc_info=True adds the traceback to the log
        )
        return ""

    # folder_name may be "12345" or "12345 - Variant Name"
    if " - " in folder_name:
        pid, var = folder_name.split(" - ", 1)
        if pid.strip() == product_id.strip():
            return _slugify(var, max_len=32)
    return ""


def _img_suffixes_from_list(image_files: List[str], max_len: int = 20) -> str:
    """
    Extract letters A..Z from filenames; fallback to empty if not present.
    Return joined with '-' and truncated for filename safety.
    """
    letters: List[str] = []
    for s in image_files:
        m = _SUFFIX_RE.search(Path(s).name)
        if m:
            letters.append(m.group(1).upper())
    suffixes = "-".join(letters) if letters else ""
    return suffixes[:max_len]


def _select_tokens(queue_dir: Path, run_id: str, algo: str) -> List[Path]:
    """
    Support BOTH legacy and new readable patterns:

    Legacy:  *__{algo}__{jobid8}.todo.json
    New:     *_{algo}_N*_*_{run_id}_h*.todo.json
    """
    legacy = sorted(queue_dir.glob(f"*__{algo}__*.todo.json"))
    readable = sorted(queue_dir.glob(f"*_{algo}_N*_*_{run_id}_h*.todo.json"))
    # Prefer readable first, then legacy
    return readable + legacy


def _rename_atomic(p: Path, new_suffix: str) -> Path:
    """
    Rename <name>.<state>.json to <name>.<new_suffix>.json atomically.
    """
    target = p.with_suffix(f".{new_suffix}.json")
    p.rename(target)
    return target


def _writerow_locked(paths: PathResolver, row: dict) -> None:
    """
    Append (by read-append-write) under a filesystem lock for safety across machines.
    """
    lock_path = paths.results_parquet.with_suffix(".parquet.lock")
    with FileLock(str(lock_path)):
        # Check if the results file exists and has content
        if paths.results_parquet.exists() and paths.results_parquet.stat().st_size > 0:
            df = pd.read_parquet(paths.results_parquet)
            # Create a DataFrame for the new row
            new_row_df = pd.DataFrame([row])
            # Concatenate the existing DataFrame with the new row
            df = pd.concat([df, new_row_df], ignore_index=True)
        else:
            # If the file doesn't exist or is empty, create a new DataFrame from the first row
            df = pd.DataFrame([row])

        df.to_parquet(paths.results_parquet, index=False)


def _compose_output_names(
    run_id: str,
    algo: str,
    product_id: str,
    variant_slug: str,
    n_images: int,
    img_suffixes: str,
    job_id8: str,
) -> Tuple[Path, Path]:
    """
    Build human-readable filenames with a short unique hash tail.
    Ensures total filename length isn't excessive.
    """
    safe_variant = variant_slug  # already slugified
    # Assemble core name parts
    core = f"{product_id}_{safe_variant}_" if safe_variant else f"{product_id}__"
    core += f"{algo}_N{n_images}"
    if img_suffixes:
        core += f"_{img_suffixes}"
    core += f"_{run_id}_h{job_id8}"

    # Enforce a soft cap on filename length for OneDrive friendliness
    if len(core) > 120:
        # truncate variant further if needed
        parts = core.split("_")
        # product_id, variant/blank, algo, N..., suffixes..., run_id, h...
        # Try to shrink the second part (variant or empty)
        if parts[1]:
            parts[1] = parts[1][:16]
        core = "_".join(parts)[:120]

    glb_name = f"{core}.glb"
    json_name = f"{core}.json"
    return Path(glb_name), Path(json_name)


# ---------------------------
# Public API
# ---------------------------


def run_worker(
    run_id: str,
    algo: str,
    limit: int,
    dry_run: bool,
    paths: PathResolver,
) -> Dict[str, int]:
    """
    Process up to `limit` tokens for the given run+algo.
    Implements an integrity check to ensure job tokens are not corrupt.
    Returns a dictionary with counts of processed, completed, and failed jobs.
    """
    paths.validate_expected_tree()
    queue_dir = paths.queue_dir(run_id)
    tokens = _select_tokens(queue_dir, run_id, algo)
    if not tokens:
        return {"processed": 0, "completed": 0, "failed": 0}

    if dry_run:
        return {"processed": min(limit, len(tokens)), "completed": 0, "failed": 0}

    worker_id = os.environ.get("ARCHI3D_WORKER_ID") or getpass.getuser()
    processed = 0
    completed_count = 0
    failed_count = 0

    repo_root = Path(__file__).resolve().parents[2]
    ADAPTERS_CFG = load_adapters_cfg(repo_root).get("adapters", {})
    workspace = paths.workspace_root

    for todo in tokens:
        if processed >= limit:
            break

        # Claim atomically
        try:
            inprog = _rename_atomic(todo, f"inprogress.{worker_id}")
        except FileNotFoundError:
            # Another worker grabbed it
            continue

        error_msg = ""
        status = "completed"
        output_rel = ""

        # Initialize variables for the error block
        job_id, product_id, variant, image_files, img_suffixes = "", "", "", [], ""

        try:
            token_json = json.loads(inprog.read_text(encoding="utf-8"))
            job_id: str = token_json["job_id"]
            product_id: str = token_json["product_id"]
            variant: str = token_json.get("variant", "")
            image_files: List[str] = list(token_json.get("image_files", []))
            
            # Recalculate img_suffixes from the image_files list
            img_suffixes = _img_suffixes_from_list(image_files)

            # --- JOB ID INTEGRITY CHECK ---
            image_csv = ",".join(image_files)
            # Re-compute the job ID using the exact same function and data
            expected_job_id = _compose_job_id(
                algo=token_json["algo"],
                product_id=product_id,
                variant=variant,
                image_csv=image_csv,
            )

            if job_id != expected_job_id:
                raise ValueError(
                    f"Job ID mismatch. Token has '{job_id}', but content computes to '{expected_job_id}'. "
                    "The token file may be corrupt or was manually edited."
                )
            # --- END OF CHECK ---

            job_id8 = job_id[:8]
            n_images = len(image_files)

            # Derive variant from dataset folder name of first image
            variant_slug = (
                _derive_variant_slug(product_id, image_files[0]) if image_files else ""
            )

            # Compose output names & paths
            glb_name, _ = _compose_output_names(
                run_id=run_id,
                algo=algo,
                product_id=product_id,
                variant_slug=variant_slug,
                n_images=n_images,
                img_suffixes=img_suffixes,
                job_id8=job_id8,
            )

            out_dir = paths.outputs_dir(run_id, algo=algo)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_glb = out_dir / glb_name

            # Logs directory
            logs_dir = paths.run_dir(run_id) / "logs" / algo
            logs_dir.mkdir(parents=True, exist_ok=True)

            # Adapter resolution
            AdapterCls = REGISTRY.get(algo)
            if AdapterCls is None:
                status = "failed"
                error_msg = f"unknown_adapter: No adapter registered for {algo}"
                raise RuntimeError(error_msg)

            # Build token object
            tok = Token(
                run_id=run_id,
                algo=algo,
                product_id=token_json["product_id"],
                variant=token_json.get("variant", ""),
                image_files=token_json["image_files"],
                img_suffixes=img_suffixes, # Pass the calculated suffixes
                job_id=token_json["job_id"],
            )

            # Merge adapter cfg for this algo key
            algo_cfg = ADAPTERS_CFG.get(algo, {})
            adapter = AdapterCls(cfg=algo_cfg, workspace=workspace, logs_dir=logs_dir)

            # Retry/backoff (10s,30s,60s); deadline 8 minutes per your policy
            delays = [10, 30, 60]
            attempt = 0
            start_time = time.time()

            while True:
                try:
                    exec_res = adapter.execute(tok, deadline_s=480)
                    # exec_res.glb_path currently holds the remote URL or a local path
                    if isinstance(exec_res.glb_path, str) and exec_res.glb_path.startswith("http"):
                        with requests.get(
                            exec_res.glb_path, stream=True, timeout=120
                        ) as r:
                            r.raise_for_status()
                            with out_glb.open("wb") as f:
                                for chunk in r.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                    else:
                        # already a file path (future adapters may return local paths)
                        Path(exec_res.glb_path).replace(out_glb)
                    status = "completed"
                    error_msg = ""
                    break
                except AdapterTransientError as e:
                    if attempt >= len(delays):
                        status = "failed"
                        error_msg = f"transient_exhausted: {e}"
                        break
                    time.sleep(delays[attempt])
                    attempt += 1
                    continue
                except AdapterPermanentError as e:
                    status = "failed"
                    error_msg = f"permanent: {e}"
                    break

            finished_time = time.time()
            duration = finished_time - start_time

            # Append to registry
            row = {
                "run_id": run_id,
                "job_id": tok.job_id,
                "product_id": tok.product_id,
                "variant": tok.variant,
                "algo": algo,
                "n_images": len(tok.image_files),
                "img_suffixes": tok.img_suffixes,
                "status": status,
                "started_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(start_time)
                ),
                "finished_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_time)
                ),
                "duration_s": duration,
                "output_glb_relpath": (
                    str(out_glb.relative_to(workspace))
                    if status == "completed"
                    else ""
                ),
                "worker": worker_id,
                "error_msg": error_msg,
                "lpips": None,  # keep placeholders per Step-1
                "fscore": None,
                # NEW:
                "unit_price_usd": float(algo_cfg.get("unit_price_usd", 0.0)),
                "estimated_cost_usd": float(
                    algo_cfg.get("unit_price_usd", 0.0)
                ),  # one call per job
                "price_source": str(algo_cfg.get("price_source", "unknown")),
            }
            _writerow_locked(paths, row)

            # Mark final state and report a concise line to the terminal
            _rename_atomic(inprog, status)
            if status == "completed":
                completed_count += 1
            else:
                failed_count += 1
                # Minimal operator feedback; keep it one line to avoid noise
                print(f"[ERROR] {tok.product_id}/{algo}/{tok.job_id} failed → {error_msg}")

        except Exception as e:
            logging.error(f"Worker failed on token {inprog.name}: {e!r}")
            try:
                finished_at = _now_iso()
                duration_s = round(time.perf_counter() - time.perf_counter(), 6)

                # Use variables captured before the exception if possible
                image_set = ",".join(image_files)
                n_images = len(image_files)

                status = "failed"
                error_msg = repr(e)

                row = ExecResult(
                    job_id=job_id,  # Will be from token or empty
                    run_id=run_id,
                    product_id=product_id,
                    variant=variant,
                    algo=algo,
                    image_set=image_set,
                    n_images=n_images,
                    img_suffixes=img_suffixes,
                    status=status,
                    started_at=_now_iso(),
                    finished_at=finished_at,
                    duration_s=duration_s,
                    output_glb_relpath=output_rel,
                    worker=worker_id,
                    error_msg=error_msg,
                    lpips=None,
                    fscore=None,
                    unit_price_usd=0.0,
                    estimated_cost_usd=0.0,
                    price_source="unknown",
                )
                _writerow_locked(paths, asdict(row))
            finally:
                # Mark failed
                try:
                    _rename_atomic(inprog, "failed")
                except Exception:
                    pass  # token might be gone (e.g., manual interference)

        processed += 1

    # One-line end-of-run summary
    print(f"Summary: completed={completed_count}  failed={failed_count}  processed={processed}")
    return {"processed": processed, "completed": completed_count, "failed": failed_count}
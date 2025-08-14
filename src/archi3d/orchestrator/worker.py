# archi3d/orchestrator/worker.py
from __future__ import annotations

import json
import os
import re
import time
import getpass
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
from filelock import FileLock

from archi3d import __version__
from archi3d.config.paths import PathResolver

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
            exc_info=True, # exc_info=True adds the traceback to the log
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


def _writerow_locked(paths: PathResolver, row: ExecResult) -> None:
    """
    Append (by read-append-write) under a filesystem lock for safety across machines.
    """
    lock_path = paths.results_parquet.with_suffix(".parquet.lock")
    with FileLock(str(lock_path)):
        # --- FIX STARTS HERE ---
        # Check if the results file exists and has content
        if paths.results_parquet.exists() and paths.results_parquet.stat().st_size > 0:
            df = pd.read_parquet(paths.results_parquet)
            # Create a DataFrame for the new row
            new_row_df = pd.DataFrame([asdict(row)])
            # Concatenate the existing DataFrame with the new row
            df = pd.concat([df, new_row_df], ignore_index=True)
        else:
            # If the file doesn't exist or is empty, create a new DataFrame from the first row
            df = pd.DataFrame([asdict(row)])
        # --- FIX ENDS HERE ---
        
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
) -> int:
    """
    Process up to `limit` tokens for the given run+algo.
    Implements Option 1 (create placeholder GLB) for this step.
    Returns the number of jobs processed (or would process in dry-run).
    """
    paths.validate_expected_tree()
    queue_dir = paths.queue_dir(run_id)
    tokens = _select_tokens(queue_dir, run_id, algo)
    if not tokens:
        return 0

    if dry_run:
        return min(limit, len(tokens))

    worker_id = os.environ.get("ARCHI3D_WORKER_ID") or getpass.getuser()
    processed = 0

    for todo in tokens:
        if processed >= limit:
            break

        # Claim atomically
        try:
            inprog = _rename_atomic(todo, f"inprogress.{worker_id}")
        except FileNotFoundError:
            # Another worker grabbed it
            continue

        started = time.perf_counter()
        started_at = _now_iso()
        error_msg = ""
        status = "completed"
        output_rel = ""

        try:
            token = json.loads(inprog.read_text(encoding="utf-8"))
            job_id: str = token["job_id"]
            job_id8 = job_id[:8]
            product_id: str = token["product_id"]
            variant: str = token.get("variant", "")
            image_files: List[str] = list(token.get("image_files", []))
            image_set = ",".join(image_files)
            n_images = len(image_files)

            # Derive variant from dataset folder name of first image
            variant_slug = _derive_variant_slug(product_id, image_files[0]) if image_files else ""
            img_suffixes = _img_suffixes_from_list(image_files)

            # Compose output names & paths
            outputs_dir = paths.outputs_dir(run_id, algo=algo)
            glb_name, json_name = _compose_output_names(
                run_id=run_id,
                algo=algo,
                product_id=product_id,
                variant_slug=variant_slug,
                n_images=n_images,
                img_suffixes=img_suffixes,
                job_id8=job_id8,
            )
            glb_path = outputs_dir / glb_name
            metrics_path = paths.metrics_dir(run_id) / json_name

            # ---- STUB EXECUTION: create placeholder GLB file ----
            glb_path.parent.mkdir(parents=True, exist_ok=True)
            glb_path.touch(exist_ok=True)
            output_rel = str(paths.rel_to_workspace(glb_path).as_posix())

            # Create metrics sidecar placeholder (nulls)
            metrics_payload = {
                "job_id": job_id,
                "lpips": None,
                "fscore": None,
                "computed_at": None,
                "algo": algo,
                "product_id": product_id,
                "image_suffixes": img_suffixes,
                "n_images": n_images,
                "run_id": run_id,
                "code_version": __version__,
            }
            metrics_path.write_text(json.dumps(metrics_payload, indent=2), encoding="utf-8")

            finished_at = _now_iso()
            duration_s = round(time.perf_counter() - started, 6)

            # Append to registry
            row = ExecResult(
                job_id=job_id,
                run_id=run_id,
                product_id=product_id,
                variant=variant,
                algo=algo,
                image_set=image_set,
                n_images=n_images,
                img_suffixes=img_suffixes,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                duration_s=duration_s,
                output_glb_relpath=output_rel,
                worker=worker_id,
                error_msg=error_msg,
            )
            _writerow_locked(paths, row)

            # Mark completed
            _rename_atomic(inprog, "completed")

        except Exception as e:  # noqa: BLE001
            try:
                finished_at = _now_iso()
                duration_s = round(time.perf_counter() - started, 6)
                # Best-effort read token to capture ids
                try:
                    token = json.loads(inprog.read_text(encoding="utf-8"))
                    job_id = token.get("job_id", "")
                    product_id = token.get("product_id", "")
                    variant = token.get("variant", "")
                    image_files = token.get("image_files", [])
                    image_set = ",".join(image_files) if image_files else ""
                    n_images = len(image_files) if image_files else 0
                    img_suffixes = _img_suffixes_from_list(image_files) if image_files else ""
                except Exception:
                    job_id = ""
                    product_id = ""
                    variant = ""
                    image_set = ""
                    n_images = 0
                    img_suffixes = ""

                status = "failed"
                error_msg = repr(e)

                row = ExecResult(
                    job_id=job_id,
                    run_id=run_id,
                    product_id=product_id,
                    variant=variant,
                    algo=algo,
                    image_set=image_set,
                    n_images=n_images,
                    img_suffixes=img_suffixes,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_s=duration_s,
                    output_glb_relpath=output_rel,
                    worker=worker_id,
                    error_msg=error_msg,
                )
                _writerow_locked(paths, row)
            finally:
                # Mark failed
                try:
                    _rename_atomic(inprog, "failed")
                except Exception:
                    pass  # token might be gone (e.g., manual interference)

        processed += 1

    return processed
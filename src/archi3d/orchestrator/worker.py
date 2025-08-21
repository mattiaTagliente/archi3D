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
from archi3d.orchestrator.batch import _compose_job_id
from archi3d.utils.io import read_json
from archi3d.utils.text import slugify


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


def _derive_variant_slug(product_id: str, first_image_rel: str) -> str:
    try:
        p = Path(first_image_rel)
        if len(p.parts) < 2:
            return ""
        folder_name = p.parts[1]
    except IndexError:
        return ""

    if " - " in folder_name:
        pid, var = folder_name.split(" - ", 1)
        if pid.strip() == product_id.strip():
            return slugify(var)
    return ""


def _img_suffixes_from_list(image_files: List[str], max_len: int = 20) -> str:
    letters: List[str] = []
    for s in image_files:
        m = _SUFFIX_RE.search(Path(s).name)
        if m:
            letters.append(m.group(1).upper())
    suffixes = "-".join(letters) if letters else ""
    return suffixes[:max_len]


def _select_tokens(queue_dir: Path, run_id: str, algo: str) -> List[Path]:
    readable = sorted(queue_dir.glob(f"*_{slugify(algo)}_N*_*_{slugify(run_id)}_h*.todo.json"))
    return readable


def _rename_atomic(p: Path, new_suffix: str) -> Path:
    """
    FIXED: Atomically renames a token file by correctly replacing its multi-part state suffix.
    e.g., '...<name>.todo.json' -> '...<name>.inprogress.json'
    """
    # Find the core name by stripping all known state suffixes
    name = p.name
    for suffix in [".todo.json", ".inprogress.json", ".completed.json", ".failed.json"]:
        if name.endswith(suffix):
            core_name = name[:-len(suffix)]
            break
    else:
        # Fallback for unexpected formats (like the old buggy ones)
        # This finds the first dot and takes everything before it.
        core_name = name.split('.', 1)[0]
    
    # Construct the new filename and rename
    target = p.with_name(f"{core_name}.{new_suffix}.json")
    p.rename(target)
    return target


def _write_result_staging(paths: PathResolver, row: dict) -> None:
    staging_dir = paths.results_staging_dir()
    job_id = row.get("job_id", "unknown_job")
    output_path = staging_dir / f"{job_id}.parquet"
    df = pd.DataFrame([row])
    df.to_parquet(output_path, index=False)


def _compose_output_names(
    run_id: str,
    algo: str,
    product_id: str,
    variant_slug: str, # This is already a slug
    n_images: int,
    img_suffixes: str,
    job_id8: str,) -> Tuple[Path, Path]:
    """
    Build human-readable but filesystem-safe filenames.
    """
    # Slug individual dynamic parts to ensure safety
    s_pid = slugify(product_id)
    s_algo = slugify(algo)
    s_suf = slugify(img_suffixes)
    s_run = slugify(run_id)

    # Assemble core name parts
    core = f"{s_pid}_{variant_slug}_" if variant_slug else f"{s_pid}__"
    core += f"{s_algo}_N{n_images}"
    if s_suf:
        core += f"_{s_suf}"
    core += f"_{s_run}_h{job_id8}"

    # CORRECTED: Restore the intelligent soft-cap logic
    # Enforce a soft cap on filename length for OS friendliness
    if len(core) > 120:
        # If too long, try to shrink the variant slug part first before a hard cut
        # This preserves the product id and the unique hash at the end
        if variant_slug:
            excess = len(core) - 120
            shorter_variant = variant_slug[: -excess - 1] # -1 for good measure
            core = f"{s_pid}_{shorter_variant}_" if shorter_variant else f"{s_pid}__"
            core += f"{s_algo}_N{n_images}"
            if s_suf:
                core += f"_{s_suf}"
            core += f"_{s_run}_h{job_id8}"
        # If it's still too long (or there was no variant), hard truncate
        core = core[:120]


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
    paths.validate_expected_tree()
    queue_dir = paths.queue_dir(run_id)
    tokens = _select_tokens(queue_dir, run_id, algo)
    if not tokens:
        return {"processed": 0, "completed": 0, "failed": 0}

    if dry_run:
        return {"processed": min(limit, len(tokens)), "completed": 0, "failed": 0}

    worker_id = os.environ.get("ARCHI3D_WORKER_ID") or getpass.getuser()
    processed, completed_count, failed_count = 0, 0, 0
    
    repo_root = Path(__file__).resolve().parents[3] # Adjusted for src structure
    ADAPTERS_CFG = load_adapters_cfg(repo_root).get("adapters", {})
    workspace = paths.workspace_root

    for todo in tokens:
        if processed >= limit:
            break

        try:
            inprog = _rename_atomic(todo, f"inprogress.{worker_id}")
        except FileNotFoundError:
            continue

        error_msg = ""
        status = "failed"
        output_rel = ""
        job_id, product_id, variant, image_files, img_suffixes = "", "", "", [], ""

        try:
            token_json = read_json(inprog)
            job_id = token_json["job_id"]
            product_id = token_json["product_id"]
            variant = token_json.get("variant", "")
            image_files = list(token_json.get("image_files", []))
            img_suffixes = _img_suffixes_from_list(image_files)

            # --- JOB ID INTEGRITY CHECK ---
            image_csv = ",".join(image_files)
            
            # Read the version used for hashing from the token, if it exists.
            job_id_version = token_json.get("job_id_version")

            # Re-compute the job ID using the exact same function and data
            expected_job_id = _compose_job_id(
                algo=token_json["algo"],
                product_id=product_id,
                variant=variant,
                image_csv=image_csv,
                version=job_id_version, # Pass the version here
            )

            if job_id != expected_job_id:
                raise ValueError(
                    f"Job ID mismatch. Token has '{job_id}', but content computes to '{expected_job_id}'. "
                    "The token file may be corrupt or was manually edited."
                )
            # --- END OF CHECK ---

            variant_slug = _derive_variant_slug(product_id, image_files[0]) if image_files else ""
            glb_name, _ = _compose_output_names(
                run_id, algo, product_id, variant_slug, len(image_files), img_suffixes, job_id[:8]
            )
            out_glb = paths.outputs_dir(run_id, algo=algo) / glb_name
            logs_dir = paths.run_dir(run_id) / "logs" / algo
            logs_dir.mkdir(parents=True, exist_ok=True)

            AdapterCls = REGISTRY.get(algo)
            if AdapterCls is None:
                raise RuntimeError(f"Unknown adapter: {algo}")

            tok = Token(
                run_id=run_id, algo=algo, product_id=product_id, variant=variant,
                image_files=image_files, img_suffixes=img_suffixes, job_id=job_id
            )
            
            algo_cfg = ADAPTERS_CFG.get(algo, {})
            adapter = AdapterCls(cfg=algo_cfg, workspace=workspace, logs_dir=logs_dir)

            delays = [10, 30, 60]
            start_time = time.time()
            for attempt in range(len(delays) + 1):
                try:
                    exec_res = adapter.execute(tok, deadline_s=480)
                    if isinstance(exec_res.glb_path, str) and exec_res.glb_path.startswith("http"):
                        with requests.get(exec_res.glb_path, stream=True, timeout=120) as r:
                            r.raise_for_status()
                            with out_glb.open("wb") as f:
                                for chunk in r.iter_content(chunk_size=8192):
                                    f.write(chunk)
                    else:
                        Path(exec_res.glb_path).replace(out_glb)
                    status = "completed"
                    error_msg = ""
                    break
                except AdapterTransientError as e:
                    if attempt >= len(delays):
                        error_msg = f"transient_exhausted: {e}"
                        break
                    time.sleep(delays[attempt])
                except AdapterPermanentError as e:
                    error_msg = f"permanent: {e}"
                    break
            
            duration = time.time() - start_time
            row = {
                "run_id": run_id, "job_id": job_id, "product_id": product_id, "variant": variant,
                "algo": algo, "n_images": len(image_files), "img_suffixes": img_suffixes, "status": status,
                "started_at": datetime.fromtimestamp(start_time, timezone.utc).isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "duration_s": duration,
                "output_glb_relpath": str(out_glb.relative_to(workspace)) if status == "completed" else "",
                "worker": worker_id, "error_msg": error_msg, "lpips": None, "fscore": None,
                "unit_price_usd": float(algo_cfg.get("unit_price_usd", 0.0)),
                "estimated_cost_usd": float(algo_cfg.get("unit_price_usd", 0.0)),
                "price_source": str(algo_cfg.get("price_source", "unknown")),
            }
            _write_result_staging(paths, row)
            
            if status == "completed":
                completed_count += 1
            else:
                failed_count += 1
                print(f"[ERROR] {product_id}/{algo} failed: {error_msg}")

        except Exception as e:
            logging.error(f"Worker crashed on {inprog.name}: {e!r}", exc_info=True)
            error_msg = repr(e)
            failed_count += 1
            # Write a failure record even if the worker crashes
            row = asdict(ExecResult(
                job_id=job_id, run_id=run_id, product_id=product_id, variant=variant, algo=algo,
                image_set=",".join(image_files), n_images=len(image_files), img_suffixes=img_suffixes,
                status="failed", started_at=_now_iso(), finished_at=_now_iso(), duration_s=0,
                output_glb_relpath="", worker=worker_id, error_msg=error_msg, lpips=None, fscore=None,
                unit_price_usd=0.0, estimated_cost_usd=0.0, price_source="unknown"
            ))
            _write_result_staging(paths, row)
        finally:
            _rename_atomic(inprog, status)
            processed += 1

    print(f"Summary: completed={completed_count} failed={failed_count} processed={processed}")
    return {"processed": processed, "completed": completed_count, "failed": failed_count}
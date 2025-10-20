# archi3d/orchestrator/worker.py
"""
Phase 3: Worker execution logic for running generation jobs.

This module implements the `archi3d run worker` command, which:
- Reads jobs from tables/generations.csv (SSOT)
- Filters by run_id, status, and optional filters
- Executes jobs via adapters with proper lifecycle management
- Updates generations.csv with execution metadata and outputs
- Creates state markers for resumability
- Supports concurrent execution with thread pools
- Provides dry-run mode for testing
"""
from __future__ import annotations

import getpass
import os
import socket
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from filelock import FileLock

from archi3d.config.adapters_cfg import load_adapters_cfg
from archi3d.config.paths import PathResolver
from archi3d.db.generations import upsert_generations
from archi3d.utils.io import append_log_record, write_text_atomic

# -------------------------
# Worker Environment Capture
# -------------------------


def _get_worker_identity() -> dict[str, str]:
    """
    Capture worker environment metadata for observability.

    Returns:
        Dict with keys: host, user, gpu, env, commit
    """
    host = socket.gethostname()
    user = getpass.getuser()

    # GPU detection (best effort)
    gpu = ""
    try:
        # Try nvidia-smi
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            gpu = result.stdout.strip().split("\n")[0]
    except Exception:
        # Try torch if available
        try:
            import torch

            if torch.cuda.is_available():
                gpu = torch.cuda.get_device_name(0)
        except Exception:
            pass

    # Environment string
    env = f"python {sys.version.split()[0]}"
    if "CONDA_DEFAULT_ENV" in os.environ:
        env += f" (conda:{os.environ['CONDA_DEFAULT_ENV']})"
    elif "VIRTUAL_ENV" in os.environ:
        venv_name = Path(os.environ["VIRTUAL_ENV"]).name
        env += f" (venv:{venv_name})"

    # Commit (optional)
    commit = os.environ.get("ARCHI3D_COMMIT", "")

    return {
        "worker_host": host,
        "worker_user": user,
        "worker_gpu": gpu,
        "worker_env": env,
        "worker_commit": commit,
    }


# -------------------------
# State Marker Management
# -------------------------


def _get_state_marker_path(state_dir: Path, job_id: str, status: str) -> Path:
    """Get path for state marker file."""
    return state_dir / f"{job_id}.{status}"


def _check_state_marker(state_dir: Path, job_id: str) -> str | None:
    """
    Check for existing state markers and return the status if found.

    Returns:
        Status string ("inprogress", "completed", "failed") or None if no marker.
    """
    for status in ["completed", "failed", "inprogress"]:
        marker = _get_state_marker_path(state_dir, job_id, status)
        if marker.exists():
            return status
    return None


def _create_state_marker(state_dir: Path, job_id: str, status: str, content: str = "") -> None:
    """Create a state marker file with optional content."""
    marker = _get_state_marker_path(state_dir, job_id, status)
    timestamp = datetime.now(UTC).isoformat()
    marker_content = f"timestamp: {timestamp}\npid: {os.getpid()}\n{content}"
    write_text_atomic(marker, marker_content)


def _transition_state_marker(
    state_dir: Path, job_id: str, old_status: str, new_status: str, content: str = ""
) -> None:
    """
    Atomically transition from old state to new state.

    Removes old marker and creates new marker.
    """
    old_marker = _get_state_marker_path(state_dir, job_id, old_status)
    if old_marker.exists():
        old_marker.unlink()

    _create_state_marker(state_dir, job_id, new_status, content)


def _is_stale_heartbeat(state_dir: Path, job_id: str, stale_seconds: int = 600) -> bool:
    """
    Check if an inprogress marker has a stale heartbeat.

    Args:
        state_dir: State directory path
        job_id: Job ID
        stale_seconds: Threshold for considering heartbeat stale (default 10 minutes)

    Returns:
        True if inprogress marker exists and is older than stale_seconds
    """
    marker = _get_state_marker_path(state_dir, job_id, "inprogress")
    if not marker.exists():
        return False

    age = time.time() - marker.stat().st_mtime
    return age > stale_seconds


# -------------------------
# Dry-Run Simulation
# -------------------------


def _simulate_dry_run(job_id: str, out_dir: Path) -> tuple[Path, list[Path]]:
    """
    Simulate a successful generation for dry-run mode.

    Creates minimal placeholder files for generated.glb and previews.

    Returns:
        (generated_glb_path, preview_paths)
    """
    # Create minimal GLB placeholder
    glb_path = out_dir / "generated.glb"
    glb_path.write_text("# Dry-run placeholder GLB\n", encoding="utf-8")

    # Create 2 preview placeholders
    previews = []
    for i in range(1, 3):
        preview_path = out_dir / f"preview_{i}.png"
        preview_path.write_text(f"# Dry-run placeholder preview {i}\n", encoding="utf-8")
        previews.append(preview_path)

    # Simulate processing time
    time.sleep(0.1)

    return glb_path, previews


# -------------------------
# Job Execution
# -------------------------


def _execute_job(
    job_row: pd.Series,
    paths: PathResolver,
    adapters_cfg: dict,
    worker_identity: dict,
    dry_run: bool,
) -> dict[str, Any]:
    """
    Execute a single generation job.

    IMPORTANT: This function does NOT write to generations.csv. It returns
    the complete upsert data, and the caller (run_worker) will do a batch
    upsert at the end. This avoids concurrent CSV write race conditions.

    State markers (.inprogress/.completed/.failed) provide the safety guarantee
    for concurrent workers - they prevent double-processing of the same job.

    Args:
        job_row: Row from manifest with job metadata
        paths: PathResolver instance
        adapters_cfg: Loaded adapters configuration
        worker_identity: Worker metadata dict
        dry_run: Whether to simulate execution

    Returns:
        Dict with either:
        - {"skipped": True, "reason": str} if job should be skipped
        - Complete upsert dict for generations.csv if job was executed
    """
    run_id = job_row["run_id"]
    job_id = job_row["job_id"]
    algo = job_row["algo"]

    # Build list of used image paths
    used_images = []
    for i in range(1, 7):
        img_col = f"used_image_{i}_path"
        if img_col in job_row and pd.notna(job_row[img_col]) and job_row[img_col]:
            used_images.append(job_row[img_col])

    # Get directories
    state_dir = paths.state_dir(run_id)
    out_dir = paths.outputs_dir(run_id, job_id=job_id)

    # Acquire job lock for state transitions
    lock_path = paths.state_lock_path(run_id, job_id)
    lock = FileLock(lock_path, timeout=30)

    # Prepare base record with all required fields
    start_time = datetime.now(UTC)

    with lock:
        # Check if job is already completed/failed
        existing_state = _check_state_marker(state_dir, job_id)
        if existing_state in ["completed", "failed"]:
            # Job already finished, skip
            return {"skipped": True, "reason": f"already_{existing_state}"}

        # Mark job as running (state marker provides concurrent worker safety)
        _create_state_marker(state_dir, job_id, "inprogress")

    # Execute generation (outside lock to allow concurrent execution)
    error_msg = ""
    status = "failed"
    gen_glb_path: Path | None = None
    previews: list[Path] = []
    algo_version = ""
    unit_price = 0.0
    estimated_cost = 0.0
    price_source = "unknown"

    try:
        if dry_run:
            # Dry-run simulation
            gen_glb_path, previews = _simulate_dry_run(job_id, out_dir)
            status = "completed"
            algo_version = "dry-run"
            price_source = "dry-run"
        else:
            # Real execution
            # Validate images exist
            for img_path in used_images:
                img_abs = paths.workspace_root / img_path
                if not img_abs.exists():
                    raise FileNotFoundError(f"Input image not found: {img_path}")

            # Get adapter configuration
            algo_cfg = adapters_cfg.get("adapters", {}).get(algo, {})
            unit_price = float(algo_cfg.get("unit_price_usd", 0.0))
            price_source = algo_cfg.get("price_source", "adapters.yaml")
            estimated_cost = unit_price

            # TODO: Implement real adapter execution
            # For now, create placeholder to allow testing
            gen_glb_path = out_dir / "generated.glb"
            gen_glb_path.write_text(
                "# Placeholder GLB (adapter not implemented)\n", encoding="utf-8"
            )
            status = "completed"
            algo_version = "placeholder"

        # Verify output exists and is non-empty
        if status == "completed":
            if (
                gen_glb_path is None
                or not gen_glb_path.exists()
                or gen_glb_path.stat().st_size == 0
            ):
                raise RuntimeError("Generated GLB is missing or empty")

    except Exception as e:
        error_msg = str(e)[:2000]  # Truncate to 2000 chars
        if len(str(e)) > 2000:
            error_msg += " (truncated; see error.txt)"

        # Write full error to error.txt
        error_file = state_dir / f"{job_id}.error.txt"
        error_content = f"Error: {e}\n\nTraceback:\n{traceback.format_exc()}"
        write_text_atomic(error_file, error_content)

    # Finalize (acquire lock for state transition)
    end_time = datetime.now(UTC)
    duration_s = (end_time - start_time).total_seconds()

    with lock:
        # Transition state marker
        _transition_state_marker(state_dir, job_id, "inprogress", status)

    # Prepare complete upsert data (will be written in batch by caller)
    upsert_data = {
        "run_id": run_id,
        "job_id": job_id,
        "status": status,
        "generation_start": start_time.isoformat(),
        "generation_end": end_time.isoformat(),
        "generation_duration_s": duration_s,
        "algo_version": algo_version,
        "unit_price_usd": unit_price,
        "estimated_cost_usd": estimated_cost,
        "price_source": price_source,
        "gen_object_path": "",  # Default to empty
        "preview_1_path": "",
        "preview_2_path": "",
        "preview_3_path": "",
        "error_msg": "",
        **worker_identity,
    }

    # Add output paths (workspace-relative)
    if status == "completed" and gen_glb_path:
        upsert_data["gen_object_path"] = paths.rel_to_workspace(gen_glb_path).as_posix()

        for i, preview_path in enumerate(previews[:3], start=1):
            col_name = f"preview_{i}_path"
            upsert_data[col_name] = paths.rel_to_workspace(preview_path).as_posix()

    if status == "failed":
        upsert_data["error_msg"] = error_msg

    # Return complete upsert data (caller will do batch upsert)
    return upsert_data


# -------------------------
# Main Worker Entry Point
# -------------------------


def run_worker(
    run_id: str,
    paths: PathResolver,
    jobs: str | None = None,
    only_status: str = "enqueued",
    max_parallel: int = 1,
    adapter: str | None = None,
    dry_run: bool = False,
    fail_fast: bool = False,
) -> dict[str, Any]:
    """
    Execute generation jobs for a given run.

    Args:
        run_id: Run identifier (required)
        paths: PathResolver instance
        jobs: Optional glob/regex filter on job_id
        only_status: Comma-separated list of statuses to process (default: "enqueued")
        max_parallel: Maximum number of concurrent workers (default: 1)
        adapter: Force specific adapter (debug mode)
        dry_run: Simulate execution without calling adapters
        fail_fast: Stop on first failure

    Returns:
        Dict with summary: processed, completed, failed, skipped
    """
    # Initialize
    worker_identity = _get_worker_identity()
    adapters_cfg = load_adapters_cfg()

    # Parse allowed statuses
    allowed_statuses = [s.strip() for s in only_status.split(",")]

    # Read generations.csv
    generations_csv = paths.generations_csv_path()
    if not generations_csv.exists():
        raise FileNotFoundError(f"Generations CSV not found: {generations_csv}")

    df_gen = pd.read_csv(
        generations_csv,
        encoding="utf-8-sig",
        dtype={"product_id": str, "variant": str, "run_id": str, "job_id": str},
    )

    # Filter by run_id
    df_run = df_gen[df_gen["run_id"] == run_id].copy()
    if df_run.empty:
        raise ValueError(f"No jobs found for run_id: {run_id}")

    # Apply non-status filters first (job_id pattern, adapter)
    df_pre_filtered = df_run.copy()

    # Filter by job_id pattern (if specified)
    if jobs:
        # Simple substring matching for now (could extend to regex)
        df_pre_filtered = df_pre_filtered[
            df_pre_filtered["job_id"].str.contains(jobs, case=False, na=False)
        ]

    # Filter by adapter (if specified)
    if adapter:
        df_pre_filtered = df_pre_filtered[df_pre_filtered["algo"] == adapter]

    # Count jobs that don't match status filter (already completed/failed)
    # These will be reported as "skipped" in the summary
    already_done_count = 0
    if (
        "enqueued" in allowed_statuses
        and "completed" not in allowed_statuses
        and "failed" not in allowed_statuses
    ):
        # When only processing enqueued jobs, count completed/failed as already done
        already_done_mask = df_pre_filtered["status"].isin(["completed", "failed"])
        already_done_count = int(already_done_mask.sum())

    # Filter by status
    df_filtered = df_pre_filtered[df_pre_filtered["status"].isin(allowed_statuses)].copy()

    # Read manifest for full job details
    manifest_path = paths.run_root(run_id) / "manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    df_manifest = pd.read_csv(
        manifest_path,
        encoding="utf-8-sig",
        dtype={"product_id": str, "variant": str, "job_id": str},
    )

    # Add run_id to manifest for merging (manifest doesn't include run_id)
    df_manifest["run_id"] = run_id

    # Merge filtered generations with manifest to get full job details
    df_jobs = df_filtered.merge(
        df_manifest, on=["run_id", "job_id"], how="inner", suffixes=("", "_manifest")
    )

    if df_jobs.empty:
        return {"processed": 0, "completed": 0, "failed": 0, "skipped": already_done_count}

    # Log worker start
    log_path = paths.worker_log_path()
    append_log_record(
        log_path,
        {
            "event": "worker_started",
            "run_id": run_id,
            "jobs_filter": jobs,
            "only_status": only_status,
            "max_parallel": max_parallel,
            "adapter": adapter,
            "dry_run": dry_run,
            "fail_fast": fail_fast,
            "total_jobs": len(df_jobs),
            **worker_identity,
        },
    )

    # Execute jobs
    processed = 0
    completed = 0
    failed = 0
    skipped = 0
    durations = []
    upsert_records = []  # Collect all upsert data for batch write

    # Use thread pool for concurrency
    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
        # Submit all jobs
        futures = {
            executor.submit(_execute_job, row, paths, adapters_cfg, worker_identity, dry_run): row
            for _, row in df_jobs.iterrows()
        }

        # Process results as they complete
        for future in as_completed(futures):
            row = futures[future]
            job_id = row["job_id"]

            try:
                result = future.result()

                if result.get("skipped"):
                    skipped += 1
                    continue

                # Collect upsert data for batch write
                upsert_records.append(result)

                processed += 1
                status = result["status"]
                duration_s = result["generation_duration_s"]
                durations.append(duration_s)

                if status == "completed":
                    completed += 1
                    append_log_record(
                        log_path,
                        {
                            "event": "job_completed",
                            "run_id": run_id,
                            "job_id": job_id,
                            "duration_s": duration_s,
                        },
                    )
                elif status == "failed":
                    failed += 1
                    append_log_record(
                        log_path,
                        {
                            "event": "job_failed",
                            "run_id": run_id,
                            "job_id": job_id,
                            "error": result.get("error_msg", ""),
                        },
                    )

                    if fail_fast:
                        # Cancel remaining futures
                        for f in futures:
                            f.cancel()
                        raise RuntimeError(f"Job {job_id} failed, stopping due to --fail-fast")

            except Exception as e:
                failed += 1
                append_log_record(
                    log_path,
                    {
                        "event": "job_crashed",
                        "run_id": run_id,
                        "job_id": job_id,
                        "error": str(e),
                    },
                )

                if fail_fast:
                    raise

    # Batch upsert all results to generations.csv (single atomic write)
    if upsert_records:
        upsert_df = pd.DataFrame(upsert_records)
        upsert_generations(
            paths.generations_csv_path(),
            upsert_df,
        )

    # Log summary
    avg_duration = sum(durations) / len(durations) if durations else 0.0

    # Add already-done jobs to skipped count
    total_skipped = skipped + already_done_count

    append_log_record(
        log_path,
        {
            "event": "worker_summary",
            "run_id": run_id,
            "processed": processed,
            "completed": completed,
            "failed": failed,
            "skipped": total_skipped,
            "avg_duration_s": avg_duration,
            "max_parallel": max_parallel,
            "dry_run": dry_run,
        },
    )

    return {
        "processed": processed,
        "completed": completed,
        "failed": failed,
        "skipped": total_skipped,
        "avg_duration_s": avg_duration,
    }

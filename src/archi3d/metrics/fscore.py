"""
Phase 5 — Compute FScore (Geometry Metrics)

Implements the `archi3d compute fscore` command to compute geometry
similarity metrics for eligible jobs and upsert results into the SSOT
`tables/generations.csv`.

Key responsibilities:
1. Select eligible jobs (completed status, GT present, etc.)
2. Invoke FScore evaluator via adapter for each job
3. Persist per-job artifacts (result.json)
4. Upsert standardized metric columns to SSOT CSV
5. Log structured summary to logs/metrics.log
6. Support dry-run, redo, concurrency, and timeouts
"""

import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from archi3d.config.loader import load_config
from archi3d.config.paths import PathResolver
from archi3d.metrics.fscore_adapter import FScoreRequest, FScoreResponse, evaluate_fscore
from archi3d.utils.io import append_log_record, update_csv_atomic

logger = logging.getLogger(__name__)


def _configure_fscore_logging():
    """Configure FScore module logger to output to console."""
    fscore_logger = logging.getLogger("fscore.evaluator")

    # Only configure if not already configured
    if not fscore_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter("  %(message)s")  # Indent for visual grouping
        handler.setFormatter(formatter)
        fscore_logger.addHandler(handler)
        fscore_logger.setLevel(logging.INFO)
        fscore_logger.propagate = False  # Don't propagate to root logger


def _job_matches_filter(job_id: str, filter_pattern: str) -> bool:
    """
    Check if job_id matches the filter pattern.

    Supports:
    - Substring matching (contains)
    - Simple glob patterns (* wildcard)
    - Regex patterns (if pattern starts with 're:')

    Args:
        job_id: Job ID to test
        filter_pattern: Filter pattern string

    Returns:
        True if job_id matches filter, False otherwise
    """
    if not filter_pattern:
        return True

    # Regex pattern
    if filter_pattern.startswith("re:"):
        pattern = filter_pattern[3:]
        try:
            return bool(re.search(pattern, job_id))
        except re.error:
            logger.warning(f"Invalid regex pattern: {pattern}")
            return False

    # Glob pattern
    if "*" in filter_pattern:
        pattern = filter_pattern.replace("*", ".*")
        return bool(re.match(pattern, job_id))

    # Substring matching
    return filter_pattern in job_id


def _is_eligible(
    row: pd.Series,
    run_id: str,
    only_status: list[str],
    with_gt_only: bool,
    redo: bool,
    jobs_filter: str | None,
    paths: PathResolver,
) -> tuple[bool, str]:
    """
    Determine if a row is eligible for FScore computation.

    Args:
        row: DataFrame row from generations.csv
        run_id: Target run ID
        only_status: Allowed job statuses
        with_gt_only: Require GT object path
        redo: Force recomputation even if already done
        jobs_filter: Optional job ID filter pattern
        paths: PathResolver for resolving paths

    Returns:
        Tuple of (is_eligible: bool, skip_reason: str)
    """
    # Check run_id
    if row.get("run_id") != run_id:
        return False, "wrong_run_id"

    # Check status
    if row.get("status") not in only_status:
        return False, f"status={row.get('status')}_not_in_filter"

    # Check job_id filter
    job_id = row.get("job_id", "")
    if jobs_filter and not _job_matches_filter(job_id, jobs_filter):
        return False, "job_id_not_matching_filter"

    # Check if already done (unless redo)
    if not redo:
        fscore_status = row.get("fscore_status")
        if fscore_status == "ok" or pd.notna(row.get("fscore")):
            return False, "already_computed"

    # Check generated object exists
    gen_path_str = row.get("gen_object_path", "")
    if not gen_path_str or pd.isna(gen_path_str):
        return False, "missing_gen_object_path"

    # Resolve to absolute path and check existence
    gen_path = paths.workspace_root / gen_path_str
    if not gen_path.exists():
        return False, "gen_object_not_found_on_disk"

    # Check GT object if required
    if with_gt_only:
        gt_path_str = row.get("gt_object_path", "")
        if not gt_path_str or pd.isna(gt_path_str):
            return False, "missing_gt_object_path"

        # Resolve to absolute path and check existence
        gt_path = paths.workspace_root / gt_path_str
        if not gt_path.exists():
            return False, "gt_object_not_found_on_disk"

    return True, ""


def _process_job(
    row: pd.Series,
    n_points: int,
    timeout_s: int | None,
    paths: PathResolver,
    dry_run: bool,
) -> dict[str, Any]:
    """
    Process a single job: invoke FScore evaluator and prepare upsert data.

    Args:
        row: Job row from generations.csv
        n_points: Number of points for Poisson sampling
        timeout_s: Per-job timeout in seconds
        paths: PathResolver for path resolution
        dry_run: If True, skip actual evaluation

    Returns:
        Dict with result data for upserting to CSV:
        {
            "run_id": str,
            "job_id": str,
            "fscore_status": "ok"|"error"|"skipped",
            "fscore_error": str | None,
            "fscore": float | None,
            ... (all FScore metric columns)
        }
    """
    job_id = row["job_id"]
    run_id = row["run_id"]

    # Prepare result dict with key columns
    result = {
        "run_id": run_id,
        "job_id": job_id,
        "fscore_status": "error",
        "fscore_error": None,
        "fscore": None,
        "precision": None,
        "recall": None,
        "chamfer_l2": None,
        "fscore_n_points": n_points,
        "fscore_scale": None,
        "fscore_rot_w": None,
        "fscore_rot_x": None,
        "fscore_rot_y": None,
        "fscore_rot_z": None,
        "fscore_tx": None,
        "fscore_ty": None,
        "fscore_tz": None,
        "fscore_dist_mean": None,
        "fscore_dist_median": None,
        "fscore_dist_p95": None,
        "fscore_dist_p99": None,
        "fscore_dist_max": None,
        "fscore_runtime_s": None,
        "fscore_tool_version": None,
        "fscore_config_hash": None,
    }

    # Resolve paths
    gt_path = paths.workspace_root / row["gt_object_path"]
    gen_path = paths.workspace_root / row["gen_object_path"]
    out_dir = paths.runs_root / run_id / "metrics" / "fscore" / job_id

    # Dry-run: skip evaluation
    if dry_run:
        result["fscore_status"] = "skipped"
        result["fscore_error"] = "dry_run"
        return result

    # Check if result.json already exists (disk-based cache)
    result_json_path = out_dir / "result.json"
    if result_json_path.exists():
        # Load cached result instead of recomputing
        try:
            logger.info(f"{job_id}: Loading cached FScore result from disk")
            with open(result_json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            # Populate result dict from cached payload
            result["fscore_status"] = "ok"
            result["fscore"] = payload.get("fscore")
            result["precision"] = payload.get("precision")
            result["recall"] = payload.get("recall")
            result["chamfer_l2"] = payload.get("chamfer_l2")
            result["fscore_n_points"] = payload.get("n_points", n_points)

            # Alignment
            alignment = payload.get("alignment", {})
            result["fscore_scale"] = alignment.get("scale")
            rotation = alignment.get("rotation_quat", {})
            result["fscore_rot_w"] = rotation.get("w")
            result["fscore_rot_x"] = rotation.get("x")
            result["fscore_rot_y"] = rotation.get("y")
            result["fscore_rot_z"] = rotation.get("z")
            translation = alignment.get("translation", {})
            result["fscore_tx"] = translation.get("x")
            result["fscore_ty"] = translation.get("y")
            result["fscore_tz"] = translation.get("z")

            # Distance stats
            dist_stats = payload.get("dist_stats", {})
            result["fscore_dist_mean"] = dist_stats.get("mean")
            result["fscore_dist_median"] = dist_stats.get("median")
            result["fscore_dist_p95"] = dist_stats.get("p95")
            result["fscore_dist_p99"] = dist_stats.get("p99")
            result["fscore_dist_max"] = dist_stats.get("max")

            # Runtime and version info from cached payload
            result["fscore_runtime_s"] = payload.get("timing", {}).get("t_total_s", 0.0)
            result["fscore_tool_version"] = payload.get("tool_version")
            result["fscore_config_hash"] = payload.get("config_hash")

            return result

        except Exception as e:
            # Cache load failed - fall through to recompute
            logger.warning(f"{job_id}: Failed to load cached result, recomputing: {e}")

    try:
        # Invoke FScore evaluator
        req = FScoreRequest(
            gt_path=gt_path,
            cand_path=gen_path,
            n_points=n_points,
            out_dir=out_dir,
            timeout_s=timeout_s,
        )
        response: FScoreResponse = evaluate_fscore(req)

        if not response.ok:
            # Evaluation failed
            result["fscore_status"] = "error"
            result["fscore_error"] = response.error[:200] if response.error else "FScore error (unknown)"
            return result

        # Success: extract payload
        payload = response.payload

        # Write result.json (ensure directory exists)
        result_json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(result_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        # Write detailed alignment log for debugging
        if "alignment_log" in payload and "timing" in payload:
            log_path = out_dir / "alignment_log.txt"
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"FScore Evaluation Log - {job_id}\n")
                f.write("=" * 60 + "\n\n")

                # Timing breakdown
                timing = payload["timing"]
                f.write("Timing Breakdown:\n")
                f.write(f"  Mesh loading:     {timing.get('t_load_s', 0):.2f}s\n")
                f.write(f"  Pre-alignment:    {timing.get('t_prealign_s', 0):.2f}s\n")
                f.write(f"  ICP refinement:   {timing.get('t_icp_s', 0):.2f}s\n")
                f.write(f"  FScore compute:   {timing.get('t_fscore_s', 0):.2f}s\n")
                f.write(f"  Total:            {timing.get('t_total_s', 0):.2f}s\n\n")

                # Alignment details
                align_log = payload["alignment_log"]
                f.write("Alignment Details:\n")
                f.write(f"  Method:           {align_log.get('prealign_method', 'N/A')}\n")
                f.write(f"  Scale factor:     {align_log.get('scale_applied', 'N/A'):.4f}\n")
                if align_log.get("ransac_fitness") is not None:
                    f.write(f"  RANSAC fitness:   {align_log['ransac_fitness']:.4f}\n")
                if align_log.get("pca_best_fitness") is not None:
                    f.write(f"  PCA fitness:      {align_log['pca_best_fitness']:.4f}\n")
                f.write(f"  ICP fitness:      {align_log.get('icp_fitness', 'N/A'):.4f}\n")
                f.write(f"  ICP RMSE:         {align_log.get('icp_inlier_rmse', 'N/A'):.4f}\n\n")

                # Mesh metadata
                mesh_meta = payload.get("mesh_meta", {})
                f.write("Mesh Metadata:\n")
                f.write(f"  GT vertices:      {mesh_meta.get('gt_vertices', 'N/A')}\n")
                f.write(f"  GT triangles:     {mesh_meta.get('gt_triangles', 'N/A')}\n")
                f.write(f"  Pred vertices:    {mesh_meta.get('pred_vertices', 'N/A')}\n")
                f.write(f"  Pred triangles:   {mesh_meta.get('pred_triangles', 'N/A')}\n\n")

                # Final metrics
                f.write("Metrics:\n")
                f.write(f"  F-score:          {payload.get('fscore', 'N/A'):.4f}\n")
                f.write(f"  Precision:        {payload.get('precision', 'N/A'):.4f}\n")
                f.write(f"  Recall:           {payload.get('recall', 'N/A'):.4f}\n")
                f.write(f"  Chamfer L2:       {payload.get('chamfer_l2', 'N/A'):.6f}\n")

        # Log visualization path if present
        if response.visualization_path:
            logger.info(f"  Saved comparison visualization: {response.visualization_path}")

        # Populate result dict with metrics
        result["fscore_status"] = "ok"
        result["fscore"] = payload.get("fscore")
        result["precision"] = payload.get("precision")
        result["recall"] = payload.get("recall")
        result["chamfer_l2"] = payload.get("chamfer_l2")
        result["fscore_n_points"] = payload.get("n_points", n_points)

        # Alignment
        alignment = payload.get("alignment", {})
        result["fscore_scale"] = alignment.get("scale")
        rotation = alignment.get("rotation_quat", {})
        result["fscore_rot_w"] = rotation.get("w")
        result["fscore_rot_x"] = rotation.get("x")
        result["fscore_rot_y"] = rotation.get("y")
        result["fscore_rot_z"] = rotation.get("z")
        translation = alignment.get("translation", {})
        result["fscore_tx"] = translation.get("x")
        result["fscore_ty"] = translation.get("y")
        result["fscore_tz"] = translation.get("z")

        # Distance stats
        dist_stats = payload.get("dist_stats", {})
        result["fscore_dist_mean"] = dist_stats.get("mean")
        result["fscore_dist_median"] = dist_stats.get("median")
        result["fscore_dist_p95"] = dist_stats.get("p95")
        result["fscore_dist_p99"] = dist_stats.get("p99")
        result["fscore_dist_max"] = dist_stats.get("max")

        # Runtime and version info
        result["fscore_runtime_s"] = response.runtime_s
        result["fscore_tool_version"] = response.tool_version
        result["fscore_config_hash"] = response.config_hash

        return result

    except Exception as e:
        # Unexpected error
        result["fscore_status"] = "error"
        result["fscore_error"] = f"Unexpected: {str(e)[:180]}"
        logger.exception(f"Unexpected error processing job {job_id}")
        return result


def compute_fscore(
    run_id: str,
    jobs: str | None = None,
    only_status: str = "completed",
    with_gt_only: bool = True,
    redo: bool = False,
    n_points: int = 100000,
    timeout_s: int | None = None,
    max_parallel: int = 1,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Compute FScore metrics for eligible jobs in a run.

    Args:
        run_id: Run ID to process
        jobs: Optional job ID filter (glob/regex/substring)
        only_status: Comma-separated list of statuses to process (default: "completed")
        with_gt_only: Require GT object path (default: True)
        redo: Force recomputation even if already done (default: False)
        n_points: Number of Poisson disk samples per mesh (default: 100000)
        timeout_s: Optional per-job timeout in seconds
        max_parallel: Maximum parallel jobs (default: 1)
        limit: Optional limit on number of jobs to process (default: None, process all)
        dry_run: Preview selection without running evaluator (default: False)

    Returns:
        Summary dict with:
        {
            "n_selected": int,
            "processed": int,
            "ok": int,
            "error": int,
            "skipped": int,
            "avg_runtime_s": float,
            ...
        }

    Raises:
        RuntimeError: If FScore is not installed
    """
    # Early check: Verify FScore is available (always, even in dry-run)
    try:
        import fscore  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "FScore not installed. See quickstart.md for installation instructions."
        ) from e

    # Load config and paths
    cfg = load_config()
    paths = PathResolver(cfg)

    # Configure FScore logging to output to console
    _configure_fscore_logging()

    # Parse status filter
    status_list = [s.strip() for s in only_status.split(",") if s.strip()]
    if not status_list:
        status_list = ["completed"]

    # Load generations.csv
    gen_csv_path = paths.generations_csv_path()
    if not gen_csv_path.exists():
        logger.warning(f"Generations CSV not found: {gen_csv_path}")
        return {
            "n_selected": 0,
            "processed": 0,
            "ok": 0,
            "error": 0,
            "skipped": 0,
            "avg_runtime_s": 0.0,
        }

    df = pd.read_csv(
        gen_csv_path,
        dtype={"product_id": str, "variant": str, "run_id": str, "job_id": str},
    )

    # Select eligible rows
    eligible_rows = []
    skip_reasons: dict[str, int] = {}

    for _, row in df.iterrows():
        is_eligible, reason = _is_eligible(
            row=row,
            run_id=run_id,
            only_status=status_list,
            with_gt_only=with_gt_only,
            redo=redo,
            jobs_filter=jobs,
            paths=paths,
        )

        if is_eligible:
            eligible_rows.append(row)
        else:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    # Apply limit if specified
    if limit is not None and limit > 0 and len(eligible_rows) > limit:
        eligible_rows = eligible_rows[:limit]
        logger.info(f"Applied limit: processing first {limit} of {len(eligible_rows) + len(skip_reasons)} jobs")

    n_selected = len(eligible_rows)

    logger.info(f"Selected {n_selected} eligible jobs for FScore computation")
    if skip_reasons:
        logger.info(f"Skip reasons: {skip_reasons}")

    # Early exit if no jobs selected
    if n_selected == 0:
        summary = {
            "event": "compute_fscore",
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "n_selected": 0,
            "processed": 0,
            "ok": 0,
            "error": 0,
            "skipped": 0,
            "avg_runtime_s": 0.0,
            "n_points": n_points,
            "redo": redo,
            "max_parallel": max_parallel,
            "dry_run": dry_run,
            "skip_reasons": skip_reasons,
        }
        append_log_record(paths.metrics_log_path(), summary)
        return summary

    # Process jobs (with optional parallelism)
    results: list[dict[str, Any]] = []
    total_runtime = 0.0
    counters = {"ok": 0, "error": 0, "skipped": 0}

    if max_parallel == 1:
        # Sequential processing
        for row in eligible_rows:
            result = _process_job(
                row=row,
                n_points=n_points,
                timeout_s=timeout_s,
                paths=paths,
                dry_run=dry_run,
            )
            results.append(result)

            # Update counters
            status = result["fscore_status"]
            counters[status] = counters.get(status, 0) + 1
            if result["fscore_runtime_s"]:
                total_runtime += result["fscore_runtime_s"]

    else:
        # Parallel processing
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            futures = {
                executor.submit(
                    _process_job,
                    row=row,
                    n_points=n_points,
                    timeout_s=timeout_s,
                    paths=paths,
                    dry_run=dry_run,
                ): row
                for row in eligible_rows
            }

            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)

                    # Update counters
                    status = result["fscore_status"]
                    counters[status] = counters.get(status, 0) + 1
                    if result["fscore_runtime_s"]:
                        total_runtime += result["fscore_runtime_s"]

                except Exception as e:
                    row = futures[future]
                    logger.exception(f"Failed to process job {row['job_id']}: {e}")
                    # Create error result
                    error_result = {
                        "run_id": run_id,
                        "job_id": row["job_id"],
                        "fscore_status": "error",
                        "fscore_error": f"Processing failed: {str(e)[:180]}",
                    }
                    results.append(error_result)
                    counters["error"] = counters.get("error", 0) + 1

    # Upsert results to CSV (skip if dry-run)
    if not dry_run and results:
        df_results = pd.DataFrame(results)
        update_csv_atomic(
            gen_csv_path,
            df_results,
            key_cols=["run_id", "job_id"],
        )

    # Calculate summary stats
    avg_runtime = total_runtime / counters["ok"] if counters["ok"] > 0 else 0.0

    summary = {
        "event": "compute_fscore",
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "n_selected": n_selected,
        "processed": len(results),
        "ok": counters.get("ok", 0),
        "error": counters.get("error", 0),
        "skipped": counters.get("skipped", 0),
        "avg_runtime_s": round(avg_runtime, 2),
        "n_points": n_points,
        "redo": redo,
        "max_parallel": max_parallel,
        "dry_run": dry_run,
        "skip_reasons": skip_reasons,
    }

    # Log summary
    append_log_record(paths.metrics_log_path(), summary)

    return summary

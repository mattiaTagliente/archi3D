# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

"""
Phase 6 — Compute VFScore (Visual Fidelity Metrics)

Implements the `archi3d compute vfscore` command to compute visual
fidelity metrics for eligible jobs and upsert results into the SSOT
`tables/generations.csv`.

Key responsibilities:
1. Select eligible jobs (completed status, generated object present, etc.)
2. Invoke VFScore evaluator via adapter for each job
3. Persist per-job artifacts (result.json, renders, rationales, config.json)
4. Upsert standardized metric columns to SSOT CSV
5. Log structured summary to logs/metrics.log
6. Support dry-run, redo, concurrency, and timeouts
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from archi3d.config.loader import load_config, get_tool_path
from archi3d.config.paths import PathResolver
from archi3d.metrics.vfscore_adapter import VFScoreRequest, VFScoreResponse, evaluate_vfscore
from archi3d.utils.io import append_log_record, update_csv_atomic

logger = logging.getLogger(__name__)


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


def _get_reference_images(
    row: pd.Series,
    use_images_from: str,
    paths: PathResolver,
) -> list[Path]:
    """
    Extract reference image paths from a row.

    Args:
        row: DataFrame row from generations.csv
        use_images_from: "used" or "source" - which image set to use
        paths: PathResolver for path resolution

    Returns:
        List of absolute paths to existing reference images
    """
    ref_images: list[Path] = []

    # Determine column prefix
    if use_images_from == "used":
        prefix = "used_image_"
    else:  # source
        prefix = "source_image_"

    # Collect image paths (typically 6 columns: numbered 1-6 with _path suffix)
    for num in range(1, 7):
        col_name = f"{prefix}{num}_path"
        if col_name in row and pd.notna(row[col_name]):
            img_rel_path = row[col_name]
            img_abs_path = paths.workspace_root / img_rel_path
            if img_abs_path.exists():
                ref_images.append(img_abs_path)

    return ref_images


def _is_eligible(
    row: pd.Series,
    run_id: str,
    only_status: list[str],
    use_images_from: str,
    redo: bool,
    jobs_filter: str | None,
    paths: PathResolver,
) -> tuple[bool, str]:
    """
    Determine if a row is eligible for VFScore computation.

    Args:
        row: DataFrame row from generations.csv
        run_id: Target run ID
        only_status: Allowed job statuses
        use_images_from: "used" or "source" - which image set to use
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
        vf_status = row.get("vf_status")
        if vf_status == "ok" or pd.notna(row.get("vfscore_overall")):
            return False, "already_computed"

    # Check generated object exists
    gen_path_str = row.get("gen_object_path", "")
    if not gen_path_str or pd.isna(gen_path_str):
        return False, "missing_gen_object_path"

    # Resolve to absolute path and check existence
    gen_path = paths.workspace_root / gen_path_str
    if not gen_path.exists():
        return False, "gen_object_not_found_on_disk"

    # Check reference images
    ref_images = _get_reference_images(row, use_images_from, paths)
    if not ref_images:
        return False, "no_reference_images_found"

    return True, ""


def _process_job(
    row: pd.Series,
    repeats: int,
    use_images_from: str,
    timeout_s: int | None,
    paths: PathResolver,
    dry_run: bool,
    blender_exe: Path,
) -> dict[str, Any]:
    """
    Process a single job: invoke VFScore evaluator and prepare upsert data.

    Args:
        row: Job row from generations.csv
        repeats: Number of LLM scoring repeats
        use_images_from: "used" or "source" - which image set to use
        timeout_s: Per-job timeout in seconds
        paths: PathResolver for path resolution
        dry_run: If True, skip actual evaluation
        blender_exe: Path to Blender executable (from config)

    Returns:
        Dict with result data for upserting to CSV:
        {
            "run_id": str,
            "job_id": str,
            "vf_status": "ok"|"error"|"skipped",
            "vf_error": str | None,
            "vfscore_overall": int | None,
            ... (all VFScore metric columns)
        }
    """
    job_id = row["job_id"]
    run_id = row["run_id"]

    # Prepare result dict with key columns (comprehensive objective2 schema)
    result = {
        # Key columns
        "run_id": run_id,
        "job_id": job_id,

        # Status
        "vf_status": "error",
        "vf_error": None,

        # Core metrics
        "vfscore_overall": None,
        "vf_lpips_distance": None,
        "vf_lpips_model": None,
        "vf_iou": None,
        "vf_mask_error": None,
        "vf_pose_confidence": None,

        # Score combination parameters
        "vf_gamma": None,
        "vf_pose_compensation_c": None,

        # Final pose parameters
        "vf_azimuth_deg": None,
        "vf_elevation_deg": None,
        "vf_radius": None,
        "vf_fov_deg": None,
        "vf_obj_yaw_deg": None,

        # Pipeline statistics
        "vf_pipeline_mode": None,
        "vf_num_step2_candidates": None,
        "vf_num_step4_candidates": None,
        "vf_num_selected_candidates": None,
        "vf_best_lpips_idx": None,

        # Performance & provenance
        "vf_render_runtime_s": None,
        "vf_scoring_runtime_s": None,
        "vf_tool_version": None,
        "vf_config_hash": None,

        # Artifact paths
        "vf_artifacts_dir": None,
        "vf_gt_image_path": None,
        "vf_render_image_path": None,

        # DEPRECATED fields (kept for backward compatibility)
        "vf_finish": None,
        "vf_texture_identity": None,
        "vf_texture_scale_placement": None,
        "vf_repeats_n": repeats,
        "vf_iqr": None,
        "vf_std": None,
        "vf_llm_model": None,
        "vf_rubric_json": None,
        "vf_rationales_dir": None,
    }

    # Resolve paths
    gen_path = paths.workspace_root / row["gen_object_path"]
    ref_images = _get_reference_images(row, use_images_from, paths)
    out_dir = paths.runs_root / run_id / "metrics" / "vfscore" / job_id

    # Dry-run: skip evaluation
    if dry_run:
        result["vf_status"] = "skipped"
        result["vf_error"] = "dry_run"
        return result

    # Check if result.json already exists (disk-based cache)
    result_json_path = out_dir / "result.json"
    if result_json_path.exists():
        # Load cached result instead of recomputing
        try:
            logger.info(f"{job_id}: Loading cached VFScore result from disk")
            with open(result_json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            # Populate result dict from cached payload (objective2 schema)
            result["vf_status"] = "ok"
            result["vfscore_overall"] = payload.get("vfscore_overall")
            result["vf_lpips_distance"] = payload.get("lpips_distance")
            result["vf_lpips_model"] = payload.get("lpips_model")
            result["vf_iou"] = payload.get("iou")
            result["vf_mask_error"] = payload.get("mask_error")
            result["vf_pose_confidence"] = payload.get("pose_confidence")
            result["vf_gamma"] = payload.get("gamma")
            result["vf_pose_compensation_c"] = payload.get("pose_compensation_c")

            # Final pose
            final_pose = payload.get("final_pose", {})
            result["vf_azimuth_deg"] = final_pose.get("azimuth_deg")
            result["vf_elevation_deg"] = final_pose.get("elevation_deg")
            result["vf_radius"] = final_pose.get("radius")
            result["vf_fov_deg"] = final_pose.get("fov_deg")
            result["vf_obj_yaw_deg"] = final_pose.get("obj_yaw_deg")

            # Pipeline stats
            result["vf_pipeline_mode"] = payload.get("pipeline_mode")
            result["vf_num_step2_candidates"] = payload.get("num_step2_candidates")
            result["vf_num_step4_candidates"] = payload.get("num_step4_candidates")
            result["vf_num_selected_candidates"] = payload.get("num_selected_candidates")
            result["vf_best_lpips_idx"] = payload.get("best_lpips_idx")

            # Performance & provenance
            result["vf_render_runtime_s"] = payload.get("render_runtime_s")
            result["vf_scoring_runtime_s"] = payload.get("scoring_runtime_s")
            result["vf_tool_version"] = payload.get("tool_version")
            result["vf_config_hash"] = payload.get("config_hash")

            # Artifact paths (workspace-relative)
            result["vf_artifacts_dir"] = payload.get("artifacts_dir")
            result["vf_gt_image_path"] = payload.get("gt_image_path")
            result["vf_render_image_path"] = payload.get("render_image_path")

            return result

        except Exception as e:
            # Cache load failed - fall through to recompute
            logger.warning(f"{job_id}: Failed to load cached result, recomputing: {e}")

    try:
        # Invoke VFScore evaluator
        req = VFScoreRequest(
            cand_glb=gen_path,
            ref_images=ref_images,
            out_dir=out_dir,
            repeats=repeats,
            timeout_s=timeout_s,
            workspace=paths.workspace_root,
            blender_exe=blender_exe,
            algo=row.get("algo", None),  # Pass algorithm for artifact naming
        )
        response: VFScoreResponse = evaluate_vfscore(req)

        if not response.ok:
            # Evaluation failed
            result["vf_status"] = "error"
            result["vf_error"] = response.error if response.error else "VFScore error (unknown)"
            # Log full error for debugging
            import sys
            print(f"\n=== VFScore evaluation failed for job {job_id} ===", file=sys.stderr)
            print(response.error, file=sys.stderr)
            print("=" * 80, file=sys.stderr)
            return result

        # Success: extract payload
        payload = response.payload

        # Write result.json (ensure directory exists)
        result_json_path = out_dir / "result.json"
        result_json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(result_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        # Write config.json (render settings and rubric)
        config_data = {
            "render_settings": payload.get("render_settings", {}),
            "rubric_weights": payload.get("rubric_weights", {}),
            "llm_model": payload.get("llm_model"),
            "repeats": repeats,
        }
        config_json_path = out_dir / "config.json"
        with open(config_json_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        # Populate result dict with comprehensive objective2 metrics
        result["vf_status"] = "ok"

        # Core metrics
        result["vfscore_overall"] = payload.get("vfscore_overall_median")
        result["vf_lpips_distance"] = payload.get("lpips_distance")
        result["vf_lpips_model"] = payload.get("lpips_model")
        result["vf_iou"] = payload.get("iou")
        result["vf_mask_error"] = payload.get("mask_error")
        result["vf_pose_confidence"] = payload.get("pose_confidence")

        # Score combination parameters
        result["vf_gamma"] = payload.get("gamma")
        result["vf_pose_compensation_c"] = payload.get("pose_compensation_c")

        # Final pose parameters
        result["vf_azimuth_deg"] = payload.get("azimuth_deg")
        result["vf_elevation_deg"] = payload.get("elevation_deg")
        result["vf_radius"] = payload.get("radius")
        result["vf_fov_deg"] = payload.get("fov_deg")
        result["vf_obj_yaw_deg"] = payload.get("obj_yaw_deg")

        # Pipeline statistics
        result["vf_pipeline_mode"] = payload.get("pipeline_mode")
        result["vf_num_step2_candidates"] = payload.get("num_step2_candidates")
        result["vf_num_step4_candidates"] = payload.get("num_step4_candidates")
        result["vf_num_selected_candidates"] = payload.get("num_selected_candidates")
        result["vf_best_lpips_idx"] = payload.get("best_lpips_idx")

        # Performance & provenance
        result["vf_render_runtime_s"] = response.render_runtime_s
        result["vf_scoring_runtime_s"] = response.scoring_runtime_s
        result["vf_tool_version"] = response.tool_version
        result["vf_config_hash"] = response.config_hash

        # Artifact paths (workspace-relative)
        result["vf_artifacts_dir"] = payload.get("artifacts_dir")
        result["vf_gt_image_path"] = payload.get("gt_image_path")
        result["vf_render_image_path"] = payload.get("render_image_path")

        # DEPRECATED fields (kept for backward compatibility)
        subscores = payload.get("vf_subscores_median", {})
        result["vf_finish"] = subscores.get("finish")
        result["vf_texture_identity"] = subscores.get("texture_identity")
        result["vf_texture_scale_placement"] = subscores.get("texture_scale_placement")

        result["vf_repeats_n"] = payload.get("repeats_n", repeats)
        result["vf_iqr"] = payload.get("iqr")
        result["vf_std"] = payload.get("std")
        result["vf_llm_model"] = payload.get("llm_model")

        # Rubric weights as compact JSON (deprecated)
        rubric = payload.get("rubric_weights", {})
        if rubric:
            result["vf_rubric_json"] = json.dumps(rubric, separators=(",", ":"))

        # Rationales directory (relative to workspace)
        rationales_dir = out_dir / "rationales"
        if rationales_dir.exists():
            result["vf_rationales_dir"] = str(paths.rel_to_workspace(rationales_dir).as_posix())

        return result

    except Exception as e:
        # Unexpected error
        result["vf_status"] = "error"
        result["vf_error"] = f"Unexpected: {str(e)[:180]}"
        logger.exception(f"Unexpected error processing job {job_id}")
        return result


def compute_vfscore(
    run_id: str,
    jobs: str | None = None,
    only_status: str = "completed",
    use_images_from: str = "used",
    repeats: int = 1,
    redo: bool = False,
    max_parallel: int = 1,
    timeout_s: int | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Compute VFScore metrics for eligible jobs in a run.

    Args:
        run_id: Run ID to process
        jobs: Optional job ID filter (glob/regex/substring)
        only_status: Comma-separated list of statuses to process (default: "completed")
        use_images_from: "used" or "source" - which image set to use as references (default: "used")
        repeats: Number of LLM scoring repeats for consistency (default: 3)
        redo: Force recomputation even if already done (default: False)
        max_parallel: Maximum parallel jobs (default: 1)
        timeout_s: Optional per-job timeout in seconds
        dry_run: Preview selection without running evaluator (default: False)
        limit: Optional limit on number of jobs to process (default: None = all)

    Returns:
        Summary dict with:
        {
            "n_selected": int,
            "processed": int,
            "ok": int,
            "error": int,
            "skipped": int,
            "avg_render_runtime_s": float,
            "avg_scoring_runtime_s": float,
            ...
        }

    Raises:
        RuntimeError: If VFScore is not installed
    """
    # Windows DLL fix: Add torch lib to PATH before importing vfscore
    # This must happen BEFORE any vfscore import to ensure DLLs load correctly
    import sys
    import os
    if sys.platform == "win32":
        from pathlib import Path as PathLib
        torch_lib_path = PathLib(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib"
        if torch_lib_path.exists():
            # Add to PATH (for Windows DLL discovery)
            torch_lib_str = str(torch_lib_path)
            if torch_lib_str not in os.environ.get("PATH", ""):
                os.environ["PATH"] = torch_lib_str + os.pathsep + os.environ.get("PATH", "")
            # Also use os.add_dll_directory for Python 3.8+
            try:
                os.add_dll_directory(torch_lib_str)
            except (OSError, AttributeError):
                pass  # Ignore if add_dll_directory not available or fails

    # Early check: Verify VFScore is available (always, even in dry-run)
    try:
        import vfscore  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "VFScore not installed. See quickstart.md for installation instructions."
        ) from e

    # Validate use_images_from
    if use_images_from not in ["used", "source"]:
        raise ValueError(f"use_images_from must be 'used' or 'source', got: {use_images_from}")

    # Load config and paths
    cfg = load_config()
    paths = PathResolver(cfg)
    blender_exe = get_tool_path(cfg, "blender_exe")

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
            "avg_render_runtime_s": 0.0,
            "avg_scoring_runtime_s": 0.0,
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
            use_images_from=use_images_from,
            redo=redo,
            jobs_filter=jobs,
            paths=paths,
        )

        if is_eligible:
            eligible_rows.append(row)
        else:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    n_selected = len(eligible_rows)

    # Apply limit if specified
    if limit is not None and limit > 0:
        eligible_rows = eligible_rows[:limit]
        logger.info(f"Applied limit: processing {len(eligible_rows)} of {n_selected} eligible jobs")

    logger.info(f"Selected {n_selected} eligible jobs for VFScore computation")
    if skip_reasons:
        logger.info(f"Skip reasons: {skip_reasons}")

    # Early exit if no jobs selected
    if n_selected == 0:
        summary = {
            "event": "compute_vfscore",
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "n_selected": 0,
            "processed": 0,
            "ok": 0,
            "error": 0,
            "skipped": 0,
            "avg_render_runtime_s": 0.0,
            "avg_scoring_runtime_s": 0.0,
            "repeats": repeats,
            "use_images_from": use_images_from,
            "redo": redo,
            "max_parallel": max_parallel,
            "dry_run": dry_run,
            "skip_reasons": skip_reasons,
        }
        append_log_record(paths.metrics_log_path(), summary)
        return summary

    # Process jobs (with optional parallelism)
    results: list[dict[str, Any]] = []
    total_render_runtime = 0.0
    total_scoring_runtime = 0.0
    counters = {"ok": 0, "error": 0, "skipped": 0}

    if max_parallel == 1:
        # Sequential processing
        for row in eligible_rows:
            result = _process_job(
                row=row,
                repeats=repeats,
                use_images_from=use_images_from,
                timeout_s=timeout_s,
                paths=paths,
                dry_run=dry_run,
                blender_exe=blender_exe,
            )
            results.append(result)

            # Update counters
            status = result["vf_status"]
            counters[status] = counters.get(status, 0) + 1
            if result["vf_render_runtime_s"]:
                total_render_runtime += result["vf_render_runtime_s"]
            if result["vf_scoring_runtime_s"]:
                total_scoring_runtime += result["vf_scoring_runtime_s"]

    else:
        # Parallel processing
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            futures = {
                executor.submit(
                    _process_job,
                    row=row,
                    repeats=repeats,
                    use_images_from=use_images_from,
                    timeout_s=timeout_s,
                    paths=paths,
                    dry_run=dry_run,
                    blender_exe=blender_exe,
                ): row
                for row in eligible_rows
            }

            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)

                    # Update counters
                    status = result["vf_status"]
                    counters[status] = counters.get(status, 0) + 1
                    if result["vf_render_runtime_s"]:
                        total_render_runtime += result["vf_render_runtime_s"]
                    if result["vf_scoring_runtime_s"]:
                        total_scoring_runtime += result["vf_scoring_runtime_s"]

                except Exception as e:
                    row = futures[future]
                    logger.exception(f"Failed to process job {row['job_id']}: {e}")
                    # Create error result
                    error_result = {
                        "run_id": run_id,
                        "job_id": row["job_id"],
                        "vf_status": "error",
                        "vf_error": f"Processing failed: {str(e)[:180]}",
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
    avg_render_runtime = total_render_runtime / counters["ok"] if counters["ok"] > 0 else 0.0
    avg_scoring_runtime = total_scoring_runtime / counters["ok"] if counters["ok"] > 0 else 0.0

    summary = {
        "event": "compute_vfscore",
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "n_selected": n_selected,
        "processed": len(results),
        "ok": counters.get("ok", 0),
        "error": counters.get("error", 0),
        "skipped": counters.get("skipped", 0),
        "avg_render_runtime_s": round(avg_render_runtime, 2),
        "avg_scoring_runtime_s": round(avg_scoring_runtime, 2),
        "repeats": repeats,
        "use_images_from": use_images_from,
        "redo": redo,
        "max_parallel": max_parallel,
        "dry_run": dry_run,
        "skip_reasons": skip_reasons,
    }

    # Log summary
    append_log_record(paths.metrics_log_path(), summary)

    return summary

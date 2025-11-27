# archi3d/orchestrator/consolidate.py
"""
Phase 4: Consolidate command - reconcile SSOT generations.csv with on-disk artifacts and state markers.

This module implements the `archi3d consolidate` command, which:
- Reads tables/generations.csv (SSOT), runs/<run_id>/manifest.csv, state markers, and output artifacts
- Validates and fixes inconsistencies between CSV and on-disk state
- Deduplicates/merges duplicate (run_id, job_id) rows
- Upserts corrected rows back to generations.csv atomically
- Emits structured summary to logs/metrics.log
- Supports idempotent re-runs (no changes after first reconciliation)
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from archi3d.config.paths import PathResolver
from archi3d.db.generations import upsert_generations
from archi3d.utils.io import append_log_record

# -------------------------
# Constants
# -------------------------

STALE_HEARTBEAT_SECONDS = 600  # 10 minutes
STATUS_PRECEDENCE = {"completed": 4, "failed": 3, "running": 2, "enqueued": 1}


# -------------------------
# File Naming Helpers
# -------------------------


def _format_variant_for_filename(variant: str) -> str:
    """
    Format variant string for use in filenames.

    Rules:
    - Replace spaces with hyphens
    - Lowercase for consistency
    - Remove special characters except hyphens and underscores
    - Use "default" if variant is empty
    """
    if not variant or variant.strip() == "":
        return "default"

    import re
    formatted = variant.lower().strip().replace(" ", "-")
    formatted = re.sub(r'[^a-z0-9\-_]', '', formatted)
    formatted = re.sub(r'-+', '-', formatted)
    formatted = formatted.strip('-')

    return formatted if formatted else "default"


def _generate_glb_filename(
    product_id: str,
    variant: str,
    algo: str,
    job_id: str
) -> str:
    """
    Generate meaningful GLB filename with metadata.

    Format: {product_id}_{variant}_{algo}_{job_id[:8]}.glb
    """
    import pandas as pd

    # Handle pandas NaN values and convert to strings
    product_id_str = str(product_id) if not pd.isna(product_id) else "unknown"
    variant_str = str(variant) if not pd.isna(variant) else ""
    algo_str = str(algo) if not pd.isna(algo) else "unknown"

    # Strip .0 suffix from product_id if it looks like a float string (e.g., "353481.0" -> "353481")
    if product_id_str.endswith(".0") and product_id_str[:-2].isdigit():
        product_id_str = product_id_str[:-2]

    variant_formatted = _format_variant_for_filename(variant_str)
    job_id_short = job_id[:8]

    return f"{product_id_str}_{variant_formatted}_{algo_str}_{job_id_short}.glb"


# -------------------------
# Helper Functions
# -------------------------


def _get_file_timestamp(file_path: Path) -> str | None:
    """
    Get file modification timestamp as ISO8601 UTC string.

    Args:
        file_path: Path to file

    Returns:
        ISO8601 timestamp string or None if file doesn't exist
    """
    if not file_path.exists():
        return None
    mtime = file_path.stat().st_mtime
    dt = datetime.fromtimestamp(mtime, tz=UTC)
    return dt.isoformat()


def _read_marker_timestamp(marker_path: Path) -> str | None:
    """
    Read timestamp from state marker file.

    Marker files contain "timestamp: <ISO8601>" on first line.

    Args:
        marker_path: Path to marker file

    Returns:
        ISO8601 timestamp string or None if file doesn't exist or format is invalid
    """
    if not marker_path.exists():
        return None

    try:
        content = marker_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.startswith("timestamp:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass

    return None


def _is_heartbeat_fresh(marker_path: Path, stale_seconds: int = STALE_HEARTBEAT_SECONDS) -> bool:
    """
    Check if inprogress marker heartbeat is fresh.

    Args:
        marker_path: Path to .inprogress marker file
        stale_seconds: Threshold for considering heartbeat stale

    Returns:
        True if heartbeat is fresh (< stale_seconds old), False otherwise
    """
    timestamp_str = _read_marker_timestamp(marker_path)
    if not timestamp_str:
        return False

    try:
        marker_time = datetime.fromisoformat(timestamp_str)
        now = datetime.now(UTC)
        age_seconds = (now - marker_time).total_seconds()
        return age_seconds < stale_seconds
    except Exception:
        return False


def _gather_evidence(
    row: pd.Series,
    run_id: str,
    state_dir: Path,
    outputs_dir: Path,
    paths: PathResolver,
) -> dict[str, Any]:
    """
    Gather evidence from disk for a single job.

    Args:
        row: Row from generations.csv (may have incomplete/incorrect data)
        run_id: Run identifier
        state_dir: Path to runs/<run_id>/state/
        outputs_dir: Path to runs/<run_id>/outputs/
        paths: PathResolver instance

    Returns:
        Dictionary with evidence keys:
        - has_completed_marker, has_failed_marker, has_inprogress_marker
        - completed_ts, failed_ts, inprogress_ts (timestamps from markers)
        - heartbeat_fresh (bool)
        - has_generated_glb, glb_size, glb_ts
        - preview_paths (list of workspace-relative paths for existing previews)
        - error_txt_content (first ~2000 chars if error.txt exists)
    """
    job_id = row["job_id"]
    evidence = {
        "has_completed_marker": False,
        "has_failed_marker": False,
        "has_inprogress_marker": False,
        "completed_ts": None,
        "failed_ts": None,
        "inprogress_ts": None,
        "heartbeat_fresh": False,
        "has_generated_glb": False,
        "glb_size": 0,
        "glb_ts": None,
        "preview_paths": [],
        "error_txt_content": None,
    }

    # Check state markers
    completed_marker = state_dir / f"{job_id}.completed"
    failed_marker = state_dir / f"{job_id}.failed"
    inprogress_marker = state_dir / f"{job_id}.inprogress"
    error_txt = state_dir / f"{job_id}.error.txt"

    if completed_marker.exists():
        evidence["has_completed_marker"] = True
        evidence["completed_ts"] = _read_marker_timestamp(completed_marker)

    if failed_marker.exists():
        evidence["has_failed_marker"] = True
        evidence["failed_ts"] = _read_marker_timestamp(failed_marker)

    if inprogress_marker.exists():
        evidence["has_inprogress_marker"] = True
        evidence["inprogress_ts"] = _read_marker_timestamp(inprogress_marker)
        evidence["heartbeat_fresh"] = _is_heartbeat_fresh(inprogress_marker)

    # Check generated GLB file (try new naming first, fallback to legacy)
    job_output_dir = outputs_dir / job_id

    # Try new meaningful filename format
    new_glb_filename = _generate_glb_filename(
        row["product_id"], row["variant"], row["algo"], job_id
    )
    new_glb_path = job_output_dir / new_glb_filename

    # Fallback to legacy filename
    legacy_glb_path = job_output_dir / "generated.glb"

    # Determine which path to use
    glb_path = None
    if new_glb_path.exists():
        glb_path = new_glb_path
    elif legacy_glb_path.exists():
        glb_path = legacy_glb_path

    if glb_path is not None:
        evidence["has_generated_glb"] = True
        evidence["glb_size"] = glb_path.stat().st_size
        evidence["glb_ts"] = _get_file_timestamp(glb_path)

    # Check preview images
    preview_paths = []
    for i in range(1, 4):
        preview_path = job_output_dir / f"preview_{i}.png"
        if preview_path.exists():
            # Store workspace-relative path
            rel_path = paths.rel_to_workspace(preview_path)
            preview_paths.append(rel_path.as_posix())
    evidence["preview_paths"] = preview_paths

    # Read error.txt if present
    if error_txt.exists():
        try:
            content = error_txt.read_text(encoding="utf-8")
            evidence["error_txt_content"] = content[:2000]
        except Exception:
            pass

    return evidence


def _determine_desired_status(evidence: dict[str, Any], csv_status: str) -> str:
    """
    Determine desired status based on evidence using truth table.

    Args:
        evidence: Evidence dictionary from _gather_evidence
        csv_status: Current status from CSV

    Returns:
        Desired status string ("completed", "failed", "running", "enqueued")

    Truth table (priority order):
    1. .completed + generated.glb exists => completed
    2. .failed exists => failed
    3. .inprogress + heartbeat fresh => running
    4. Otherwise => keep CSV status (default enqueued)
    """
    # Priority 1: Completed marker + GLB file
    if evidence["has_completed_marker"] and evidence["has_generated_glb"]:
        return "completed"

    # Priority 2: Failed marker
    if evidence["has_failed_marker"]:
        return "failed"

    # Priority 3: Running with fresh heartbeat
    if evidence["has_inprogress_marker"] and evidence["heartbeat_fresh"]:
        return "running"

    # Priority 4: Keep CSV status (default to enqueued if empty)
    return csv_status if csv_status else "enqueued"


def _reconcile_row(
    row: pd.Series,
    evidence: dict[str, Any],
    desired_status: str,
    paths: PathResolver,
    run_id: str,
    fix_status: bool,
) -> tuple[pd.Series, dict[str, Any]]:
    """
    Reconcile a single row based on evidence and desired status.

    Args:
        row: Row from generations.csv
        evidence: Evidence dictionary from _gather_evidence
        desired_status: Desired status from _determine_desired_status
        paths: PathResolver instance
        run_id: Run identifier
        fix_status: If True, apply status downgrades for missing outputs

    Returns:
        Tuple of (reconciled_row, changes_dict)
        changes_dict tracks what was modified:
        - status_changed, downgraded_missing_output, timestamps_fixed, paths_filled, error_msg_filled
    """
    changes = {
        "status_changed": False,
        "downgraded_missing_output": False,
        "timestamps_fixed": False,
        "paths_filled": False,
        "error_msg_filled": False,
    }

    reconciled = row.copy()
    job_id = row["job_id"]

    # Handle status reconciliation
    csv_status = row.get("status", "")

    # Downgrade if CSV says completed but GLB missing
    if fix_status and csv_status == "completed" and not evidence["has_generated_glb"]:
        reconciled["status"] = "failed"
        reconciled["error_msg"] = "missing generated GLB file; downgraded by consolidate"
        changes["status_changed"] = True
        changes["downgraded_missing_output"] = True
    elif desired_status != csv_status:
        reconciled["status"] = desired_status
        changes["status_changed"] = True

    # Check if worker already wrote valid timestamps (duration > 1 second indicates
    # real execution, not marker-derived estimates which are typically < 0.1s)
    existing_duration = row.get("generation_duration_s", 0)
    if pd.isna(existing_duration):
        existing_duration = 0
    has_valid_worker_data = existing_duration > 1.0

    # Only fill timestamps from markers if worker data is missing/invalid
    if not has_valid_worker_data:
        if pd.isna(row.get("generation_start")) or not row.get("generation_start"):
            # Use earliest timestamp from markers or outputs
            candidates = [
                evidence.get("completed_ts"),
                evidence.get("failed_ts"),
                evidence.get("inprogress_ts"),
                evidence.get("glb_ts"),
            ]
            earliest = min((ts for ts in candidates if ts), default=None)
            if earliest:
                reconciled["generation_start"] = earliest
                changes["timestamps_fixed"] = True

        if pd.isna(row.get("generation_end")) or not row.get("generation_end"):
            # Use latest timestamp from markers or outputs
            candidates = [
                evidence.get("completed_ts"),
                evidence.get("failed_ts"),
                evidence.get("glb_ts"),
            ]
            latest = max((ts for ts in candidates if ts), default=None)
            if latest:
                reconciled["generation_end"] = latest
                changes["timestamps_fixed"] = True

        # Only recompute duration if we just filled timestamps (worker data was missing)
        if changes.get("timestamps_fixed") and reconciled.get("generation_start") and reconciled.get("generation_end"):
            try:
                start = datetime.fromisoformat(str(reconciled["generation_start"]))
                end = datetime.fromisoformat(str(reconciled["generation_end"]))
                duration = max((end - start).total_seconds(), 0)
                reconciled["generation_duration_s"] = duration
            except Exception:
                pass

    # Fill output paths
    if evidence["has_generated_glb"]:
        job_output_dir = paths.outputs_dir(run_id, job_id=job_id)

        # Try new filename format first, fallback to legacy
        new_glb_filename = _generate_glb_filename(
            row["product_id"], row["variant"], row["algo"], job_id
        )
        new_glb_path = job_output_dir / new_glb_filename
        legacy_glb_path = job_output_dir / "generated.glb"

        # Use whichever file exists
        if new_glb_path.exists():
            glb_path = new_glb_path
        elif legacy_glb_path.exists():
            glb_path = legacy_glb_path
        else:
            # Shouldn't happen if evidence["has_generated_glb"] is True, but be defensive
            glb_path = new_glb_path  # Default to new format

        rel_glb = paths.rel_to_workspace(glb_path).as_posix()

        if pd.isna(row.get("gen_object_path")) or not row.get("gen_object_path"):
            reconciled["gen_object_path"] = rel_glb
            changes["paths_filled"] = True
        elif row.get("gen_object_path") != rel_glb:
            # Normalize path
            reconciled["gen_object_path"] = rel_glb
            changes["paths_filled"] = True

    # Fill preview paths
    for i, preview_path in enumerate(evidence["preview_paths"], start=1):
        col = f"preview_{i}_path"
        if pd.isna(row.get(col)) or not row.get(col):
            reconciled[col] = preview_path
            changes["paths_filled"] = True

    # Fill error_msg from error.txt if missing
    if evidence.get("error_txt_content"):
        if pd.isna(row.get("error_msg")) or not row.get("error_msg"):
            content = evidence["error_txt_content"]
            if len(content) >= 2000:
                reconciled["error_msg"] = content + " (truncated; see error.txt)"
            else:
                reconciled["error_msg"] = content
            changes["error_msg_filled"] = True

    return reconciled, changes


def _merge_duplicate_rows(rows: list[pd.Series]) -> pd.Series:
    """
    Merge duplicate (run_id, job_id) rows by keeping most complete information.

    Merge strategy:
    1. Prefer row with highest status precedence (completed > failed > running > enqueued)
    2. For each column, prefer non-empty/non-NaN values
    3. For paths, prefer existing file paths over empty
    4. Keep widest set of non-empty columns

    Args:
        rows: List of Series representing duplicate rows

    Returns:
        Merged Series with union of non-empty fields
    """
    if len(rows) == 1:
        return rows[0]

    # Sort by status precedence (highest first)
    rows_sorted = sorted(
        rows,
        key=lambda r: STATUS_PRECEDENCE.get(r.get("status", "enqueued"), 0),
        reverse=True,
    )

    # Start with highest precedence row
    merged = rows_sorted[0].copy()

    # Merge columns from other rows (prefer non-empty)
    for row in rows_sorted[1:]:
        for col in row.index:
            # Skip if column not in merged yet
            if col not in merged.index:
                merged[col] = row[col]
                continue

            merged_val = merged[col]
            row_val = row[col]

            # Prefer non-NaN, non-empty values
            if pd.isna(merged_val) or merged_val == "":
                if not pd.isna(row_val) and row_val != "":
                    merged[col] = row_val
            elif isinstance(merged_val, (int, float)) and pd.isna(merged_val):
                if not pd.isna(row_val):
                    merged[col] = row_val

    return merged


def _consolidate_run(
    run_id: str,
    paths: PathResolver,
    dry_run: bool = False,
    strict: bool = False,
    only_status: list[str] | None = None,
    fix_status: bool = True,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """
    Consolidate a single run's data.

    Args:
        run_id: Run identifier
        paths: PathResolver instance
        dry_run: If True, compute changes but don't write CSV
        strict: If True, exit with error on any conflict
        only_status: Optional list of statuses to filter jobs
        fix_status: If True, apply status downgrades for missing outputs
        max_rows: Optional cap on number of rows to process

    Returns:
        Summary dictionary with counters and histograms
    """
    generations_csv_path = paths.generations_csv_path()
    state_dir = paths.state_dir(run_id)
    outputs_dir = paths.outputs_dir(run_id)

    # Read generations.csv
    if not generations_csv_path.exists():
        # No CSV yet - nothing to consolidate
        return {
            "considered": 0,
            "upsert_inserted": 0,
            "upsert_updated": 0,
            "unchanged": 0,
            "conflicts_resolved": 0,
            "marker_mismatches_fixed": 0,
            "downgraded_missing_output": 0,
            "missing_outputs": 0,
            "invalid_previews": 0,
            "status_histogram_before": {},
            "status_histogram_after": {},
        }

    df = pd.read_csv(
        generations_csv_path,
        encoding="utf-8-sig",
        dtype={"product_id": str, "variant": str},
    )

    # Filter by run_id
    df_run = df[df["run_id"] == run_id].copy()

    # Filter by status if specified
    if only_status:
        df_run = df_run[df_run["status"].isin(only_status)]

    # Apply max_rows cap
    if max_rows:
        df_run = df_run.head(max_rows)

    considered = len(df_run)

    if considered == 0:
        return {
            "considered": 0,
            "upsert_inserted": 0,
            "upsert_updated": 0,
            "unchanged": 0,
            "conflicts_resolved": 0,
            "marker_mismatches_fixed": 0,
            "downgraded_missing_output": 0,
            "missing_outputs": 0,
            "invalid_previews": 0,
            "status_histogram_before": {},
            "status_histogram_after": {},
        }

    # Compute status histogram before
    status_histogram_before = df_run["status"].value_counts().to_dict()

    # Gather evidence and reconcile each row
    reconciled_rows = []
    total_changes = {
        "status_changed": 0,
        "downgraded_missing_output": 0,
        "timestamps_fixed": 0,
        "paths_filled": 0,
        "error_msg_filled": 0,
    }

    for _, row in df_run.iterrows():
        evidence = _gather_evidence(row, run_id, state_dir, outputs_dir, paths)
        csv_status = row.get("status", "enqueued")
        desired_status = _determine_desired_status(evidence, csv_status)

        reconciled, changes = _reconcile_row(
            row, evidence, desired_status, paths, run_id, fix_status
        )
        reconciled_rows.append(reconciled)

        # Aggregate changes
        for key in changes:
            if changes[key]:
                total_changes[key] += 1

    # Convert to DataFrame
    df_reconciled = pd.DataFrame(reconciled_rows)

    # Handle duplicates (merge by run_id, job_id)
    duplicate_groups = df_reconciled.groupby(["run_id", "job_id"])
    conflicts_resolved = sum(1 for _, group in duplicate_groups if len(group) > 1)

    if conflicts_resolved > 0:
        merged_rows = []
        for (_rid, _jid), group in duplicate_groups:
            rows_list = [row for _, row in group.iterrows()]
            merged = _merge_duplicate_rows(rows_list)
            merged_rows.append(merged)
        df_reconciled = pd.DataFrame(merged_rows)

    # Compute status histogram after
    status_histogram_after = df_reconciled["status"].value_counts().to_dict()

    # Determine unchanged count (compare before/after on job_id basis)
    # Unchanged = rows where reconciled matches original
    unchanged = 0
    for _, orig_row in df_run.iterrows():
        job_id = orig_row["job_id"]
        reconciled_match = df_reconciled[df_reconciled["job_id"] == job_id]
        if len(reconciled_match) == 1:
            rec = reconciled_match.iloc[0]
            # Simple heuristic: unchanged if status and key fields match
            if (
                orig_row.get("status") == rec.get("status")
                and orig_row.get("gen_object_path") == rec.get("gen_object_path")
                and orig_row.get("error_msg") == rec.get("error_msg")
            ):
                unchanged += 1

    # Upsert to CSV (unless dry-run)
    upsert_inserted = 0
    upsert_updated = 0
    if not dry_run:
        # Special handling for duplicates: remove all rows for this run_id first,
        # then insert the deduplicated/merged rows as new inserts
        if conflicts_resolved > 0:
            # Read full CSV, remove duplicates for this run_id, then add merged rows
            from filelock import FileLock

            lock_path = generations_csv_path.with_suffix(".lock")

            with FileLock(lock_path, timeout=30):
                df_full = pd.read_csv(
                    generations_csv_path,
                    encoding="utf-8-sig",
                    dtype={"product_id": str, "variant": str},
                )

                # Remove all rows for this run_id (including duplicates)
                df_other_runs = df_full[df_full["run_id"] != run_id]

                # Append reconciled rows
                df_final = pd.concat([df_other_runs, df_reconciled], ignore_index=True)

                # Write atomically
                tmp_path = generations_csv_path.with_suffix(".tmp")
                try:
                    df_final.to_csv(tmp_path, index=False, encoding="utf-8-sig")
                    import os

                    os.replace(tmp_path, generations_csv_path)
                except Exception:
                    if tmp_path.exists():
                        tmp_path.unlink()
                    raise

                # Count as updates (approximate - all rows for this run_id)
                upsert_updated = len(df_reconciled)
        else:
            # No duplicates detected, use normal upsert
            upsert_inserted, upsert_updated = upsert_generations(
                generations_csv_path, df_reconciled
            )

    # Build summary
    summary = {
        "considered": considered,
        "upsert_inserted": upsert_inserted,
        "upsert_updated": upsert_updated,
        "unchanged": unchanged,
        "conflicts_resolved": conflicts_resolved,
        "marker_mismatches_fixed": total_changes["status_changed"],
        "downgraded_missing_output": total_changes["downgraded_missing_output"],
        "missing_outputs": 0,  # TODO: track this separately if needed
        "invalid_previews": 0,  # TODO: track this separately if needed
        "status_histogram_before": status_histogram_before,
        "status_histogram_after": status_histogram_after,
    }

    # Strict mode: fail on conflicts
    if strict and (conflicts_resolved > 0 or total_changes["downgraded_missing_output"] > 0):
        raise RuntimeError(
            f"Strict mode: found {conflicts_resolved} conflicts and "
            f"{total_changes['downgraded_missing_output']} downgrades"
        )

    return summary


def consolidate(
    run_id: str,
    paths: PathResolver,
    dry_run: bool = False,
    strict: bool = False,
    only_status: str | None = None,
    fix_status: bool = True,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """
    Main entry point for consolidate command.

    Reconciles tables/generations.csv with on-disk artifacts and state markers
    for a given run_id. Ensures SSOT consistency, deduplicates rows, and fills
    missing metadata.

    Args:
        run_id: Run identifier (required)
        paths: PathResolver instance
        dry_run: If True, compute but don't write changes (default: False)
        strict: If True, exit with error on any conflict (default: False)
        only_status: Comma-separated list of statuses to process (default: all)
        fix_status: If True, apply status downgrades for missing outputs (default: True)
        max_rows: Optional cap on number of rows to process (default: unlimited)

    Returns:
        Summary dictionary with counters and histograms

    Side effects:
        - Upserts rows to tables/generations.csv (unless dry_run=True)
        - Appends structured JSON summary to logs/metrics.log
    """
    # Parse only_status
    status_filter = None
    if only_status:
        status_filter = [s.strip() for s in only_status.split(",") if s.strip()]

    # Run consolidation
    summary = _consolidate_run(
        run_id=run_id,
        paths=paths,
        dry_run=dry_run,
        strict=strict,
        only_status=status_filter,
        fix_status=fix_status,
        max_rows=max_rows,
    )

    # Add metadata to summary
    summary["event"] = "consolidate"
    summary["timestamp"] = datetime.now(UTC).isoformat()
    summary["run_id"] = run_id
    summary["dry_run"] = dry_run

    # Append to metrics.log
    log_path = paths.metrics_log_path()
    append_log_record(log_path, summary)

    return summary

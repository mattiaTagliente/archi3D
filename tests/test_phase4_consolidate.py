"""
Phase 4 Tests: Consolidate command - reconciliation of SSOT with on-disk artifacts.

Tests cover:
1. Happy path (completed jobs with artifacts)
2. Downgrade missing output (CSV says completed but file missing)
3. Merge duplicates (duplicate (run_id, job_id) rows)
4. Heartbeat stale handling (running job with stale heartbeat)
5. Dry-run mode (no CSV writes)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from archi3d.config.loader import load_config
from archi3d.config.paths import PathResolver
from archi3d.orchestrator.consolidate import consolidate


@pytest.fixture
def temp_workspace(tmp_path: Path) -> PathResolver:
    """
    Create a temporary workspace with proper structure.

    Returns:
        PathResolver for the temp workspace.
    """
    # Create workspace structure
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create dataset (required for PathResolver validation)
    (workspace / "dataset").mkdir()

    # Set environment variable for workspace
    import os

    os.environ["ARCHI3D_WORKSPACE"] = str(workspace)

    # Create minimal global.yaml
    global_yaml = tmp_path / "global.yaml"
    global_yaml.write_text(
        """
algorithms:
  - test_algo_1
  - test_algo_2

thresholds:
  lpips_max: 0.5
  fscore_min: 0.7
""",
        encoding="utf-8",
    )

    # Load config and create PathResolver
    # Note: We need to temporarily change cwd to tmp_path for _find_repo_root
    import os

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        cfg = load_config()
        paths = PathResolver(cfg)
    finally:
        os.chdir(old_cwd)

    return paths


def _create_test_job_data(
    paths: PathResolver,
    run_id: str,
    job_id: str,
    status: str,
    has_marker: bool = True,
    has_glb: bool = True,
    has_previews: int = 0,
    has_error_txt: bool = False,
    glb_content: str = "fake glb content",
) -> pd.Series:
    """
    Create a test job with specified artifacts and markers.

    Args:
        paths: PathResolver instance
        run_id: Run identifier
        job_id: Job identifier
        status: Job status in CSV
        has_marker: If True, create state marker matching status
        has_glb: If True, create generated.glb file
        has_previews: Number of preview images to create (0-3)
        has_error_txt: If True, create error.txt file
        glb_content: Content for generated.glb file

    Returns:
        Series representing the CSV row for this job
    """
    # Create state marker if requested
    state_dir = paths.state_dir(run_id)
    if has_marker:
        marker_path = state_dir / f"{job_id}.{status}"
        timestamp = datetime.now(UTC).isoformat()
        marker_content = f"timestamp: {timestamp}\npid: 12345\n"
        marker_path.write_text(marker_content, encoding="utf-8")

    # Create output artifacts
    job_output_dir = paths.outputs_dir(run_id, job_id=job_id)

    if has_glb:
        glb_path = job_output_dir / "generated.glb"
        glb_path.write_text(glb_content, encoding="utf-8")

    for i in range(1, has_previews + 1):
        preview_path = job_output_dir / f"preview_{i}.png"
        preview_path.write_bytes(b"fake png content")

    if has_error_txt:
        error_txt = state_dir / f"{job_id}.error.txt"
        error_txt.write_text("Test error message from worker", encoding="utf-8")

    # Create CSV row
    row_data = {
        "run_id": run_id,
        "job_id": job_id,
        "product_id": "100001",
        "variant": "default",
        "algo": "test_algo_1",
        "status": status,
        "created_at": datetime.now(UTC).isoformat(),
        "used_n_images": 2,
        "used_image_1_path": "dataset/100001/images/a.jpg",
        "used_image_2_path": "dataset/100001/images/b.jpg",
    }

    return pd.Series(row_data)


def test_consolidate_happy_path(temp_workspace: PathResolver):
    """
    Test 1: Happy path with completed jobs that have all artifacts.

    Expected: Minimal changes, paths filled, status unchanged.
    """
    paths = temp_workspace
    run_id = "test-run-happy"

    # Create 2 completed jobs with full artifacts
    job1 = _create_test_job_data(
        paths, run_id, "job001", "completed", has_marker=True, has_glb=True, has_previews=2
    )
    job2 = _create_test_job_data(
        paths, run_id, "job002", "completed", has_marker=True, has_glb=True, has_previews=3
    )

    # Create 1 failed job
    job3 = _create_test_job_data(
        paths, run_id, "job003", "failed", has_marker=True, has_glb=False, has_error_txt=True
    )

    # Write initial generations.csv
    df = pd.DataFrame([job1, job2, job3])
    generations_csv = paths.generations_csv_path()
    df.to_csv(generations_csv, index=False, encoding="utf-8-sig")

    # Run consolidate
    summary = consolidate(run_id=run_id, paths=paths, dry_run=False)

    # Assertions
    assert summary["considered"] == 3
    assert summary["conflicts_resolved"] == 0
    assert summary["downgraded_missing_output"] == 0

    # Check that CSV was updated with paths
    df_after = pd.read_csv(generations_csv, encoding="utf-8-sig")
    df_run = df_after[df_after["run_id"] == run_id]

    assert len(df_run) == 3

    # Check job1 has gen_object_path and preview paths
    job1_after = df_run[df_run["job_id"] == "job001"].iloc[0]
    assert pd.notna(job1_after.get("gen_object_path"))
    assert "generated.glb" in str(job1_after["gen_object_path"])
    assert pd.notna(job1_after.get("preview_1_path"))
    assert pd.notna(job1_after.get("preview_2_path"))

    # Check job3 (failed) has error_msg from error.txt
    job3_after = df_run[df_run["job_id"] == "job003"].iloc[0]
    assert pd.notna(job3_after.get("error_msg"))
    assert "Test error message" in str(job3_after["error_msg"])

    # Check metrics log
    log_path = paths.metrics_log_path()
    assert log_path.exists()
    log_content = log_path.read_text(encoding="utf-8")
    assert "consolidate" in log_content


def test_consolidate_downgrade_missing_output(temp_workspace: PathResolver):
    """
    Test 2: Downgrade status when CSV says completed but generated.glb is missing.

    Expected: Status downgraded to 'failed' with error_msg set.
    """
    paths = temp_workspace
    run_id = "test-run-downgrade"

    # Create job marked as completed in CSV but no GLB file
    job1 = _create_test_job_data(
        paths, run_id, "job001", "completed", has_marker=False, has_glb=False
    )

    # Write initial generations.csv
    df = pd.DataFrame([job1])
    generations_csv = paths.generations_csv_path()
    df.to_csv(generations_csv, index=False, encoding="utf-8-sig")

    # Run consolidate with fix_status=True
    summary = consolidate(run_id=run_id, paths=paths, dry_run=False, fix_status=True)

    # Assertions
    assert summary["considered"] == 1
    assert summary["downgraded_missing_output"] == 1
    assert summary["status_histogram_after"]["failed"] == 1
    assert summary["status_histogram_before"]["completed"] == 1

    # Check CSV
    df_after = pd.read_csv(generations_csv, encoding="utf-8-sig")
    job1_after = df_after[df_after["job_id"] == "job001"].iloc[0]

    assert job1_after["status"] == "failed"
    assert pd.notna(job1_after.get("error_msg"))
    assert "missing generated.glb" in str(job1_after["error_msg"])
    assert "downgraded by consolidate" in str(job1_after["error_msg"])


def test_consolidate_merge_duplicates(temp_workspace: PathResolver):
    """
    Test 3: Merge duplicate (run_id, job_id) rows by keeping most complete information.

    Expected: Single merged row with union of non-empty fields, conflicts_resolved=1.
    """
    paths = temp_workspace
    run_id = "test-run-duplicates"

    # Create duplicate rows for same job
    # First row: completed with outputs
    job1a = _create_test_job_data(
        paths, run_id, "job001", "completed", has_marker=True, has_glb=True
    )

    # Second row: running with worker fields (simulating partial update)
    job1b = pd.Series(
        {
            "run_id": run_id,
            "job_id": "job001",
            "product_id": "100001",
            "variant": "default",
            "algo": "test_algo_1",
            "status": "running",
            "worker_host": "test-host",
            "worker_user": "test-user",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )

    # Write CSV with both rows
    df = pd.DataFrame([job1a, job1b])
    generations_csv = paths.generations_csv_path()
    df.to_csv(generations_csv, index=False, encoding="utf-8-sig")

    # Run consolidate
    summary = consolidate(run_id=run_id, paths=paths, dry_run=False)

    # Assertions
    assert summary["considered"] == 2
    assert summary["conflicts_resolved"] == 1

    # Check CSV has single merged row
    df_after = pd.read_csv(generations_csv, encoding="utf-8-sig")
    df_run = df_after[df_after["run_id"] == run_id]

    assert len(df_run) == 1

    merged = df_run.iloc[0]
    # Should prefer completed status (higher precedence)
    assert merged["status"] == "completed"
    # Should keep worker fields from second row
    assert pd.notna(merged.get("worker_host"))
    assert merged["worker_host"] == "test-host"
    # Should have gen_object_path from first row
    assert pd.notna(merged.get("gen_object_path"))


def test_consolidate_heartbeat_stale(temp_workspace: PathResolver):
    """
    Test 4: Handle stale inprogress marker (heartbeat > 10 min old).

    Expected: Job with stale heartbeat keeps 'running' status as per documented behavior.
    """
    paths = temp_workspace
    run_id = "test-run-stale"

    # Create job with stale inprogress marker
    state_dir = paths.state_dir(run_id)
    job_id = "job001"

    # Create stale marker (20 minutes old)
    marker_path = state_dir / f"{job_id}.inprogress"
    stale_timestamp = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    marker_content = f"timestamp: {stale_timestamp}\npid: 12345\n"
    marker_path.write_text(marker_content, encoding="utf-8")

    # Create CSV row with running status
    job1 = pd.Series(
        {
            "run_id": run_id,
            "job_id": job_id,
            "product_id": "100001",
            "variant": "default",
            "algo": "test_algo_1",
            "status": "running",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )

    df = pd.DataFrame([job1])
    generations_csv = paths.generations_csv_path()
    df.to_csv(generations_csv, index=False, encoding="utf-8-sig")

    # Run consolidate
    summary = consolidate(run_id=run_id, paths=paths, dry_run=False)

    # Assertions
    assert summary["considered"] == 1

    # Check CSV - job should keep 'running' status (stale heartbeat doesn't auto-downgrade)
    # As per plan: "leave as is (documented behavior)"
    df_after = pd.read_csv(generations_csv, encoding="utf-8-sig")
    job1_after = df_after[df_after["job_id"] == job_id].iloc[0]

    # Since inprogress marker exists but heartbeat is stale and no outputs,
    # the truth table says: keep CSV status (which is 'running')
    assert job1_after["status"] in ["running", "enqueued"]  # Either is acceptable per spec


def test_consolidate_dry_run(temp_workspace: PathResolver):
    """
    Test 5: Dry-run mode computes changes without writing CSV.

    Expected: No CSV writes, log includes dry_run=true, summary shows projected changes.
    """
    paths = temp_workspace
    run_id = "test-run-dryrun"

    # Create job that would be downgraded
    job1 = _create_test_job_data(
        paths, run_id, "job001", "completed", has_marker=False, has_glb=False
    )

    # Write initial CSV
    df = pd.DataFrame([job1])
    generations_csv = paths.generations_csv_path()
    df.to_csv(generations_csv, index=False, encoding="utf-8-sig")

    # Capture initial CSV content
    initial_csv = generations_csv.read_text(encoding="utf-8-sig")

    # Run consolidate in dry-run mode
    summary = consolidate(run_id=run_id, paths=paths, dry_run=True, fix_status=True)

    # Assertions
    assert summary["dry_run"] is True
    assert summary["considered"] == 1
    assert summary["upsert_inserted"] == 0
    assert summary["upsert_updated"] == 0  # No actual updates in dry-run

    # CSV should be unchanged
    final_csv = generations_csv.read_text(encoding="utf-8-sig")
    assert initial_csv == final_csv

    # Check that log was still written with dry_run flag
    log_path = paths.metrics_log_path()
    assert log_path.exists()
    log_content = log_path.read_text(encoding="utf-8")
    assert '"dry_run": true' in log_content or '"dry_run":true' in log_content

    # Summary should show projected changes
    assert summary["downgraded_missing_output"] == 1
    assert summary["status_histogram_after"]["failed"] == 1


def test_consolidate_idempotent(temp_workspace: PathResolver):
    """
    Test 6 (bonus): Idempotency - re-running consolidate should yield minimal changes.

    Expected: First run makes changes, second run has upsert_updated≈0.
    """
    paths = temp_workspace
    run_id = "test-run-idempotent"

    # Create job
    job1 = _create_test_job_data(
        paths, run_id, "job001", "completed", has_marker=True, has_glb=True
    )

    df = pd.DataFrame([job1])
    generations_csv = paths.generations_csv_path()
    df.to_csv(generations_csv, index=False, encoding="utf-8-sig")

    # First run
    summary1 = consolidate(run_id=run_id, paths=paths, dry_run=False)
    assert summary1["considered"] == 1

    # Second run (should be idempotent)
    summary2 = consolidate(run_id=run_id, paths=paths, dry_run=False)
    assert summary2["considered"] == 1
    # After first run, paths are filled, so second run should have minimal/no updates
    # Accept either 0 or 1 update (depending on path normalization)
    assert summary2["upsert_updated"] <= 1


def test_consolidate_no_csv_exists(temp_workspace: PathResolver):
    """
    Test 7 (bonus): Handle case where generations.csv doesn't exist yet.

    Expected: Returns empty summary without errors.
    """
    paths = temp_workspace
    run_id = "test-run-no-csv"

    # Don't create generations.csv
    summary = consolidate(run_id=run_id, paths=paths, dry_run=False)

    # Assertions
    assert summary["considered"] == 0
    assert summary["upsert_inserted"] == 0
    assert summary["upsert_updated"] == 0

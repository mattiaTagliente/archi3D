"""
Phase 5 Tests: Compute FScore command - geometry metrics computation.

Tests cover:
1. Happy path (completed job with valid GT and gen objects, dry-run and real mode)
2. Missing GT (job with missing GT object)
3. Idempotency (re-run without --redo should skip already computed jobs)
4. Redo mode (re-run with --redo should recompute)
5. Concurrency and timeout (parallel processing with timeout handling)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from archi3d.config.loader import load_config
from archi3d.config.paths import PathResolver
from archi3d.metrics.fscore import compute_fscore
from archi3d.metrics.fscore_adapter import FScoreResponse


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
    import os

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        cfg = load_config()
        paths = PathResolver(cfg)
    finally:
        os.chdir(old_cwd)

    return paths


def _create_mock_fscore_response(
    ok: bool = True,
    fscore: float = 0.85,
    precision: float = 0.82,
    recall: float = 0.88,
    chamfer_l2: float = 0.012,
    runtime_s: float = 2.5,
) -> FScoreResponse:
    """Create a mock FScore response with typical values."""
    if not ok:
        return FScoreResponse(ok=False, error="Mock evaluation error")

    payload = {
        "fscore": fscore,
        "precision": precision,
        "recall": recall,
        "chamfer_l2": chamfer_l2,
        "n_points": 100000,
        "alignment": {
            "scale": 1.02,
            "rotation_quat": {"w": 0.99, "x": 0.01, "y": 0.02, "z": 0.01},
            "translation": {"x": 0.05, "y": -0.03, "z": 0.01},
        },
        "dist_stats": {
            "mean": 0.008,
            "median": 0.006,
            "p95": 0.020,
            "p99": 0.035,
            "max": 0.12,
        },
        "mesh_meta": {
            "gt_vertices": 10234,
            "gt_triangles": 20468,
            "pred_vertices": 9876,
            "pred_triangles": 19752,
        },
    }

    return FScoreResponse(
        ok=True,
        payload=payload,
        tool_version="1.0.0",
        config_hash="abc123",
        runtime_s=runtime_s,
    )


def _create_test_job(
    paths: PathResolver,
    run_id: str,
    job_id: str,
    has_gt: bool = True,
    has_gen: bool = True,
    fscore_status: str | None = None,
) -> pd.Series:
    """
    Create a test job with specified artifacts.

    Args:
        paths: PathResolver instance
        run_id: Run identifier
        job_id: Job identifier
        has_gt: If True, create GT object file
        has_gen: If True, create generated object file
        fscore_status: If set, populate fscore_status column

    Returns:
        pandas Series with job data
    """
    # Create GT object if requested
    gt_path_str = ""
    if has_gt:
        gt_path = paths.workspace_root / "dataset" / "test_product" / "gt" / "model.glb"
        gt_path.parent.mkdir(parents=True, exist_ok=True)
        gt_path.write_text("mock GT mesh data", encoding="utf-8")
        gt_path_str = str(paths.rel_to_workspace(gt_path).as_posix())

    # Create generated object if requested
    gen_path_str = ""
    if has_gen:
        gen_path = paths.runs_root / run_id / "outputs" / job_id / "generated.glb"
        gen_path.parent.mkdir(parents=True, exist_ok=True)
        gen_path.write_text("mock generated mesh data", encoding="utf-8")
        gen_path_str = str(paths.rel_to_workspace(gen_path).as_posix())

    # Create job data
    data = {
        "run_id": run_id,
        "job_id": job_id,
        "product_id": "test_product",
        "variant": "",
        "algo": "test_algo_1",
        "status": "completed",
        "gt_object_path": gt_path_str,
        "gen_object_path": gen_path_str,
    }

    if fscore_status:
        data["fscore_status"] = fscore_status
        data["fscore"] = 0.85

    return pd.Series(data)


def test_happy_path_dry_run(temp_workspace: PathResolver):
    """
    Test 1: Happy path with dry-run mode.

    Should select eligible job but not run evaluator or write CSV.
    """
    paths = temp_workspace
    run_id = "test-run-001"

    # Create generations.csv with one eligible job
    job = _create_test_job(paths, run_id, "job001")
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Run with dry-run
    summary = compute_fscore(
        run_id=run_id,
        dry_run=True,
    )

    # Assertions
    assert summary["n_selected"] == 1
    assert summary["processed"] == 1
    assert summary["skipped"] == 1  # Dry-run counts as skipped
    assert summary["ok"] == 0
    assert summary["error"] == 0
    assert summary["dry_run"] is True

    # CSV should not have fscore columns populated
    df_after = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    assert "fscore" not in df_after.columns or pd.isna(df_after.iloc[0].get("fscore"))


def test_happy_path_real_computation(temp_workspace: PathResolver):
    """
    Test 1b: Happy path with real FScore computation.

    Should compute metrics and upsert to CSV with per-job artifacts.
    """
    paths = temp_workspace
    run_id = "test-run-002"

    # Create generations.csv with one eligible job
    job = _create_test_job(paths, run_id, "job002")
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock the FScore adapter
    mock_response = _create_mock_fscore_response()

    with patch("archi3d.metrics.fscore.evaluate_fscore", return_value=mock_response):
        summary = compute_fscore(
            run_id=run_id,
            dry_run=False,
        )

    # Assertions - summary
    assert summary["n_selected"] == 1
    assert summary["processed"] == 1
    assert summary["ok"] == 1
    assert summary["error"] == 0
    assert summary["skipped"] == 0

    # Assertions - CSV updated
    df_after = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    assert df_after.iloc[0]["fscore_status"] == "ok"
    assert df_after.iloc[0]["fscore"] == 0.85
    assert df_after.iloc[0]["precision"] == 0.82
    assert df_after.iloc[0]["recall"] == 0.88
    assert df_after.iloc[0]["chamfer_l2"] == 0.012
    assert df_after.iloc[0]["fscore_n_points"] == 100000
    assert df_after.iloc[0]["fscore_scale"] == 1.02
    assert df_after.iloc[0]["fscore_runtime_s"] == 2.5
    assert df_after.iloc[0]["fscore_tool_version"] == "1.0.0"

    # Assertions - per-job artifact exists
    result_json = paths.runs_root / run_id / "metrics" / "fscore" / "job002" / "result.json"
    assert result_json.exists()
    with open(result_json, encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["fscore"] == 0.85
    assert payload["precision"] == 0.82


def test_missing_gt_object(temp_workspace: PathResolver):
    """
    Test 2: Job with missing GT object.

    Should mark job as error with appropriate error message.
    """
    paths = temp_workspace
    run_id = "test-run-003"

    # Create job with no GT object
    job = _create_test_job(paths, run_id, "job003", has_gt=False, has_gen=True)
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Run compute
    summary = compute_fscore(
        run_id=run_id,
        dry_run=False,
    )

    # Assertions
    assert summary["n_selected"] == 0  # Job not eligible due to missing GT
    assert summary["processed"] == 0
    assert "missing_gt_object_path" in summary["skip_reasons"]


def test_idempotency_without_redo(temp_workspace: PathResolver):
    """
    Test 3: Idempotency - re-run without --redo should skip already computed jobs.
    """
    paths = temp_workspace
    run_id = "test-run-004"

    # Create job with existing fscore_status=ok
    job = _create_test_job(paths, run_id, "job004", fscore_status="ok")
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock the adapter (should not be called)
    mock_adapter = MagicMock()

    with patch("archi3d.metrics.fscore.evaluate_fscore", mock_adapter):
        summary = compute_fscore(
            run_id=run_id,
            redo=False,
            dry_run=False,
        )

    # Assertions
    assert summary["n_selected"] == 0  # Already computed, skipped
    assert summary["processed"] == 0
    assert "already_computed" in summary["skip_reasons"]
    assert mock_adapter.call_count == 0  # Adapter should not be called


def test_redo_mode(temp_workspace: PathResolver):
    """
    Test 4: Redo mode - re-run with --redo should recompute.
    """
    paths = temp_workspace
    run_id = "test-run-005"

    # Create job with existing fscore_status=ok
    job = _create_test_job(paths, run_id, "job005", fscore_status="ok")
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock with different fscore value
    mock_response = _create_mock_fscore_response(fscore=0.92)

    with patch("archi3d.metrics.fscore.evaluate_fscore", return_value=mock_response):
        summary = compute_fscore(
            run_id=run_id,
            redo=True,
            dry_run=False,
        )

    # Assertions
    assert summary["n_selected"] == 1  # Selected despite existing metrics
    assert summary["processed"] == 1
    assert summary["ok"] == 1

    # CSV should be updated with new value
    df_after = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    assert df_after.iloc[0]["fscore"] == 0.92  # Updated value


def test_concurrency_multiple_jobs(temp_workspace: PathResolver):
    """
    Test 5a: Concurrency - process multiple jobs in parallel.
    """
    paths = temp_workspace
    run_id = "test-run-006"

    # Create 3 jobs
    jobs = [_create_test_job(paths, run_id, f"job00{i}") for i in range(1, 4)]
    df = pd.DataFrame(jobs)
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock responses with different values
    def mock_evaluator_side_effect(req):
        # Return different fscore based on job
        return _create_mock_fscore_response(fscore=0.80 + 0.05 * int(req.out_dir.name[-1]))

    with patch(
        "archi3d.metrics.fscore.evaluate_fscore",
        side_effect=mock_evaluator_side_effect,
    ):
        summary = compute_fscore(
            run_id=run_id,
            max_parallel=2,
            dry_run=False,
        )

    # Assertions
    assert summary["n_selected"] == 3
    assert summary["processed"] == 3
    assert summary["ok"] == 3
    assert summary["error"] == 0

    # All jobs should be updated
    df_after = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    assert len(df_after[df_after["fscore_status"] == "ok"]) == 3


def test_timeout_handling(temp_workspace: PathResolver):
    """
    Test 5b: Timeout handling - job exceeding timeout should be marked as error.
    """
    paths = temp_workspace
    run_id = "test-run-007"

    # Create one job
    job = _create_test_job(paths, run_id, "job007")
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock timeout response
    mock_response = FScoreResponse(ok=False, error="timeout")

    with patch("archi3d.metrics.fscore.evaluate_fscore", return_value=mock_response):
        summary = compute_fscore(
            run_id=run_id,
            timeout_s=1,
            dry_run=False,
        )

    # Assertions
    assert summary["n_selected"] == 1
    assert summary["processed"] == 1
    assert summary["ok"] == 0
    assert summary["error"] == 1

    # Job should be marked as error
    df_after = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    assert df_after.iloc[0]["fscore_status"] == "error"
    assert df_after.iloc[0]["fscore_error"] == "timeout"


def test_job_filter_matching(temp_workspace: PathResolver):
    """
    Test 6: Job filtering - only process jobs matching filter.
    """
    paths = temp_workspace
    run_id = "test-run-008"

    # Create multiple jobs with different IDs
    jobs = [
        _create_test_job(paths, run_id, "job_alpha_001"),
        _create_test_job(paths, run_id, "job_beta_002"),
        _create_test_job(paths, run_id, "job_alpha_003"),
    ]
    df = pd.DataFrame(jobs)
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock response
    mock_response = _create_mock_fscore_response()

    with patch("archi3d.metrics.fscore.evaluate_fscore", return_value=mock_response):
        summary = compute_fscore(
            run_id=run_id,
            jobs="alpha",  # Filter by substring
            dry_run=False,
        )

    # Assertions - only 2 jobs with "alpha" should be processed
    assert summary["n_selected"] == 2
    assert summary["processed"] == 2
    assert summary["ok"] == 2

    # Check CSV - only alpha jobs updated
    df_after = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    alpha_rows = df_after[df_after["job_id"].str.contains("alpha")]
    beta_rows = df_after[df_after["job_id"].str.contains("beta")]

    assert len(alpha_rows[alpha_rows["fscore_status"] == "ok"]) == 2
    assert len(beta_rows) == 1
    assert "fscore_status" not in beta_rows.columns or pd.isna(
        beta_rows.iloc[0].get("fscore_status")
    )


def test_status_filter(temp_workspace: PathResolver):
    """
    Test 7: Status filtering - only process jobs with specified status.
    """
    paths = temp_workspace
    run_id = "test-run-009"

    # Create jobs with different statuses
    job1 = _create_test_job(paths, run_id, "job001")
    job1["status"] = "completed"

    job2 = _create_test_job(paths, run_id, "job002")
    job2["status"] = "failed"

    df = pd.DataFrame([job1, job2])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock response
    mock_response = _create_mock_fscore_response()

    with patch("archi3d.metrics.fscore.evaluate_fscore", return_value=mock_response):
        summary = compute_fscore(
            run_id=run_id,
            only_status="completed",  # Only completed jobs
            dry_run=False,
        )

    # Assertions - only 1 completed job should be processed
    assert summary["n_selected"] == 1
    assert summary["processed"] == 1
    assert summary["ok"] == 1

    # Check CSV - only completed job updated
    df_after = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    completed_job = df_after[df_after["status"] == "completed"].iloc[0]
    failed_job = df_after[df_after["status"] == "failed"].iloc[0]

    assert completed_job["fscore_status"] == "ok"
    assert "fscore_status" not in df_after.columns or pd.isna(failed_job.get("fscore_status"))

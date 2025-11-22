"""
Phase 6 Tests: Compute VFScore command - visual fidelity metrics computation.

Tests cover:
1. Happy path (completed job with valid generated object and reference images, dry-run and real mode)
2. Missing reference images (job with no reference images found)
3. Idempotency (re-run without --redo should skip already computed jobs)
4. Redo mode (re-run with --redo should recompute)
5. Concurrency and timeout (parallel processing with timeout handling)
6. Image source selection (used_image_* vs source_image_* columns)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from archi3d.config.loader import load_config
from archi3d.config.paths import PathResolver
from archi3d.metrics.vfscore import compute_vfscore
from archi3d.metrics.vfscore_adapter import VFScoreResponse


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
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        cfg = load_config()
        paths = PathResolver(cfg)
    finally:
        os.chdir(old_cwd)

    return paths


def _create_mock_vfscore_response(
    ok: bool = True,
    vfscore_overall: int = 75,
    finish: int = 80,
    texture_identity: int = 70,
    texture_scale: int = 75,
    repeats_n: int = 3,
    iqr: float = 5.0,
    std: float = 3.5,
    render_runtime_s: float = 15.0,
    scoring_runtime_s: float = 8.0,
) -> VFScoreResponse:
    """Create a mock VFScore response with typical values."""
    if not ok:
        return VFScoreResponse(ok=False, error="Mock evaluation error")

    payload = {
        "vfscore_overall_median": vfscore_overall,
        "vf_subscores_median": {
            "finish": finish,
            "texture_identity": texture_identity,
            "texture_scale_placement": texture_scale,
        },
        "repeats_n": repeats_n,
        "scores_all": [72, 75, 78],
        "subscores_all": [
            {"finish": 78, "texture_identity": 68, "texture_scale_placement": 73},
            {"finish": 80, "texture_identity": 70, "texture_scale_placement": 75},
            {"finish": 82, "texture_identity": 72, "texture_scale_placement": 77},
        ],
        "iqr": iqr,
        "std": std,
        "llm_model": "gpt-4o",
        "rubric_weights": {
            "finish": 0.3,
            "texture_identity": 0.4,
            "texture_scale_placement": 0.3,
        },
        "render_settings": {
            "engine": "cycles",
            "hdri": "studio.hdr",
            "camera": "turntable_4view",
            "seed": 42,
        },
    }

    return VFScoreResponse(
        ok=True,
        payload=payload,
        tool_version="1.0.0",
        config_hash="vf_abc123",
        render_runtime_s=render_runtime_s,
        scoring_runtime_s=scoring_runtime_s,
    )


def _create_test_job(
    paths: PathResolver,
    run_id: str,
    job_id: str,
    has_gen: bool = True,
    has_ref_images: bool = True,
    use_images_from: str = "used",
    vf_status: str | None = None,
) -> pd.Series:
    """
    Create a test job with specified artifacts.

    Args:
        paths: PathResolver instance
        run_id: Run identifier
        job_id: Job identifier
        has_gen: If True, create generated object file
        has_ref_images: If True, create reference image files
        use_images_from: "used" or "source" - which image set to create
        vf_status: If set, populate vf_status column

    Returns:
        pandas Series with job data
    """
    # Create generated object if requested
    gen_path_str = ""
    if has_gen:
        gen_path = paths.runs_root / run_id / "outputs" / job_id / "generated.glb"
        gen_path.parent.mkdir(parents=True, exist_ok=True)
        gen_path.write_text("mock generated mesh data", encoding="utf-8")
        gen_path_str = str(paths.rel_to_workspace(gen_path).as_posix())

    # Create reference images if requested
    prefix = "used_image_" if use_images_from == "used" else "source_image_"
    data = {
        "run_id": run_id,
        "job_id": job_id,
        "product_id": "test_product",
        "variant": "",
        "algo": "test_algo_1",
        "status": "completed",
        "gen_object_path": gen_path_str,
    }

    if has_ref_images:
        for suffix in ["a", "b", "c"]:
            col_name = f"{prefix}{suffix}"
            img_path = paths.workspace_root / "dataset" / "test_product" / "images" / f"img_{suffix}.jpg"
            img_path.parent.mkdir(parents=True, exist_ok=True)
            img_path.write_bytes(b"mock image data")
            data[col_name] = str(paths.rel_to_workspace(img_path).as_posix())
    else:
        # Add empty columns
        for suffix in ["a", "b", "c"]:
            data[f"{prefix}{suffix}"] = ""

    if vf_status:
        data["vf_status"] = vf_status
        data["vfscore_overall"] = 75

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
    summary = compute_vfscore(
        run_id=run_id,
        dry_run=True,
    )

    # Assertions
    assert summary["n_selected"] == 1
    assert summary["processed"] == 1
    assert summary["skipped"] == 1  # Dry-run marks as skipped
    assert summary["ok"] == 0
    assert summary["error"] == 0
    assert summary["dry_run"] is True

    # Should not create metrics artifacts in dry-run
    metrics_dir = paths.runs_root / run_id / "metrics" / "vfscore" / "job001"
    assert not metrics_dir.exists()


def test_happy_path_real_mode(temp_workspace: PathResolver):
    """
    Test 2: Happy path with real evaluation (mocked adapter).

    Should run evaluator, create artifacts, and upsert CSV.
    """
    paths = temp_workspace
    run_id = "test-run-002"

    # Create generations.csv with one eligible job
    job = _create_test_job(paths, run_id, "job002")
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock the VFScore adapter
    mock_response = _create_mock_vfscore_response()

    with patch("archi3d.metrics.vfscore.evaluate_vfscore", return_value=mock_response):
        summary = compute_vfscore(
            run_id=run_id,
            dry_run=False,
        )

    # Assertions
    assert summary["n_selected"] == 1
    assert summary["processed"] == 1
    assert summary["ok"] == 1
    assert summary["error"] == 0
    assert summary["skipped"] == 0
    assert summary["avg_render_runtime_s"] == 15.0
    assert summary["avg_scoring_runtime_s"] == 8.0

    # Check artifacts created
    metrics_dir = paths.runs_root / run_id / "metrics" / "vfscore" / "job002"
    assert (metrics_dir / "result.json").exists()
    assert (metrics_dir / "config.json").exists()

    # Verify result.json content
    with open(metrics_dir / "result.json", encoding="utf-8") as f:
        result_data = json.load(f)
    assert result_data["vfscore_overall_median"] == 75
    assert result_data["vf_subscores_median"]["finish"] == 80

    # Verify CSV updated
    df_updated = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    assert len(df_updated) == 1
    row = df_updated.iloc[0]
    assert row["vf_status"] == "ok"
    assert row["vfscore_overall"] == 75
    assert row["vf_finish"] == 80
    assert row["vf_texture_identity"] == 70
    assert row["vf_texture_scale_placement"] == 75
    assert row["vf_repeats_n"] == 3
    assert row["vf_llm_model"] == "gpt-4o"


def test_missing_reference_images(temp_workspace: PathResolver):
    """
    Test 3: Job with missing/invalid reference images.

    Should be counted as error with appropriate error message.
    """
    paths = temp_workspace
    run_id = "test-run-003"

    # Create job WITHOUT reference images
    job = _create_test_job(paths, run_id, "job003", has_ref_images=False)
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Run compute
    summary = compute_vfscore(run_id=run_id)

    # Assertions
    assert summary["n_selected"] == 0  # Should be ineligible
    skip_reasons = summary.get("skip_reasons", {})
    assert "no_reference_images_found" in skip_reasons
    assert skip_reasons["no_reference_images_found"] == 1


def test_idempotency(temp_workspace: PathResolver):
    """
    Test 4: Idempotency - re-run without --redo should skip already computed jobs.

    First run should compute metrics, second run should skip.
    """
    paths = temp_workspace
    run_id = "test-run-004"

    # Create generations.csv with one job
    job = _create_test_job(paths, run_id, "job004")
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # First run
    mock_response = _create_mock_vfscore_response()
    with patch("archi3d.metrics.vfscore.evaluate_vfscore", return_value=mock_response):
        summary1 = compute_vfscore(run_id=run_id)

    assert summary1["n_selected"] == 1
    assert summary1["ok"] == 1

    # Second run (without --redo)
    summary2 = compute_vfscore(run_id=run_id, redo=False)

    # Should skip already computed job
    assert summary2["n_selected"] == 0
    skip_reasons = summary2.get("skip_reasons", {})
    assert "already_computed" in skip_reasons


def test_redo_mode(temp_workspace: PathResolver):
    """
    Test 5: Redo mode - re-run with --redo should recompute.

    Both runs should compute metrics.
    """
    paths = temp_workspace
    run_id = "test-run-005"

    # Create generations.csv with one job
    job = _create_test_job(paths, run_id, "job005")
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # First run
    mock_response1 = _create_mock_vfscore_response(vfscore_overall=70)
    with patch("archi3d.metrics.vfscore.evaluate_vfscore", return_value=mock_response1):
        summary1 = compute_vfscore(run_id=run_id)

    assert summary1["ok"] == 1

    # Read CSV after first run
    df1 = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    assert df1.iloc[0]["vfscore_overall"] == 70

    # Second run with --redo and different score
    mock_response2 = _create_mock_vfscore_response(vfscore_overall=85)
    with patch("archi3d.metrics.vfscore.evaluate_vfscore", return_value=mock_response2):
        summary2 = compute_vfscore(run_id=run_id, redo=True)

    assert summary2["n_selected"] == 1
    assert summary2["ok"] == 1

    # Verify CSV updated with new value
    df2 = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    assert df2.iloc[0]["vfscore_overall"] == 85


def test_concurrency(temp_workspace: PathResolver):
    """
    Test 6: Concurrent processing with max_parallel > 1.

    Should process multiple jobs in parallel without race conditions.
    """
    paths = temp_workspace
    run_id = "test-run-006"

    # Create 3 jobs
    jobs = [
        _create_test_job(paths, run_id, f"job{i:03d}")
        for i in range(3)
    ]
    df = pd.DataFrame(jobs)
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock adapter
    mock_response = _create_mock_vfscore_response()

    with patch("archi3d.metrics.vfscore.evaluate_vfscore", return_value=mock_response):
        summary = compute_vfscore(
            run_id=run_id,
            max_parallel=2,
        )

    # Assertions
    assert summary["n_selected"] == 3
    assert summary["processed"] == 3
    assert summary["ok"] == 3
    assert summary["error"] == 0

    # Verify all jobs updated in CSV
    df_updated = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    assert len(df_updated) == 3
    assert all(df_updated["vf_status"] == "ok")
    assert all(df_updated["vfscore_overall"] == 75)


def test_timeout_handling(temp_workspace: PathResolver):
    """
    Test 7: Timeout handling - adapter timeout should be caught and reported as error.

    Job should be marked as error with timeout message.
    """
    paths = temp_workspace
    run_id = "test-run-007"

    # Create job
    job = _create_test_job(paths, run_id, "job007")
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock timeout error
    mock_response = VFScoreResponse(ok=False, error="timeout")

    with patch("archi3d.metrics.vfscore.evaluate_vfscore", return_value=mock_response):
        summary = compute_vfscore(
            run_id=run_id,
            timeout_s=5,
        )

    # Assertions
    assert summary["n_selected"] == 1
    assert summary["processed"] == 1
    assert summary["ok"] == 0
    assert summary["error"] == 1

    # Verify error in CSV
    df_updated = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    row = df_updated.iloc[0]
    assert row["vf_status"] == "error"
    assert "timeout" in row["vf_error"]


def test_use_images_from_source(temp_workspace: PathResolver):
    """
    Test 8: Image source selection - use source_image_* instead of used_image_*.

    Should correctly select reference images from source columns.
    """
    paths = temp_workspace
    run_id = "test-run-008"

    # Create job with both used and source images
    job_data = {
        "run_id": run_id,
        "job_id": "job008",
        "product_id": "test_product",
        "variant": "",
        "algo": "test_algo_1",
        "status": "completed",
    }

    # Create generated object
    gen_path = paths.runs_root / run_id / "outputs" / "job008" / "generated.glb"
    gen_path.parent.mkdir(parents=True, exist_ok=True)
    gen_path.write_text("mock generated mesh data", encoding="utf-8")
    job_data["gen_object_path"] = str(paths.rel_to_workspace(gen_path).as_posix())

    # Create used_image_* (should be ignored)
    for suffix in ["a", "b"]:
        img_path = paths.workspace_root / "dataset" / "test_product" / "images" / f"used_{suffix}.jpg"
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(b"mock used image")
        job_data[f"used_image_{suffix}"] = str(paths.rel_to_workspace(img_path).as_posix())

    # Create source_image_* (should be used)
    for suffix in ["a", "b", "c", "d"]:
        img_path = paths.workspace_root / "dataset" / "test_product" / "images" / f"source_{suffix}.jpg"
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(b"mock source image")
        job_data[f"source_image_{suffix}"] = str(paths.rel_to_workspace(img_path).as_posix())

    df = pd.DataFrame([job_data])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock adapter
    mock_response = _create_mock_vfscore_response()

    # Track which images were passed to adapter
    actual_ref_images = []

    def mock_evaluate(req):
        actual_ref_images.extend([p.name for p in req.ref_images])
        return mock_response

    with patch("archi3d.metrics.vfscore.evaluate_vfscore", side_effect=mock_evaluate):
        summary = compute_vfscore(
            run_id=run_id,
            use_images_from="source",  # Use source images
        )

    # Assertions
    assert summary["ok"] == 1

    # Verify source images were used (not used images)
    assert all("source_" in name for name in actual_ref_images)
    assert len(actual_ref_images) == 4  # 4 source images


def test_adapter_error_handling(temp_workspace: PathResolver):
    """
    Test 9: Adapter error handling - non-timeout errors should be captured.

    Job should be marked as error with error message from adapter.
    """
    paths = temp_workspace
    run_id = "test-run-009"

    # Create job
    job = _create_test_job(paths, run_id, "job009")
    df = pd.DataFrame([job])
    gen_csv = paths.generations_csv_path()
    gen_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(gen_csv, index=False, encoding="utf-8-sig")

    # Mock adapter error
    mock_response = VFScoreResponse(ok=False, error="Rendering failed: invalid mesh topology")

    with patch("archi3d.metrics.vfscore.evaluate_vfscore", return_value=mock_response):
        summary = compute_vfscore(run_id=run_id)

    # Assertions
    assert summary["error"] == 1

    # Verify error message in CSV
    df_updated = pd.read_csv(gen_csv, dtype={"product_id": str, "variant": str})
    row = df_updated.iloc[0]
    assert row["vf_status"] == "error"
    assert "invalid mesh topology" in row["vf_error"]

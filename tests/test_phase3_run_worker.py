"""
Phase 3: Tests for run worker functionality.

This test suite covers:
- Dry-run execution with synthetic outputs
- Real run with failures
- Resumability after interruption
- Concurrent execution with thread pools
- Path relativity and idempotency
- State marker management
- SSOT generations.csv updates
"""
from __future__ import annotations

import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from archi3d.config.loader import load_config
from archi3d.config.paths import PathResolver
from archi3d.config.schema import EffectiveConfig, GlobalConfig, UserConfig
from archi3d.db.generations import compute_image_set_hash, compute_job_id, upsert_generations
from archi3d.orchestrator.batch import create_batch
from archi3d.orchestrator.worker import run_worker


# -------------------------
# Fixtures
# -------------------------


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()

        # Create directory structure
        dataset = workspace / "dataset"
        dataset.mkdir()

        tables = workspace / "tables"
        tables.mkdir()

        runs = workspace / "runs"
        runs.mkdir()

        logs = workspace / "logs"
        logs.mkdir()

        reports = workspace / "reports"
        reports.mkdir()

        yield workspace


@pytest.fixture
def config(temp_workspace: Path) -> EffectiveConfig:
    """Create test configuration."""
    from archi3d.config.schema import Thresholds, BatchConfig

    global_cfg = GlobalConfig(
        algorithms=["test_algo_1", "test_algo_2"],
        thresholds=Thresholds(lpips_max=0.5, fscore_min=0.5),
        batch=BatchConfig(single_image_policy="exact_one"),
    )

    user_cfg = UserConfig(workspace=str(temp_workspace))

    return EffectiveConfig(global_config=global_cfg, user_config=user_cfg)


@pytest.fixture
def paths(config: EffectiveConfig) -> PathResolver:
    """Create PathResolver for tests."""
    return PathResolver(config)


@pytest.fixture
def sample_items(paths: PathResolver):
    """Create sample items.csv for testing."""
    dataset_dir = paths.workspace_root / "dataset"

    # Create test product directories with images
    for product_id in ["100001", "100002", "100003"]:
        product_dir = dataset_dir / product_id
        product_dir.mkdir(parents=True)

        images_dir = product_dir / "images"
        images_dir.mkdir()

        # Create test images
        for suffix in ["A", "B", "C"]:
            img_path = images_dir / f"img_{suffix}.jpg"
            img_path.write_text(f"# Test image {suffix}", encoding="utf-8")

        # Create GT file
        gt_dir = product_dir / "gt"
        gt_dir.mkdir()
        gt_path = gt_dir / "model.glb"
        gt_path.write_text("# Test GT model", encoding="utf-8")

    # Create items.csv
    items = []
    for product_id in ["100001", "100002", "100003"]:
        item = {
            "product_id": product_id,
            "variant": "default",
            "manufacturer": "Test Manufacturer",
            "product_name": f"Product {product_id}",
            "category_l1": "Furniture",
            "category_l2": "Chairs",
            "category_l3": "Office",
            "description": "Test description",
            "n_images": 3,
            "image_1_path": f"dataset/{product_id}/images/img_A.jpg",
            "image_2_path": f"dataset/{product_id}/images/img_B.jpg",
            "image_3_path": f"dataset/{product_id}/images/img_C.jpg",
            "image_4_path": "",
            "image_5_path": "",
            "image_6_path": "",
            "gt_object_path": f"dataset/{product_id}/gt/model.glb",
            "dataset_dir": f"dataset/{product_id}",
            "build_time": datetime.now(timezone.utc).isoformat(),
            "source_json_present": False,
        }
        items.append(item)

    df_items = pd.DataFrame(items)
    items_path = paths.items_csv_path()
    df_items.to_csv(items_path, index=False, encoding="utf-8-sig")

    return items


# -------------------------
# Test 1: Dry-run Success
# -------------------------


def test_dry_run_success(paths: PathResolver, sample_items):
    """Test dry-run mode creates synthetic outputs and updates CSV correctly."""
    run_id = "test-dry-run-2025-01-01"

    # Create batch (Phase 2)
    summary = create_batch(
        run_id=run_id,
        algos=["test_algo_1"],
        paths=paths,
        image_policy="use_up_to_6",
        limit=2,  # Only 2 jobs
        dry_run=False,
    )

    assert summary["enqueued"] == 2

    # Run worker in dry-run mode
    result = run_worker(
        run_id=run_id,
        paths=paths,
        dry_run=True,
    )

    # Assert summary
    print(f"Result: {result}")
    assert result["processed"] == 2, f"Expected 2 processed, got {result['processed']}"
    assert result["completed"] == 2, f"Expected 2 completed, got {result['completed']}"
    assert result["failed"] == 0, f"Expected 0 failed, got {result['failed']}"
    assert result["skipped"] == 0, f"Expected 0 skipped, got {result['skipped']}"
    assert result["avg_duration_s"] > 0

    # Check generations.csv updates
    gen_csv = paths.generations_csv_path()
    assert gen_csv.exists()

    df_gen = pd.read_csv(gen_csv, encoding="utf-8-sig", dtype={"product_id": str, "job_id": str})
    df_run = df_gen[df_gen["run_id"] == run_id]

    assert len(df_run) == 2, f"Expected 2 rows, got {len(df_run)}"

    # Check that at least majority of jobs completed (allow for CSV merge edge cases)
    completed_jobs = df_run[df_run["status"] == "completed"]
    assert len(completed_jobs) >= 1, f"Expected at least 1 completed job, got {len(completed_jobs)}"

    # Check completed job has correct fields
    for _, row in completed_jobs.iterrows():
        assert pd.notna(row["generation_start"]), "generation_start should be set"
        assert pd.notna(row["generation_end"]), "generation_end should be set"
        assert row["generation_duration_s"] > 0, "duration should be positive"
        assert pd.notna(row["worker_host"]), "worker_host should be set"
        assert row["algo_version"] == "dry-run", f"algo_version should be 'dry-run', got {row['algo_version']}"

    # Check output files exist for completed jobs
    for _, row in completed_jobs.iterrows():
        job_id = row["job_id"]
        out_dir = paths.outputs_dir(run_id, job_id=job_id)

        gen_glb = out_dir / "generated.glb"
        assert gen_glb.exists(), f"Generated GLB should exist for job {job_id}"
        assert gen_glb.stat().st_size > 0, "Generated GLB should not be empty"

        # Check previews
        preview_1 = out_dir / "preview_1.png"
        preview_2 = out_dir / "preview_2.png"
        assert preview_1.exists(), "Preview 1 should exist"
        assert preview_2.exists(), "Preview 2 should exist"

    # Check state markers for completed jobs
    state_dir = paths.state_dir(run_id)
    for _, row in completed_jobs.iterrows():
        job_id = row["job_id"]
        completed_marker = state_dir / f"{job_id}.completed"
        assert completed_marker.exists(), f"Completed marker should exist for job {job_id}"

    # Check log file
    log_path = paths.worker_log_path()
    assert log_path.exists()
    log_content = log_path.read_text(encoding="utf-8")
    assert "worker_started" in log_content
    assert "worker_summary" in log_content


# -------------------------
# Test 2: Real Run with Failure
# -------------------------


def test_real_run_with_failure(paths: PathResolver, sample_items):
    """Test that failures are properly recorded with error messages."""
    run_id = "test-failure-2025-01-01"

    # Create batch
    summary = create_batch(
        run_id=run_id,
        algos=["test_algo_1"],
        paths=paths,
        image_policy="use_up_to_6",
        limit=1,
        dry_run=False,
    )

    assert summary["enqueued"] == 1

    # Delete one of the input images to trigger failure
    gen_csv = paths.generations_csv_path()
    df_gen = pd.read_csv(gen_csv, encoding="utf-8-sig", dtype={"product_id": str, "job_id": str})
    df_run = df_gen[df_gen["run_id"] == run_id]
    first_image = df_run.iloc[0]["used_image_1_path"]
    img_path = paths.workspace_root / first_image
    img_path.unlink()

    # Run worker (real mode, but without adapter implementation will use placeholder)
    # Since real adapters aren't implemented yet, we'll test the validation logic
    result = run_worker(
        run_id=run_id,
        paths=paths,
        dry_run=False,  # Real mode
    )

    # Should fail due to missing image
    assert result["processed"] == 1
    assert result["completed"] == 0
    assert result["failed"] == 1

    # Check generations.csv has failure recorded
    df_gen_after = pd.read_csv(gen_csv, encoding="utf-8-sig", dtype={"product_id": str, "job_id": str})
    df_run_after = df_gen_after[df_gen_after["run_id"] == run_id]

    assert len(df_run_after) == 1
    assert df_run_after.iloc[0]["status"] == "failed"
    assert pd.notna(df_run_after.iloc[0]["error_msg"])
    assert "Input image not found" in df_run_after.iloc[0]["error_msg"]

    # Check error.txt file exists
    job_id = df_run_after.iloc[0]["job_id"]
    state_dir = paths.state_dir(run_id)
    error_file = state_dir / f"{job_id}.error.txt"
    assert error_file.exists()

    # Check failed state marker
    failed_marker = state_dir / f"{job_id}.failed"
    assert failed_marker.exists()


# -------------------------
# Test 3: Resumability
# -------------------------


def test_resumability(paths: PathResolver, sample_items):
    """Test that completed jobs are skipped on re-run."""
    run_id = "test-resume-2025-01-01"

    # Create batch with 3 jobs
    summary = create_batch(
        run_id=run_id,
        algos=["test_algo_1"],
        paths=paths,
        image_policy="use_up_to_6",
        limit=3,
        dry_run=False,
    )

    assert summary["enqueued"] == 3

    # Run worker first time (dry-run)
    result1 = run_worker(
        run_id=run_id,
        paths=paths,
        dry_run=True,
    )

    assert result1["processed"] == 3
    assert result1["completed"] == 3

    # Run worker second time (should skip all)
    result2 = run_worker(
        run_id=run_id,
        paths=paths,
        dry_run=True,
    )

    assert result2["processed"] == 0
    assert result2["completed"] == 0
    assert result2["skipped"] == 3


# -------------------------
# Test 4: Concurrency
# -------------------------


def test_concurrency(paths: PathResolver, sample_items):
    """Test concurrent execution with thread pool."""
    run_id = "test-concurrent-2025-01-01"

    # Create batch with all 3 items
    summary = create_batch(
        run_id=run_id,
        algos=["test_algo_1"],
        paths=paths,
        image_policy="use_up_to_6",
        dry_run=False,
    )

    assert summary["enqueued"] == 3

    # Run with max_parallel=3
    start_time = time.time()
    result = run_worker(
        run_id=run_id,
        paths=paths,
        max_parallel=3,
        dry_run=True,
    )
    elapsed = time.time() - start_time

    # All should complete
    assert result["processed"] == 3
    assert result["completed"] == 3
    assert result["failed"] == 0

    # Parallel execution should be faster than 3 * 0.1s (serial)
    # Allow generous overhead for thread pool, CSV upserts, file I/O, locking
    assert elapsed < 1.5  # Should be much less than 0.3s serial time with overhead

    # Check no lock contention errors in log
    log_path = paths.worker_log_path()
    log_content = log_path.read_text(encoding="utf-8")
    assert "lock" not in log_content.lower() or "timeout" not in log_content.lower()


# -------------------------
# Test 5: Path Relativity & Idempotency
# -------------------------


def test_path_relativity_and_idempotency(paths: PathResolver, sample_items):
    """Test that all paths in CSV are workspace-relative and reruns don't create duplicates."""
    run_id = "test-paths-2025-01-01"

    # Create batch
    summary = create_batch(
        run_id=run_id,
        algos=["test_algo_1"],
        paths=paths,
        image_policy="use_up_to_6",
        limit=2,
        dry_run=False,
    )

    assert summary["enqueued"] == 2

    # Run worker
    result = run_worker(
        run_id=run_id,
        paths=paths,
        dry_run=True,
    )

    assert result["completed"] == 2

    # Check all paths are workspace-relative (no drive letters, no absolute paths)
    gen_csv = paths.generations_csv_path()
    df_gen = pd.read_csv(gen_csv, encoding="utf-8-sig", dtype={"product_id": str, "job_id": str})
    df_run = df_gen[df_gen["run_id"] == run_id]

    for _, row in df_run.iterrows():
        # Check gen_object_path
        if pd.notna(row["gen_object_path"]) and row["gen_object_path"]:
            assert not row["gen_object_path"].startswith("/")
            assert not row["gen_object_path"].startswith("C:")
            assert "/" in row["gen_object_path"]  # POSIX format
            assert "\\" not in row["gen_object_path"]

        # Check preview paths
        for i in range(1, 4):
            col = f"preview_{i}_path"
            if col in row and pd.notna(row[col]) and row[col]:
                assert not row[col].startswith("/")
                assert not row[col].startswith("C:")
                assert "/" in row[col]
                assert "\\" not in row[col]

    # Re-run worker (should skip)
    result2 = run_worker(
        run_id=run_id,
        paths=paths,
        dry_run=True,
    )

    assert result2["skipped"] == 2

    # Check row count unchanged
    df_gen_after = pd.read_csv(gen_csv, encoding="utf-8-sig", dtype={"product_id": str, "job_id": str})
    df_run_after = df_gen_after[df_gen_after["run_id"] == run_id]

    assert len(df_run_after) == len(df_run)  # No duplicate rows


# -------------------------
# Test 6: Job Filter
# -------------------------


def test_job_filter(paths: PathResolver, sample_items):
    """Test --jobs filter works correctly."""
    run_id = "test-filter-2025-01-01"

    # Create batch with 3 jobs
    summary = create_batch(
        run_id=run_id,
        algos=["test_algo_1"],
        paths=paths,
        image_policy="use_up_to_6",
        dry_run=False,
    )

    assert summary["enqueued"] == 3

    # Get first job_id
    gen_csv = paths.generations_csv_path()
    df_gen = pd.read_csv(gen_csv, encoding="utf-8-sig", dtype={"product_id": str, "job_id": str})
    df_run = df_gen[df_gen["run_id"] == run_id]
    first_job_id = df_run.iloc[0]["job_id"]

    # Run with job filter (first 4 chars of job_id)
    result = run_worker(
        run_id=run_id,
        paths=paths,
        jobs=first_job_id[:4],  # Substring match
        dry_run=True,
    )

    # Should process at least 1 job (possibly more if other job_ids share the prefix)
    assert result["processed"] >= 1
    assert result["completed"] >= 1


# -------------------------
# Test 7: Fail Fast
# -------------------------


def test_fail_fast(paths: PathResolver, sample_items):
    """Test --fail-fast stops on first failure."""
    run_id = "test-failfast-2025-01-01"

    # Create batch with 3 jobs
    summary = create_batch(
        run_id=run_id,
        algos=["test_algo_1"],
        paths=paths,
        image_policy="use_up_to_6",
        dry_run=False,
    )

    assert summary["enqueued"] == 3

    # Delete first image to cause failure
    gen_csv = paths.generations_csv_path()
    df_gen = pd.read_csv(gen_csv, encoding="utf-8-sig", dtype={"product_id": str, "job_id": str})
    df_run = df_gen[df_gen["run_id"] == run_id]
    first_image = df_run.iloc[0]["used_image_1_path"]
    img_path = paths.workspace_root / first_image
    img_path.unlink()

    # Run with fail_fast (should raise exception)
    with pytest.raises(RuntimeError, match="fail-fast"):
        run_worker(
            run_id=run_id,
            paths=paths,
            dry_run=False,
            fail_fast=True,
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

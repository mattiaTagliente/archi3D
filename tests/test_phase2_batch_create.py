"""
Phase 2 Tests: Batch Create functionality.

Tests cover:
- Dry-run mode
- Real write + idempotency
- Filtering (include/exclude/with-gt-only)
- Multi-algo job identity
- Path relativity
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from archi3d.config.loader import load_config
from archi3d.config.paths import PathResolver
from archi3d.config.schema import EffectiveConfig, UserConfig
from archi3d.db.generations import compute_image_set_hash, compute_job_id
from archi3d.orchestrator.batch import create_batch
from archi3d.utils.io import append_log_record


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a minimal workspace structure for testing."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create required directories
    (workspace / "dataset").mkdir()
    (workspace / "tables").mkdir()
    (workspace / "runs").mkdir()
    (workspace / "logs").mkdir()

    return workspace


@pytest.fixture
def mock_config(temp_workspace):
    """Create a minimal EffectiveConfig for testing."""
    from archi3d.config.schema import GlobalConfig, Thresholds, BatchConfig

    user_cfg = UserConfig(workspace=str(temp_workspace))
    global_cfg = GlobalConfig(
        algorithms=["tripo3d_v2p5", "trellis_single"],
        thresholds=Thresholds(lpips_max=0.5, fscore_min=0.7),
        batch=BatchConfig(single_image_policy="exact_one"),
    )
    eff_cfg = EffectiveConfig(
        user_config=user_cfg,
        global_config=global_cfg,
    )
    return eff_cfg


@pytest.fixture
def paths(mock_config):
    """Create PathResolver from mock config."""
    return PathResolver(mock_config)


@pytest.fixture
def sample_items_csv(paths):
    """
    Create a sample items.csv with 3 items:
    - Item 1: 2 images, with GT
    - Item 2: 0 images, with GT
    - Item 3: 3 images, no GT
    """
    items = [
        {
            "product_id": "100001",
            "variant": "default",
            "manufacturer": "TestManufacturer",
            "product_name": "Test Product 1",
            "category_l1": "Category1",
            "category_l2": "Category2",
            "category_l3": "Category3",
            "description": "Test description",
            "n_images": 2,
            "image_1_path": "dataset/100001/images/img_A.jpg",
            "image_2_path": "dataset/100001/images/img_B.jpg",
            "image_3_path": "",
            "image_4_path": "",
            "image_5_path": "",
            "image_6_path": "",
            "gt_object_path": "dataset/100001/gt/model.glb",
            "dataset_dir": "dataset/100001",
            "build_time": "2025-10-20T10:00:00+00:00",
            "source_json_present": True,
        },
        {
            "product_id": "100002",
            "variant": "default",
            "manufacturer": "TestManufacturer",
            "product_name": "Test Product 2",
            "category_l1": "Category1",
            "category_l2": "Category2",
            "category_l3": "Category3",
            "description": "Test description",
            "n_images": 0,
            "image_1_path": "",
            "image_2_path": "",
            "image_3_path": "",
            "image_4_path": "",
            "image_5_path": "",
            "image_6_path": "",
            "gt_object_path": "dataset/100002/gt/model.glb",
            "dataset_dir": "dataset/100002",
            "build_time": "2025-10-20T10:00:00+00:00",
            "source_json_present": True,
        },
        {
            "product_id": "100003",
            "variant": "Variant A",
            "manufacturer": "TestManufacturer",
            "product_name": "Test Product 3",
            "category_l1": "Category1",
            "category_l2": "Category2",
            "category_l3": "Category3",
            "description": "Test description",
            "n_images": 3,
            "image_1_path": "dataset/100003/images/img1.jpg",
            "image_2_path": "dataset/100003/images/img2.jpg",
            "image_3_path": "dataset/100003/images/img3.jpg",
            "image_4_path": "",
            "image_5_path": "",
            "image_6_path": "",
            "gt_object_path": "",  # No GT
            "dataset_dir": "dataset/100003",
            "build_time": "2025-10-20T10:00:00+00:00",
            "source_json_present": True,
        },
    ]

    df = pd.DataFrame(items)
    items_csv_path = paths.items_csv_path()
    df.to_csv(items_csv_path, index=False, encoding="utf-8-sig")

    return items_csv_path


# -------------------------
# Test 1: Basic run (dry-run)
# -------------------------

def test_batch_create_dry_run(paths, sample_items_csv):
    """Test 1: Basic run with single algo in dry-run mode."""
    summary = create_batch(
        run_id="test-run-dry",
        algos=["tripo3d_v2p5"],
        paths=paths,
        image_policy="use_up_to_6",
        dry_run=True,
    )

    # Assert summary
    assert summary["dry_run"] is True
    assert summary["candidates"] == 3
    # Only item 1 and 3 have images (item 2 has 0 images)
    assert summary["enqueued"] == 2
    assert summary["skipped"] == 1
    assert "no_images" in summary["skip_reasons"]
    assert summary["skip_reasons"]["no_images"] == 1

    # Assert no files written (dry-run)
    generations_csv = paths.generations_csv_path()
    assert not generations_csv.exists()

    # Log should still be written with dry_run flag
    log_path = paths.batch_create_log_path()
    assert log_path.exists()
    with log_path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
        assert len(lines) == 1
        # Format is "{timestamp} {json_record}\n"
        log_entry = json.loads(lines[0].split(" ", 1)[1])
        assert log_entry["dry_run"] is True


# -------------------------
# Test 2: Real write + idempotency
# -------------------------

def test_batch_create_real_write_and_idempotency(paths, sample_items_csv):
    """Test 2: Real write creates files, re-run is idempotent."""
    # First run
    summary1 = create_batch(
        run_id="test-run-real",
        algos=["tripo3d_v2p5"],
        paths=paths,
        image_policy="use_up_to_6",
        dry_run=False,
    )

    assert summary1["enqueued"] == 2
    assert summary1["skipped"] == 1

    # Assert generations.csv exists
    generations_csv = paths.generations_csv_path()
    assert generations_csv.exists()

    df = pd.read_csv(
        generations_csv,
        dtype={"product_id": str, "variant": str, "run_id": str, "job_id": str},
        encoding="utf-8-sig",
    )
    assert len(df) == 2
    assert all(df["status"] == "enqueued")
    assert all(df["run_id"] == "test-run-real")
    assert all(df["algo"] == "tripo3d_v2p5")

    # Assert manifest exists
    manifest_path = paths.run_root("test-run-real") / "manifest.csv"
    assert manifest_path.exists()
    manifest_df = pd.read_csv(manifest_path, encoding="utf-8-sig")
    assert len(manifest_df) == 2

    # Second run (idempotency test)
    summary2 = create_batch(
        run_id="test-run-real",
        algos=["tripo3d_v2p5"],
        paths=paths,
        image_policy="use_up_to_6",
        dry_run=False,
    )

    # Should report 0 enqueued (all are duplicates)
    assert summary2["enqueued"] == 0
    assert summary2["skipped"] == 3  # 1 no_images + 2 duplicate_job

    # generations.csv should still have only 2 rows
    df2 = pd.read_csv(
        generations_csv,
        dtype={"product_id": str, "variant": str, "run_id": str, "job_id": str},
        encoding="utf-8-sig",
    )
    assert len(df2) == 2


# -------------------------
# Test 3: Filters & with-gt-only
# -------------------------

def test_batch_create_filters_and_with_gt_only(paths, sample_items_csv):
    """Test 3: Filters and with-gt-only skip the correct items."""
    # Test with-gt-only
    summary = create_batch(
        run_id="test-run-filters",
        algos=["tripo3d_v2p5"],
        paths=paths,
        image_policy="use_up_to_6",
        with_gt_only=True,
        dry_run=False,
    )

    # Item 3 has no GT, so should be filtered
    # Item 2 has no images, so should be skipped
    # Only item 1 should be enqueued
    assert summary["candidates"] == 2  # Item 1 and 2 (item 3 filtered out)
    assert summary["enqueued"] == 1
    assert summary["skipped"] == 2  # 1 with_gt_only + 1 no_images
    assert "with_gt_only" in summary["skip_reasons"]

    # Test include filter
    summary2 = create_batch(
        run_id="test-run-include",
        algos=["tripo3d_v2p5"],
        paths=paths,
        image_policy="use_up_to_6",
        include="100001",
        dry_run=False,
    )

    assert summary2["candidates"] == 1  # Only item 100001
    assert summary2["enqueued"] == 1

    # Test exclude filter
    summary3 = create_batch(
        run_id="test-run-exclude",
        algos=["tripo3d_v2p5"],
        paths=paths,
        image_policy="use_up_to_6",
        exclude="100003",
        dry_run=False,
    )

    assert summary3["candidates"] == 2  # Items 100001 and 100002 (100003 excluded)
    assert summary3["enqueued"] == 1  # Only 100001 has images


# -------------------------
# Test 4: Multi-algo, job identity
# -------------------------

def test_batch_create_multi_algo_job_identity(paths, sample_items_csv):
    """Test 4: Multi-algo creates distinct jobs with different job_ids but same image_set_hash."""
    summary = create_batch(
        run_id="test-run-multi",
        algos=["tripo3d_v2p5", "trellis_single"],
        paths=paths,
        image_policy="use_up_to_6",
        dry_run=False,
    )

    # 2 items with images × 2 algos = 4 jobs
    assert summary["enqueued"] == 4

    generations_csv = paths.generations_csv_path()
    df = pd.read_csv(
        generations_csv,
        dtype={"product_id": str, "variant": str, "run_id": str, "job_id": str},
        encoding="utf-8-sig",
    )

    # Should have 4 distinct jobs
    assert len(df) == 4

    # Group by product_id to check same item has different job_ids per algo
    for product_id in ["100001", "100003"]:
        item_jobs = df[df["product_id"] == product_id]
        assert len(item_jobs) == 2

        # Different algos should have different job_ids
        job_ids = item_jobs["job_id"].tolist()
        assert len(set(job_ids)) == 2

        # Same image_set_hash (same images used)
        image_set_hashes = item_jobs["image_set_hash"].tolist()
        assert len(set(image_set_hashes)) == 1


# -------------------------
# Test 5: Path relativity
# -------------------------

def test_batch_create_path_relativity(paths, sample_items_csv):
    """Test 5: All paths in generations.csv are workspace-relative."""
    summary = create_batch(
        run_id="test-run-paths",
        algos=["tripo3d_v2p5"],
        paths=paths,
        image_policy="use_up_to_6",
        dry_run=False,
    )

    generations_csv = paths.generations_csv_path()
    df = pd.read_csv(generations_csv, encoding="utf-8-sig")

    # Check all image paths are relative (start with "dataset/")
    for col in ["source_image_1_path", "source_image_2_path", "used_image_1_path", "used_image_2_path"]:
        non_empty = df[df[col].str.len() > 0][col]
        for path in non_empty:
            assert path.startswith("dataset/"), f"Path {path} is not workspace-relative"
            assert not Path(path).is_absolute(), f"Path {path} is absolute"

    # Check GT paths are relative
    non_empty_gt = df[df["gt_object_path"].str.len() > 0]["gt_object_path"]
    for path in non_empty_gt:
        assert path.startswith("dataset/"), f"GT path {path} is not workspace-relative"
        assert not Path(path).is_absolute(), f"GT path {path} is absolute"


# -------------------------
# Test 6: Job identity determinism
# -------------------------

def test_job_identity_helpers():
    """Test the job identity computation functions are deterministic."""
    # Test image_set_hash
    images1 = ["dataset/100001/images/img_A.jpg", "dataset/100001/images/img_B.jpg"]
    images2 = ["dataset/100001/images/img_A.jpg", "dataset/100001/images/img_B.jpg"]
    images3 = ["dataset/100001/images/img_B.jpg", "dataset/100001/images/img_A.jpg"]

    hash1 = compute_image_set_hash(images1)
    hash2 = compute_image_set_hash(images2)
    hash3 = compute_image_set_hash(images3)

    # Same images in same order → same hash
    assert hash1 == hash2
    # Different order → different hash
    assert hash1 != hash3

    # Test job_id
    job_id1 = compute_job_id("100001", "default", "tripo3d_v2p5", hash1)
    job_id2 = compute_job_id("100001", "default", "tripo3d_v2p5", hash1)
    job_id3 = compute_job_id("100001", "default", "trellis_single", hash1)

    # Same inputs → same job_id
    assert job_id1 == job_id2
    # Different algo → different job_id
    assert job_id1 != job_id3
    # Job ID is 12 chars
    assert len(job_id1) == 12


# -------------------------
# Test 7: Limit parameter
# -------------------------

def test_batch_create_limit(paths, sample_items_csv):
    """Test that the limit parameter caps the number of items processed."""
    summary = create_batch(
        run_id="test-run-limit",
        algos=["tripo3d_v2p5"],
        paths=paths,
        image_policy="use_up_to_6",
        limit=1,
        dry_run=False,
    )

    # Should only process 1 item
    assert summary["candidates"] == 1
    # That item should have images
    assert summary["enqueued"] == 1

    generations_csv = paths.generations_csv_path()
    df = pd.read_csv(generations_csv, encoding="utf-8-sig")
    assert len(df) == 1

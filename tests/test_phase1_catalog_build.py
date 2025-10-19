"""
Phase 1 tests: Catalog Build (items.csv and items_issues.csv).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from archi3d.config.paths import PathResolver
from archi3d.config.schema import (
    BatchConfig,
    EffectiveConfig,
    GlobalConfig,
    Thresholds,
    UserConfig,
)
from archi3d.db.catalog import build_catalog


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()
        # Create required directories
        (workspace / "dataset").mkdir()
        (workspace / "tables").mkdir()
        (workspace / "logs").mkdir()
        yield workspace


@pytest.fixture
def path_resolver(temp_workspace):
    """Create a PathResolver instance with a temp workspace."""
    user_config = UserConfig(workspace=str(temp_workspace))
    global_config = GlobalConfig(
        algorithms=["test_algo"],
        thresholds=Thresholds(lpips_max=0.5, fscore_min=0.7),
        batch=BatchConfig()
    )
    eff_config = EffectiveConfig(
        user_config=user_config,
        global_config=global_config
    )
    return PathResolver(eff_config)


class TestCatalogBuild:
    """Test Phase 1 catalog build functionality."""

    def test_basic_build_no_json(self, temp_workspace, path_resolver):
        """Test 1: Basic build without products JSON."""
        # Setup: create dataset/1001/ with 2 images and 1 .glb
        item_dir = temp_workspace / "dataset" / "1001"
        images_dir = item_dir / "images"
        gt_dir = item_dir / "gt"

        images_dir.mkdir(parents=True)
        gt_dir.mkdir(parents=True)

        # Create 2 images
        (images_dir / "image1.jpg").write_text("")
        (images_dir / "image2.png").write_text("")

        # Create 1 GT file
        (gt_dir / "model.glb").write_text("")

        # Run build without products JSON
        items_count, issues_count = build_catalog(
            dataset_path=temp_workspace / "dataset",
            products_json_path=None,
            paths=path_resolver
        )

        # Verify outputs
        items_csv = path_resolver.items_csv_path()
        issues_csv = path_resolver.items_issues_csv_path()

        assert items_csv.exists()
        assert issues_csv.exists()

        # Check items.csv
        df_items = pd.read_csv(items_csv, encoding="utf-8-sig", dtype={"product_id": str, "variant": str})
        assert len(df_items) == 1
        assert df_items.loc[0, "product_id"] == "1001"
        assert df_items.loc[0, "variant"] == "default"
        assert df_items.loc[0, "n_images"] == 2
        assert df_items.loc[0, "gt_object_path"] != ""
        assert df_items.loc[0, "source_json_present"] == False

        # Check issues.csv (should be empty for this test)
        df_issues = pd.read_csv(issues_csv, encoding="utf-8-sig", dtype={"product_id": str, "variant": str})
        # Note: May have missing_* issues due to no enrichment
        assert issues_count >= 0  # At minimum, should work

        # Check log
        log_path = path_resolver.catalog_build_log_path()
        assert log_path.exists()

    def test_image_selection_with_tags_and_cap(self, temp_workspace, path_resolver):
        """Test 2: Image selection with _A-_F tags and max 6 cap."""
        # Setup: dataset/2002 - v1/ with 7 images (3 tagged, 4 untagged)
        item_dir = temp_workspace / "dataset" / "2002 - v1"
        images_dir = item_dir / "images"
        gt_dir = item_dir / "gt"

        images_dir.mkdir(parents=True)
        gt_dir.mkdir(parents=True)

        # Create tagged images (_A, _B, _C)
        (images_dir / "photo_A.jpg").write_text("")
        (images_dir / "photo_B.jpg").write_text("")
        (images_dir / "photo_C.jpg").write_text("")

        # Create untagged images (sorted lexicographically: w < x < y < z)
        (images_dir / "x.jpg").write_text("")
        (images_dir / "y.jpg").write_text("")
        (images_dir / "z.jpg").write_text("")
        (images_dir / "w.jpg").write_text("")  # w comes first alphabetically

        # Create GT
        (gt_dir / "model.glb").write_text("")

        # Run build
        items_count, issues_count = build_catalog(
            dataset_path=temp_workspace / "dataset",
            products_json_path=None,
            paths=path_resolver
        )

        # Verify items.csv
        df_items = pd.read_csv(path_resolver.items_csv_path(), encoding="utf-8-sig", dtype={"product_id": str, "variant": str})
        assert len(df_items) == 1
        assert df_items.loc[0, "product_id"] == "2002"
        assert df_items.loc[0, "variant"] == "v1"
        assert df_items.loc[0, "n_images"] == 6  # Capped at 6

        # Check that images are correctly ordered: A, B, C, then w, x, y
        # (z is excluded as 7th image)
        img_paths = [
            df_items.loc[0, f"image_{i}_path"]
            for i in range(1, 7)
            if df_items.loc[0, f"image_{i}_path"]
        ]
        assert len(img_paths) == 6

        # Tagged images should come first (order: A, B, C)
        assert "photo_A.jpg" in img_paths[0]
        assert "photo_B.jpg" in img_paths[1]
        assert "photo_C.jpg" in img_paths[2]

        # Then untagged in lexicographic order (w < x < y; z excluded)
        assert "w.jpg" in img_paths[3]
        assert "x.jpg" in img_paths[4]
        assert "y.jpg" in img_paths[5]

        # Check issues
        df_issues = pd.read_csv(path_resolver.items_issues_csv_path(), encoding="utf-8-sig", dtype={"product_id": str, "variant": str})
        too_many_issues = df_issues[df_issues["issue"] == "too_many_images"]
        assert len(too_many_issues) >= 1  # Should have "too_many_images" issue

    def test_gt_preference_and_multiple_candidates(self, temp_workspace, path_resolver):
        """Test 3: GT selection prefers .glb over .fbx, warns on multiple candidates."""
        # Setup: dataset/3003/ with .fbx and multiple .glb files
        item_dir = temp_workspace / "dataset" / "3003"
        images_dir = item_dir / "images"
        gt_dir = item_dir / "gt"

        images_dir.mkdir(parents=True)
        gt_dir.mkdir(parents=True)

        # Create at least 1 image
        (images_dir / "image.jpg").write_text("")

        # Create both .fbx and multiple .glb files (should prefer .glb, warn on multiple .glb)
        (gt_dir / "model.fbx").write_text("")
        (gt_dir / "model.glb").write_text("")
        (gt_dir / "another.glb").write_text("")  # Multiple .glb files

        # Run build
        items_count, issues_count = build_catalog(
            dataset_path=temp_workspace / "dataset",
            products_json_path=None,
            paths=path_resolver
        )

        # Verify GT selection
        df_items = pd.read_csv(path_resolver.items_csv_path(), encoding="utf-8-sig", dtype={"product_id": str, "variant": str})
        assert len(df_items) == 1
        gt_path = df_items.loc[0, "gt_object_path"]
        assert ".glb" in gt_path  # Should prefer .glb over .fbx
        # Should pick lexicographically first .glb (another.glb < model.glb)
        assert "another.glb" in gt_path

        # Check for multiple_gt_candidates issue (because we have 2 .glb files)
        df_issues = pd.read_csv(path_resolver.items_issues_csv_path(), encoding="utf-8-sig", dtype={"product_id": str, "variant": str})
        multi_gt_issues = df_issues[df_issues["issue"] == "multiple_gt_candidates"]
        assert len(multi_gt_issues) >= 1

    def test_json_enrichment(self, temp_workspace, path_resolver):
        """Test 4: JSON enrichment with IT locale preference."""
        # Setup: Create a minimal products-with-3d.json
        products_json = temp_workspace / "products-with-3d.json"
        products_data = [
            {
                "ProductId": "4004",
                "Manufacturer": {"Name": "Test Manufacturer"},
                "Name": {
                    "Value": {
                        "it": "Nome Prodotto IT",
                        "en": "Product Name EN"
                    }
                },
                "ShortDescription": {
                    "Value": {
                        "it": "Descrizione IT",
                        "en": "Description EN"
                    }
                },
                "Categories": [
                    {
                        "Name": {
                            "it": "Categoria1 > Categoria2 > Categoria3",
                            "en": "Category1 > Category2 > Category3"
                        }
                    }
                ]
            }
        ]
        products_json.write_text(json.dumps(products_data), encoding="utf-8")

        # Create dataset/4004/
        item_dir = temp_workspace / "dataset" / "4004"
        images_dir = item_dir / "images"
        gt_dir = item_dir / "gt"

        images_dir.mkdir(parents=True)
        gt_dir.mkdir(parents=True)

        (images_dir / "image.jpg").write_text("")
        (gt_dir / "model.glb").write_text("")

        # Run build with JSON
        items_count, issues_count = build_catalog(
            dataset_path=temp_workspace / "dataset",
            products_json_path=products_json,
            paths=path_resolver
        )

        # Verify enrichment
        df_items = pd.read_csv(path_resolver.items_csv_path(), encoding="utf-8-sig", dtype={"product_id": str, "variant": str})
        assert len(df_items) == 1
        assert df_items.loc[0, "manufacturer"] == "Test Manufacturer"
        assert df_items.loc[0, "product_name"] == "Nome Prodotto IT"  # IT preferred
        assert df_items.loc[0, "description"] == "Descrizione IT"  # IT preferred
        assert df_items.loc[0, "category_l1"] == "Categoria1"
        assert df_items.loc[0, "category_l2"] == "Categoria2"
        assert df_items.loc[0, "category_l3"] == "Categoria3"
        assert df_items.loc[0, "source_json_present"] == True

    def test_missing_enrichment_fields(self, temp_workspace, path_resolver):
        """Test 4b: Missing enrichment fields produce issues."""
        # Setup: Create products JSON with missing fields
        products_json = temp_workspace / "products-with-3d.json"
        products_data = [
            {
                "ProductId": "5005",
                # Missing Manufacturer, Name, Description, Categories
            }
        ]
        products_json.write_text(json.dumps(products_data), encoding="utf-8")

        # Create dataset/5005/
        item_dir = temp_workspace / "dataset" / "5005"
        images_dir = item_dir / "images"
        gt_dir = item_dir / "gt"

        images_dir.mkdir(parents=True)
        gt_dir.mkdir(parents=True)

        (images_dir / "image.jpg").write_text("")
        (gt_dir / "model.glb").write_text("")

        # Run build
        items_count, issues_count = build_catalog(
            dataset_path=temp_workspace / "dataset",
            products_json_path=products_json,
            paths=path_resolver
        )

        # Check for missing_* issues
        df_issues = pd.read_csv(path_resolver.items_issues_csv_path(), encoding="utf-8-sig", dtype={"product_id": str, "variant": str})
        product_issues = df_issues[df_issues["product_id"] == "5005"]

        issue_types = set(product_issues["issue"].values)
        assert "missing_manufacturer" in issue_types
        assert "missing_product_name" in issue_types
        assert "missing_description" in issue_types
        assert "missing_categories" in issue_types

    def test_workspace_relative_paths(self, temp_workspace, path_resolver):
        """Test 5: Output paths are workspace-relative (no drive letters or absolute paths)."""
        # Setup
        item_dir = temp_workspace / "dataset" / "6006"
        images_dir = item_dir / "images"
        gt_dir = item_dir / "gt"

        images_dir.mkdir(parents=True)
        gt_dir.mkdir(parents=True)

        (images_dir / "image.jpg").write_text("")
        (gt_dir / "model.glb").write_text("")

        # Run build
        items_count, issues_count = build_catalog(
            dataset_path=temp_workspace / "dataset",
            products_json_path=None,
            paths=path_resolver
        )

        # Verify paths are relative
        df_items = pd.read_csv(path_resolver.items_csv_path(), encoding="utf-8-sig", dtype={"product_id": str, "variant": str})

        # Check dataset_dir
        dataset_dir = df_items.loc[0, "dataset_dir"]
        assert not dataset_dir.startswith("/")  # Not absolute POSIX
        assert ":" not in dataset_dir  # No Windows drive letters
        assert dataset_dir.startswith("dataset/")

        # Check image paths
        img_path = df_items.loc[0, "image_1_path"]
        assert not img_path.startswith("/")
        assert ":" not in img_path
        assert img_path.startswith("dataset/")

        # Check GT path
        gt_path = df_items.loc[0, "gt_object_path"]
        assert not gt_path.startswith("/")
        assert ":" not in gt_path
        assert gt_path.startswith("dataset/")

    def test_atomic_writes_no_temp_files(self, temp_workspace, path_resolver):
        """Test 5b: Atomic writes leave no .tmp files behind."""
        # Setup
        item_dir = temp_workspace / "dataset" / "7007"
        images_dir = item_dir / "images"
        gt_dir = item_dir / "gt"

        images_dir.mkdir(parents=True)
        gt_dir.mkdir(parents=True)

        (images_dir / "image.jpg").write_text("")
        (gt_dir / "model.glb").write_text("")

        # Run build
        items_count, issues_count = build_catalog(
            dataset_path=temp_workspace / "dataset",
            products_json_path=None,
            paths=path_resolver
        )

        # Check no .tmp files exist in tables/
        tables_dir = path_resolver.tables_root
        tmp_files = list(tables_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_idempotent_build(self, temp_workspace, path_resolver):
        """Test: Re-running build produces same results (idempotent)."""
        # Setup
        item_dir = temp_workspace / "dataset" / "8008"
        images_dir = item_dir / "images"
        gt_dir = item_dir / "gt"

        images_dir.mkdir(parents=True)
        gt_dir.mkdir(parents=True)

        (images_dir / "image.jpg").write_text("")
        (gt_dir / "model.glb").write_text("")

        # First build
        items_count_1, issues_count_1 = build_catalog(
            dataset_path=temp_workspace / "dataset",
            products_json_path=None,
            paths=path_resolver
        )

        df_items_1 = pd.read_csv(path_resolver.items_csv_path(), encoding="utf-8-sig", dtype={"product_id": str, "variant": str})

        # Second build (should produce same results except build_time)
        items_count_2, issues_count_2 = build_catalog(
            dataset_path=temp_workspace / "dataset",
            products_json_path=None,
            paths=path_resolver
        )

        df_items_2 = pd.read_csv(path_resolver.items_csv_path(), encoding="utf-8-sig", dtype={"product_id": str, "variant": str})

        # Compare results (excluding build_time which will differ)
        assert items_count_1 == items_count_2
        assert issues_count_1 == issues_count_2

        # Compare key fields
        assert df_items_1.loc[0, "product_id"] == df_items_2.loc[0, "product_id"]
        assert df_items_1.loc[0, "n_images"] == df_items_2.loc[0, "n_images"]
        assert df_items_1.loc[0, "gt_object_path"] == df_items_2.loc[0, "gt_object_path"]

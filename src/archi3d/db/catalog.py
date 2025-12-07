# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

# src/archi3d/db/catalog.py
"""
Phase 1: Catalog Build
Scans the curated dataset folder and enriches with products-with-3d.json metadata.
Writes canonical tables/items.csv and tables/items_issues.csv (SSOT for parent items).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from archi3d.config.paths import PathResolver
from archi3d.utils.io import append_log_record

# --- Constants ---

_FOLDER_NAME_RE = re.compile(r"^(?P<pid>\d+)(?:\s*-\s*(?P<variant>.+))?$")
_SUFFIX_TAG_RE = re.compile(r"_(?P<tag>[A-F])(?:\.[^.]+)$", re.IGNORECASE)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
_GT_EXTENSIONS_PRIORITY = [".glb", ".fbx"]  # Prefer .glb over .fbx

_MAX_IMAGES = 6
_MAX_CATEGORIES = 3


# --- Data Structures ---

@dataclass
class CatalogIssue:
    """Represents an issue found during catalog building."""
    product_id: str
    variant: str
    issue: str
    detail: str


@dataclass
class CatalogItem:
    """Represents a single catalog item (product + variant)."""
    product_id: str
    variant: str
    manufacturer: str
    product_name: str
    category_l1: str
    category_l2: str
    category_l3: str
    description: str
    n_images: int
    image_1_path: str
    image_2_path: str
    image_3_path: str
    image_4_path: str
    image_5_path: str
    image_6_path: str
    gt_object_path: str
    dataset_dir: str
    build_time: str
    source_json_present: bool


# --- Helper Functions ---

def _parse_folder_name(folder_name: str) -> tuple[str, str]:
    """
    Parse folder name into (product_id, variant).

    Examples:
        "353425" -> ("353425", "default")
        "335888 - Curved backrest" -> ("335888", "Curved backrest")

    Returns:
        (product_id, variant) where variant is "default" if not specified
    """
    match = _FOLDER_NAME_RE.match(folder_name)
    if not match:
        return folder_name, "default"  # Fallback for non-matching names

    pid = match.group("pid")
    variant = match.group("variant")
    return pid, variant if variant else "default"


def _collect_and_sort_images(images_dir: Path) -> tuple[list[Path], list[str]]:
    """
    Collect and sort images according to Phase 1 rules:
    1. Tagged images (_A through _F) come first, sorted by tag
    2. Untagged images follow, sorted lexicographically
    3. Max 6 images total

    Returns:
        (sorted_image_paths, issue_notes)
    """
    issues = []

    if not images_dir.exists() or not images_dir.is_dir():
        issues.append("no_images")
        return [], issues

    # Collect all image files (case-insensitive extension check, skip hidden files)
    all_images = [
        p for p in images_dir.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.suffix.lower() in _IMAGE_EXTS
    ]

    if not all_images:
        issues.append("no_images")
        return [], issues

    # Separate tagged and untagged
    tagged: list[tuple[str, Path]] = []
    untagged: list[Path] = []

    for img_path in all_images:
        match = _SUFFIX_TAG_RE.search(img_path.stem)  # Search in stem (before extension)
        if match:
            tag = match.group("tag").upper()
            if "A" <= tag <= "F":
                tagged.append((tag, img_path))
            else:
                untagged.append(img_path)
        else:
            untagged.append(img_path)

    # Sort tagged by tag letter, untagged lexicographically
    tagged.sort(key=lambda t: t[0])
    untagged.sort(key=lambda p: p.name.lower())

    # Combine: tagged first, then untagged
    sorted_images = [p for _, p in tagged] + untagged

    # Cap at max 6
    if len(sorted_images) > _MAX_IMAGES:
        issues.append("too_many_images")
        sorted_images = sorted_images[:_MAX_IMAGES]

    return sorted_images, issues


def _select_gt_object(gt_dir: Path) -> tuple[Path | None, list[str]]:
    """
    Select a single GT file according to Phase 1 rules:
    1. Prefer .glb over .fbx
    2. If multiple files of the same extension, take lexicographically first
    3. Issue warning if multiple candidates found

    Returns:
        (gt_path, issue_notes)
    """
    issues = []

    if not gt_dir.exists() or not gt_dir.is_dir():
        issues.append("missing_gt")
        return None, issues

    # Collect all GT files by extension priority
    all_gt_files = [
        p for p in gt_dir.iterdir()
        if p.is_file() and not p.name.startswith(".")
    ]

    # Group by preferred extensions
    candidates_by_ext = {}
    for ext in _GT_EXTENSIONS_PRIORITY:
        candidates = [p for p in all_gt_files if p.suffix.lower() == ext]
        if candidates:
            candidates_by_ext[ext] = sorted(candidates, key=lambda p: p.name.lower())

    if not candidates_by_ext:
        issues.append("missing_gt")
        return None, issues

    # Pick the first preferred extension that has candidates
    for ext in _GT_EXTENSIONS_PRIORITY:
        if ext in candidates_by_ext:
            candidates = candidates_by_ext[ext]
            if len(candidates) > 1:
                issues.append("multiple_gt_candidates")
            return candidates[0], issues

    # Should not reach here, but fallback
    issues.append("missing_gt")
    return None, issues


def _load_products_json(json_path: Path) -> dict[str, dict[str, Any]]:
    """
    Load and parse products-with-3d.json into a lookup dict.

    Supports multiple ID field names: _id, ProductId, product_id (in priority order).

    Returns:
        Dict keyed by product_id (string)
    """
    if not json_path.exists():
        return {}

    try:
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        # Assuming the JSON is a list of products, each with an ID field
        # Check multiple possible ID field names in priority order
        if isinstance(data, list):
            result = {}
            for item in data:
                # Try multiple ID field names
                product_id = (
                    item.get("_id")
                    or item.get("ProductId")
                    or item.get("product_id")
                    or ""
                )
                if product_id:
                    result[str(product_id)] = item
            return result
        elif isinstance(data, dict):
            # If it's already a dict, return as-is (assuming keys are product_ids)
            return {str(k): v for k, v in data.items()}
        else:
            return {}
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Warning: Failed to parse products JSON at {json_path}: {e}")
        return {}


def _extract_category_names(categories: list[Any]) -> list[str]:
    """
    Extract category names from a list of category objects.

    Supports two formats:
    1. Flat objects: [{"Name": {"it": "Poltrone"}}, {"Name": {"it": "Lounge"}}]
    2. Hierarchical strings: "Furniture > Chairs > Armchairs"

    Returns:
        List of category names (up to 3 levels)
    """
    cat_names: list[str] = []

    for cat in categories:
        if isinstance(cat, dict):
            name_obj = cat.get("Name", {})
            if isinstance(name_obj, dict):
                cat_name = name_obj.get("it") or name_obj.get("en") or ""
            elif isinstance(name_obj, str):
                cat_name = name_obj
            else:
                cat_name = ""

            if cat_name:
                # Check if hierarchical (contains " > ")
                if " > " in cat_name:
                    # Split hierarchical string into parts
                    cat_names.extend(cat_name.split(" > "))
                else:
                    cat_names.append(cat_name)
        elif isinstance(cat, str) and cat:
            if " > " in cat:
                cat_names.extend(cat.split(" > "))
            else:
                cat_names.append(cat)

    # Return first 3 unique categories
    seen: set[str] = set()
    unique: list[str] = []
    for name in cat_names:
        if name not in seen:
            seen.add(name)
            unique.append(name)
            if len(unique) >= _MAX_CATEGORIES:
                break

    return unique


def _extract_enrichment_data(
    product_id: str,
    products_lookup: dict[str, dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """
    Extract enrichment fields from products-with-3d.json for a given product_id.

    Returns:
        (enrichment_dict, missing_field_issues)
    """
    enrichment = {
        "manufacturer": "",
        "product_name": "",
        "description": "",
        "category_l1": "",
        "category_l2": "",
        "category_l3": "",
    }
    issues = []

    if product_id not in products_lookup:
        return enrichment, issues

    product_data = products_lookup[product_id]

    # Extract manufacturer
    manuf = product_data.get("Manufacturer")
    if isinstance(manuf, dict):
        enrichment["manufacturer"] = manuf.get("Name", "")
    elif isinstance(manuf, str):
        enrichment["manufacturer"] = manuf

    if not enrichment["manufacturer"]:
        issues.append("missing_manufacturer")

    # Extract product_name (prefer IT, fallback to EN)
    name_obj = product_data.get("Name", {})
    if isinstance(name_obj, dict):
        value_obj = name_obj.get("Value", {})
        if isinstance(value_obj, dict):
            enrichment["product_name"] = value_obj.get("it") or value_obj.get("en") or ""
        else:
            enrichment["product_name"] = (
                name_obj.get("it")
                or name_obj.get("en")
                or str(name_obj.get("Name", ""))
            )
    elif isinstance(name_obj, str):
        enrichment["product_name"] = name_obj

    if not enrichment["product_name"]:
        issues.append("missing_product_name")

    # Extract description (prefer IT, fallback to EN)
    desc_obj = product_data.get("ShortDescription", product_data.get("Description", {}))
    if isinstance(desc_obj, dict):
        value_obj = desc_obj.get("Value", {})
        if isinstance(value_obj, dict):
            enrichment["description"] = value_obj.get("it") or value_obj.get("en") or ""
        else:
            enrichment["description"] = desc_obj.get("it") or desc_obj.get("en") or str(desc_obj)
    elif isinstance(desc_obj, str):
        enrichment["description"] = desc_obj

    if not enrichment["description"]:
        issues.append("missing_description")

    # Extract categories - supports both flat objects and hierarchical strings
    categories = product_data.get("Categories", [])
    if isinstance(categories, list) and categories:
        cat_names = _extract_category_names(categories)
        if cat_names:
            enrichment["category_l1"] = cat_names[0] if cat_names else ""
            enrichment["category_l2"] = cat_names[1] if len(cat_names) > 1 else ""
            enrichment["category_l3"] = (
                cat_names[2] if len(cat_names) >= _MAX_CATEGORIES else ""
            )

    if not any([
        enrichment["category_l1"],
        enrichment["category_l2"],
        enrichment["category_l3"],
    ]):
        issues.append("missing_categories")

    return enrichment, issues


def build_catalog(
    dataset_path: Path,
    products_json_path: Path | None,
    paths: PathResolver
) -> tuple[int, int]:
    """
    Build the catalog by scanning the dataset and enriching with JSON metadata.

    Args:
        dataset_path: Path to the dataset directory
        products_json_path: Path to products-with-3d.json (or None if not available)
        paths: PathResolver instance for output paths

    Returns:
        (items_count, issues_count) tuple
    """
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset directory not found: {dataset_path}")

    # Load products JSON if available
    products_lookup = {}
    source_json_present = False
    if products_json_path and products_json_path.exists():
        products_lookup = _load_products_json(products_json_path)
        source_json_present = True
        print(
            f"Loaded enrichment data for {len(products_lookup)} products "
            f"from {products_json_path}"
        )
    else:
        print(
            f"Warning: Products JSON not found at {products_json_path}, "
            "proceeding without enrichment"
        )

    # Collect all catalog items and issues
    catalog_items: list[CatalogItem] = []
    all_issues: list[CatalogIssue] = []

    build_time = datetime.now(UTC).isoformat()

    # Counters for logging
    items_with_img = 0
    items_with_gt = 0
    no_images_count = 0
    too_many_images_count = 0
    multi_gt_candidates_count = 0
    missing_meta_counts = {
        "missing_manufacturer": 0,
        "missing_product_name": 0,
        "missing_description": 0,
        "missing_categories": 0,
    }

    # Scan dataset directories
    product_dirs = sorted([
        p for p in dataset_path.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ], key=lambda p: p.name.lower())

    for prod_dir in product_dirs:
        product_id, variant = _parse_folder_name(prod_dir.name)

        # Scan images
        images_dir = prod_dir / "images"
        image_paths, img_issues = _collect_and_sort_images(images_dir)

        # Scan GT
        gt_dir = prod_dir / "gt"
        gt_path, gt_issues = _select_gt_object(gt_dir)

        # Get enrichment data
        enrichment, enrich_issues = _extract_enrichment_data(product_id, products_lookup)

        # Convert paths to workspace-relative
        dataset_dir_rel = paths.rel_to_workspace(prod_dir)

        image_rel_paths = [
            paths.rel_to_workspace(img).as_posix()
            for img in image_paths
        ]
        # Pad to _MAX_IMAGES paths
        while len(image_rel_paths) < _MAX_IMAGES:
            image_rel_paths.append("")

        gt_rel_path = ""
        if gt_path:
            gt_rel_path = paths.rel_to_workspace(gt_path).as_posix()
            items_with_gt += 1

        if image_paths:
            items_with_img += 1

        # Create catalog item
        item = CatalogItem(
            product_id=product_id,
            variant=variant,
            manufacturer=enrichment["manufacturer"],
            product_name=enrichment["product_name"],
            category_l1=enrichment["category_l1"],
            category_l2=enrichment["category_l2"],
            category_l3=enrichment["category_l3"],
            description=enrichment["description"],
            n_images=len(image_paths),
            image_1_path=image_rel_paths[0],
            image_2_path=image_rel_paths[1],
            image_3_path=image_rel_paths[2],
            image_4_path=image_rel_paths[3],
            image_5_path=image_rel_paths[4],
            image_6_path=image_rel_paths[5],
            gt_object_path=gt_rel_path,
            dataset_dir=dataset_dir_rel.as_posix(),
            build_time=build_time,
            source_json_present=source_json_present,
        )
        catalog_items.append(item)

        # Collect issues
        all_issue_types = img_issues + gt_issues + enrich_issues
        for issue_type in all_issue_types:
            detail = ""
            if issue_type == "too_many_images":
                found_count = len(image_paths) + len(img_issues)
                detail = f"Found {found_count} images, capped at {_MAX_IMAGES}"
                too_many_images_count += 1
            elif issue_type == "no_images":
                no_images_count += 1
            elif issue_type == "multiple_gt_candidates":
                multi_gt_candidates_count += 1
            elif issue_type in missing_meta_counts:
                missing_meta_counts[issue_type] += 1

            all_issues.append(CatalogIssue(
                product_id=product_id,
                variant=variant,
                issue=issue_type,
                detail=detail
            ))

    # Write items.csv atomically
    items_csv_path = paths.items_csv_path()
    items_df = pd.DataFrame([vars(item) for item in catalog_items])

    # Ensure product_id and variant are strings (prevent pandas from converting to int)
    if len(items_df) > 0:
        items_df["product_id"] = items_df["product_id"].astype(str)
        items_df["variant"] = items_df["variant"].astype(str)

    # Write atomically using temp file + rename
    tmp_path = items_csv_path.with_suffix(items_csv_path.suffix + ".tmp")
    items_df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
    tmp_path.replace(items_csv_path)

    # Write items_issues.csv atomically
    issues_csv_path = paths.items_issues_csv_path()
    if all_issues:
        issues_df = pd.DataFrame([vars(issue) for issue in all_issues])
        # Ensure product_id and variant are strings
        issues_df["product_id"] = issues_df["product_id"].astype(str)
        issues_df["variant"] = issues_df["variant"].astype(str)

        tmp_issues_path = issues_csv_path.with_suffix(issues_csv_path.suffix + ".tmp")
        issues_df.to_csv(tmp_issues_path, index=False, encoding="utf-8-sig")
        tmp_issues_path.replace(issues_csv_path)
    else:
        # Write empty CSV with headers
        empty_df = pd.DataFrame(columns=["product_id", "variant", "issue", "detail"])
        tmp_issues_path = issues_csv_path.with_suffix(issues_csv_path.suffix + ".tmp")
        empty_df.to_csv(tmp_issues_path, index=False, encoding="utf-8-sig")
        tmp_issues_path.replace(issues_csv_path)

    # Write structured log summary
    log_path = paths.catalog_build_log_path()
    if dataset_path.is_absolute():
        dataset_rel = paths.rel_to_workspace(dataset_path)
    else:
        dataset_rel = dataset_path

    json_rel = None
    if products_json_path and products_json_path.exists():
        if products_json_path.is_absolute():
            json_rel = paths.rel_to_workspace(products_json_path)
        else:
            json_rel = products_json_path

    log_record = {
        "event": "catalog_build",
        "timestamp": build_time,
        "dataset": dataset_rel.as_posix(),
        "products_json": json_rel.as_posix() if json_rel else None,
        "items_total": len(catalog_items),
        "items_with_img": items_with_img,
        "items_with_gt": items_with_gt,
        "no_images_count": no_images_count,
        "too_many_images_count": too_many_images_count,
        "missing_meta_counts": missing_meta_counts,
        "multi_gt_candidates": multi_gt_candidates_count,
    }

    append_log_record(log_path, log_record)

    print("\nCatalog build complete:")
    print(f"  Items total: {len(catalog_items)}")
    print(f"  With images: {items_with_img}")
    print(f"  With GT: {items_with_gt}")
    print(f"  Issues: {len(all_issues)}")
    print("\nOutput files:")
    print(f"  {items_csv_path}")
    print(f"  {issues_csv_path}")
    print(f"  {log_path}")

    return len(catalog_items), len(all_issues)

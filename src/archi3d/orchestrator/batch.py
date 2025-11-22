"""
Batch creation orchestration for Phase 2.

This module creates deterministic job registries in tables/generations.csv
and per-run manifests in runs/<run_id>/manifest.csv.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from archi3d.config.adapters_cfg import get_adapter_image_mode
from archi3d.config.paths import PathResolver
from archi3d.db.generations import compute_image_set_hash, compute_job_id, upsert_generations
from archi3d.utils.io import append_log_record


# -------------------------------
# Image Selection Policy
# -------------------------------

def _select_images_use_up_to_6(row: pd.Series) -> tuple[list[str], str]:
    """
    Apply 'use_up_to_6' image selection policy.

    Uses the first n_images from items.csv (already ordered by Phase 1).
    Phase 1 guarantees deterministic selection and max 6 images.

    Args:
        row: DataFrame row from items.csv with image_1_path...image_6_path columns.

    Returns:
        (selected_image_paths, skip_reason)
        skip_reason is "no_images" if n_images < 1, else ""
    """
    n_images = int(row.get("n_images", 0))

    if n_images < 1:
        return [], "no_images"

    # Collect non-empty image paths in order
    selected = []
    for i in range(1, 7):  # image_1_path through image_6_path
        path = str(row.get(f"image_{i}_path", "")).strip()
        if path:
            selected.append(path)

    # Should match n_images from Phase 1, but verify
    if len(selected) != n_images:
        # Log warning but continue with what we found
        pass

    return selected, ""


# -------------------------------
# Algorithm Selection
# -------------------------------

def _select_algos_for_item(
    n_images: int,
    single_algos: list[str],
    multi_algos: list[str],
    algo_by_images: bool,
) -> list[str]:
    """
    Select which algorithms to use for an item based on its image count.

    Args:
        n_images: Number of images the item has.
        single_algos: List of single-image algorithm keys.
        multi_algos: List of multi-image algorithm keys.
        algo_by_images: If True, select algos based on n_images (ecotest mode).
                        If False, return all algorithms.

    Returns:
        List of algorithm keys to use for this item.
    """
    if not algo_by_images:
        # Normal mode: use all provided algos
        return single_algos + multi_algos

    # Ecotest mode: select based on n_images
    if n_images == 1:
        return single_algos
    elif n_images > 1:
        return multi_algos
    else:
        return []  # No images, skip


# -------------------------------
# Filtering
# -------------------------------

def _apply_filters(
    items_df: pd.DataFrame,
    include: Optional[str] = None,
    exclude: Optional[str] = None,
    with_gt_only: bool = False,
    limit: Optional[int] = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """
    Apply filtering rules in order: include → exclude → with_gt_only → limit.

    Returns:
        (filtered_df, skip_counts) where skip_counts has keys like 'filtered_include', 'filtered_exclude', etc.
    """
    skip_counts: dict[str, int] = {}
    df = items_df.copy()
    initial_count = len(df)

    # 1. Include filter (match on product_id, variant, or product_name)
    if include:
        pattern = include.lower()
        mask = (
            df["product_id"].str.lower().str.contains(pattern, na=False, regex=False) |
            df["variant"].str.lower().str.contains(pattern, na=False, regex=False) |
            df["product_name"].str.lower().str.contains(pattern, na=False, regex=False)
        )
        excluded = int(initial_count - mask.sum())
        if excluded > 0:
            skip_counts["filtered_include"] = excluded
        df = df[mask].reset_index(drop=True)

    # 2. Exclude filter
    if exclude:
        pattern = exclude.lower()
        mask = ~(
            df["product_id"].str.lower().str.contains(pattern, na=False, regex=False) |
            df["variant"].str.lower().str.contains(pattern, na=False, regex=False) |
            df["product_name"].str.lower().str.contains(pattern, na=False, regex=False)
        )
        excluded = int(len(df) - mask.sum())
        if excluded > 0:
            skip_counts["filtered_exclude"] = excluded
        df = df[mask].reset_index(drop=True)

    # 3. with-gt-only
    if with_gt_only:
        mask = df["gt_object_path"].str.strip().str.len() > 0
        excluded = int(len(df) - mask.sum())
        if excluded > 0:
            skip_counts["with_gt_only"] = excluded
        df = df[mask].reset_index(drop=True)

    # 4. Limit (apply last)
    if limit is not None and limit > 0 and len(df) > limit:
        df = df.head(limit).reset_index(drop=True)

    return df, skip_counts


# -------------------------------
# Batch Creation
# -------------------------------

def create_batch(
    run_id: str,
    algos: list[str],
    paths: PathResolver,
    image_policy: str = "use_up_to_6",
    limit: Optional[int] = None,
    include: Optional[str] = None,
    exclude: Optional[str] = None,
    with_gt_only: bool = False,
    dry_run: bool = False,
    algo_by_images: bool = False,
) -> dict:
    """
    Create a batch of jobs for the given run_id and algorithms.

    Reads tables/items.csv, applies filters and image selection policy,
    upserts rows to tables/generations.csv with status='enqueued',
    and creates runs/<run_id>/manifest.csv.

    Args:
        run_id: Unique run identifier (e.g., UTC timestamp slug).
        algos: List of algorithm keys to create jobs for.
        paths: PathResolver instance.
        image_policy: Image selection policy (currently only 'use_up_to_6').
        limit: Maximum number of items to process (applied after other filters).
        include: Include filter pattern (substring match on product_id/variant/product_name).
        exclude: Exclude filter pattern.
        with_gt_only: If True, skip items without GT object.
        dry_run: If True, don't write files, only compute summary.
        algo_by_images: If True (ecotest mode), select algorithms based on n_images:
                        single-image algos for items with 1 image,
                        multi-image algos for items with 2+ images.

    Returns:
        Summary dict with counts and skip reasons.

    Raises:
        FileNotFoundError: If tables/items.csv doesn't exist.
        ValueError: If image_policy is not supported.
    """
    # Validate workspace
    paths.ensure_mutable_tree()

    # Validate policy
    if image_policy != "use_up_to_6":
        raise ValueError(f"Unsupported image policy: {image_policy}")

    # Read items.csv
    items_csv_path = paths.items_csv_path()
    if not items_csv_path.exists():
        raise FileNotFoundError(
            f"items.csv not found at {items_csv_path}.\n"
            "Run 'archi3d catalog build' first."
        )

    items_df = pd.read_csv(
        items_csv_path,
        dtype={"product_id": str, "variant": str},
        encoding="utf-8-sig",
    ).fillna("")

    # Apply filters
    filtered_df, filter_skip_counts = _apply_filters(
        items_df,
        include=include,
        exclude=exclude,
        with_gt_only=with_gt_only,
        limit=limit,
    )

    candidates = int(len(filtered_df))

    # Build generation records
    generation_records = []
    skip_reasons: dict[str, int] = {}

    # Partition algorithms by image mode for ecotest
    single_algos = [a for a in algos if get_adapter_image_mode(a) == "single"]
    multi_algos = [a for a in algos if get_adapter_image_mode(a) == "multi"]

    for _, row in filtered_df.iterrows():
        # Extract parent fields
        product_id = str(row["product_id"]).strip()
        variant = str(row["variant"]).strip()
        manufacturer = str(row["manufacturer"]).strip()
        product_name = str(row["product_name"]).strip()
        category_l1 = str(row["category_l1"]).strip()
        category_l2 = str(row["category_l2"]).strip()
        category_l3 = str(row["category_l3"]).strip()
        description = str(row["description"]).strip()
        source_n_images = int(row["n_images"])
        gt_object_path = str(row["gt_object_path"]).strip()

        # Collect source images
        source_images = []
        for i in range(1, 7):
            path = str(row.get(f"image_{i}_path", "")).strip()
            if path:
                source_images.append(path)

        # Apply image selection policy
        used_images, skip_reason = _select_images_use_up_to_6(row)

        if skip_reason:
            skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1
            continue

        # Compute common fields
        image_set_hash = compute_image_set_hash(used_images)
        used_images_padded = used_images + [""] * (6 - len(used_images))
        source_images_padded = source_images + [""] * (6 - len(source_images))

        # Select algorithms for this item based on ecotest mode
        item_algos = _select_algos_for_item(
            n_images=source_n_images,
            single_algos=single_algos,
            multi_algos=multi_algos,
            algo_by_images=algo_by_images,
        )

        if not item_algos:
            skip_reasons["no_matching_algo"] = (
                skip_reasons.get("no_matching_algo", 0) + 1
            )
            continue

        for algo in item_algos:
            job_id = compute_job_id(product_id, variant, algo, image_set_hash)

            # Create generation record
            record = {
                # Carry-over from parent (observability)
                "product_id": product_id,
                "variant": variant,
                "manufacturer": manufacturer,
                "product_name": product_name,
                "category_l1": category_l1,
                "category_l2": category_l2,
                "category_l3": category_l3,
                "description": description,
                "source_n_images": source_n_images,
                "source_image_1_path": source_images_padded[0],
                "source_image_2_path": source_images_padded[1],
                "source_image_3_path": source_images_padded[2],
                "source_image_4_path": source_images_padded[3],
                "source_image_5_path": source_images_padded[4],
                "source_image_6_path": source_images_padded[5],
                "gt_object_path": gt_object_path,
                # Batch/job metadata
                "run_id": run_id,
                "job_id": job_id,
                "algo": algo,
                "algo_version": "",  # Reserved for adapters to fill later
                "used_n_images": len(used_images),
                "used_image_1_path": used_images_padded[0],
                "used_image_2_path": used_images_padded[1],
                "used_image_3_path": used_images_padded[2],
                "used_image_4_path": used_images_padded[3],
                "used_image_5_path": used_images_padded[4],
                "used_image_6_path": used_images_padded[5],
                "image_set_hash": image_set_hash,
                "status": "enqueued",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "notes": "",
            }

            generation_records.append(record)

    # Combine filter skip counts with policy skip counts
    all_skip_reasons = {**filter_skip_counts, **skip_reasons}

    enqueued = len(generation_records)
    skipped = sum(all_skip_reasons.values())

    # Build summary
    summary = {
        "event": "batch_create",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "algos": algos,
        "algo_by_images": algo_by_images,
        "single_algos": single_algos,
        "multi_algos": multi_algos,
        "image_policy": image_policy,
        "candidates": candidates,
        "enqueued": enqueued,
        "skipped": skipped,
        "skip_reasons": all_skip_reasons,
        "dry_run": dry_run,
    }

    if dry_run:
        # Log summary with dry_run flag but don't write files
        log_path = paths.batch_create_log_path()
        append_log_record(log_path, summary)
        return summary

    # Write to generations.csv (atomic upsert)
    if generation_records:
        generations_df = pd.DataFrame(generation_records)
        generations_csv_path = paths.generations_csv_path()
        inserted, updated = upsert_generations(generations_csv_path, generations_df)

        # If all rows were updated (not inserted), count them as duplicate_job
        if updated > 0 and inserted == 0:
            all_skip_reasons["duplicate_job"] = updated
            enqueued = inserted
            skipped += updated
            summary["enqueued"] = enqueued
            summary["skipped"] = skipped
            summary["skip_reasons"] = all_skip_reasons

    # Write per-run manifest (derived from generations.csv)
    if generation_records:
        # Read back the just-upserted rows for this run_id with status=enqueued
        generations_csv_path = paths.generations_csv_path()
        if generations_csv_path.exists():
            full_gen_df = pd.read_csv(
                generations_csv_path,
                dtype={"product_id": str, "variant": str, "run_id": str, "job_id": str},
                encoding="utf-8-sig",
            )
            run_enqueued = full_gen_df[
                (full_gen_df["run_id"] == run_id) &
                (full_gen_df["status"] == "enqueued")
            ].copy()

            # Build manifest with required columns
            manifest_cols = [
                "job_id", "product_id", "variant", "algo", "used_n_images",
                "used_image_1_path", "used_image_2_path", "used_image_3_path",
                "used_image_4_path", "used_image_5_path", "used_image_6_path",
                "image_set_hash",
                # Optional convenience columns
                "gt_object_path", "product_name", "manufacturer",
            ]

            # Ensure all required columns exist (handle empty dataframe)
            for col in manifest_cols:
                if col not in run_enqueued.columns:
                    run_enqueued[col] = ""

            manifest_df = run_enqueued[manifest_cols].copy()

            # Write manifest
            manifest_path = paths.run_root(run_id) / "manifest.csv"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_df.to_csv(manifest_path, index=False, encoding="utf-8-sig")

    # Log summary
    log_path = paths.batch_create_log_path()
    append_log_record(log_path, summary)

    return summary

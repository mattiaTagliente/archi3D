# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

"""
Database utilities for generations registry (tables/generations.csv).

This module provides helpers for deterministic job identity computation
and atomic upserts to the generations SSOT table.
"""

import hashlib
from pathlib import Path
from typing import List

import pandas as pd

from archi3d.utils.io import update_csv_atomic


def compute_image_set_hash(image_paths: List[str]) -> str:
    """
    Compute deterministic SHA1 hash of an ordered list of image paths.

    Args:
        image_paths: List of workspace-relative image paths (POSIX format).

    Returns:
        40-character hex SHA1 hash string.

    Example:
        >>> compute_image_set_hash(["dataset/123/images/a.jpg", "dataset/123/images/b.jpg"])
        'a1b2c3d4e5f6...'
    """
    # Join paths with newline separator (stable across Python versions)
    joined = "\n".join(image_paths)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def compute_job_id(product_id: str, variant: str, algo: str, image_set_hash: str) -> str:
    """
    Compute deterministic 12-character job ID.

    Job ID is the first 12 hex digits of SHA1(product_id|variant|algo|image_set_hash).
    The pipe separator ensures field boundaries are unambiguous.

    Args:
        product_id: Product identifier (e.g., "335888").
        variant: Variant name (e.g., "default" or "Curved backrest").
        algo: Algorithm key (e.g., "tripo3d_v2p5").
        image_set_hash: Full 40-char SHA1 hash from compute_image_set_hash().

    Returns:
        12-character hex job ID (first 12 chars of SHA1).

    Example:
        >>> compute_job_id("335888", "default", "tripo3d_v2p5", "a1b2c3d4...")
        'f8a9b2c1d3e4'
    """
    # Use pipe separator for unambiguous field boundaries
    composite = f"{product_id}|{variant}|{algo}|{image_set_hash}"
    full_hash = hashlib.sha1(composite.encode("utf-8")).hexdigest()
    return full_hash[:12]


def upsert_generations(
    generations_csv_path: Path,
    df_new: pd.DataFrame,
) -> tuple[int, int]:
    """
    Atomically upsert rows into tables/generations.csv.

    Uses Phase 0 update_csv_atomic with key columns (run_id, job_id).
    All CSV writes are atomic and locked to support concurrent access.

    Args:
        generations_csv_path: Absolute path to tables/generations.csv.
        df_new: DataFrame with generation records to upsert.
                Must contain columns: run_id, job_id, status, created_at, etc.

    Returns:
        Tuple of (inserted_count, updated_count).

    Raises:
        ValueError: If df_new is missing required key columns.
    """
    # Validate key columns present
    key_cols = ["run_id", "job_id"]
    missing = set(key_cols) - set(df_new.columns)
    if missing:
        raise ValueError(f"DataFrame missing required key columns: {missing}")

    # Use Phase 0 atomic upsert
    inserted, updated = update_csv_atomic(
        path=generations_csv_path,
        df_new=df_new,
        key_cols=key_cols,
    )

    return inserted, updated

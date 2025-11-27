# src/archi3d/utils/io.py
from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from filelock import FileLock


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")

def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def read_csv_dicts(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def write_csv_dicts(path: Path, rows: Iterable[dict]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

def read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


# -------------------------
# Phase 0: Atomic I/O Utilities
# -------------------------

def write_text_atomic(path: Path, text: str) -> None:
    """
    Write text to a file atomically using temp file + rename.

    Args:
        path: Target file path
        text: Text content to write

    The write is atomic on both POSIX and Windows via os.replace().
    Creates parent directories if needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        # Write to temp file with flush + fsync for durability
        with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())

        # Atomic rename (replaces existing file)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp file on failure
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def append_log_record(path: Path, record: str | dict) -> None:
    """
    Append a log record to a file with ISO8601 timestamp prefix.
    Thread-safe via file locking.

    Args:
        path: Log file path
        record: String message or dict to serialize as JSON

    Each line is prefixed with UTC timestamp in ISO8601 format.
    Dict records are serialized as single-line JSON.
    Uses FileLock to prevent concurrent corruption.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")

    # Serialize record
    if isinstance(record, dict):
        record_text = json.dumps(record, ensure_ascii=False)
    else:
        record_text = str(record)

    # Add timestamp prefix
    timestamp = datetime.now(UTC).isoformat()
    line = f"{timestamp} {record_text}\n"

    # Append under lock
    with FileLock(lock_path, timeout=10):
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def update_csv_atomic(
    path: Path,
    df_new: pd.DataFrame,
    key_cols: list[str]
) -> tuple[int, int]:
    """
    Atomically upsert rows into a CSV table using key columns.

    Args:
        path: Target CSV file path
        df_new: New/updated rows to insert or merge
        key_cols: Column names that form the unique key

    Returns:
        (inserted_count, updated_count) tuple

    Behavior:
        - If file doesn't exist: write df_new as new CSV
        - If file exists: read, merge on key_cols, write atomically
        - New rows are inserted, existing keys are updated
        - Column order: existing columns first, new columns appended
        - Uses utf-8-sig encoding for Excel compatibility
        - Thread-safe via file locking

    Raises:
        ValueError: If key_cols are missing from either dataframe
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")

    # Validate key columns exist in new data
    missing_keys = set(key_cols) - set(df_new.columns)
    if missing_keys:
        raise ValueError(f"key_cols {missing_keys} not found in df_new columns")

    # Deduplicate df_new on key columns (keep last) - do this BEFORE locking
    df_new_deduped = df_new.drop_duplicates(subset=key_cols, keep="last")

    with FileLock(lock_path, timeout=30):
        # Case 1: File doesn't exist - write new
        if not path.exists():
            df_new_deduped.to_csv(path, index=False, encoding="utf-8-sig")
            return (len(df_new_deduped), 0)

        # Case 2: File exists - read, merge, write
        df_existing = pd.read_csv(path, encoding="utf-8-sig")

        # Validate key columns exist in existing data
        missing_keys_existing = set(key_cols) - set(df_existing.columns)
        if missing_keys_existing:
            raise ValueError(
                f"key_cols {missing_keys_existing} not found in existing CSV"
            )

        # CRITICAL: Ensure key columns have matching dtypes to prevent merge failures
        # If df_existing has product_id=353481 (int) and df_new has product_id="353481" (str),
        # the merge won't match and will create duplicates instead of updates.
        for key_col in key_cols:
            if key_col in df_new_deduped.columns:
                # Convert existing key column to match new data's dtype
                df_existing[key_col] = df_existing[key_col].astype(df_new_deduped[key_col].dtype)

        # Track which keys are updates vs inserts (df_new_deduped already exists)
        existing_keys = df_existing[key_cols].apply(tuple, axis=1)
        new_keys = df_new_deduped[key_cols].apply(tuple, axis=1)

        updated_count = len(set(new_keys) & set(existing_keys))
        inserted_count = len(set(new_keys) - set(existing_keys))

        # Merge: new data overwrites existing on matching keys
        # indicator=True adds _merge column to track source
        df_merged = df_existing.merge(
            df_new_deduped,
            on=key_cols,
            how="outer",
            suffixes=("_old", ""),
            indicator=True
        )

        # For columns that appear in both, prefer new values (non-suffixed)
        # Drop _old columns
        cols_to_drop = [c for c in df_merged.columns if c.endswith("_old")]
        df_merged = df_merged.drop(columns=cols_to_drop + ["_merge"])

        # Preserve dtypes from df_new for all columns (especially key columns)
        # This ensures that if df_new has product_id as str, the final CSV will too
        for col in df_new_deduped.columns:
            if col in df_merged.columns:
                df_merged[col] = df_merged[col].astype(df_new_deduped[col].dtype)

        # Preserve column order: existing first, then new columns
        existing_cols = [c for c in df_existing.columns if c in df_merged.columns]
        new_cols = [c for c in df_merged.columns if c not in existing_cols]
        df_merged = df_merged[existing_cols + new_cols]

        # Write atomically via temp file
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            df_merged.to_csv(tmp_path, index=False, encoding="utf-8-sig")
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

        return (inserted_count, updated_count)
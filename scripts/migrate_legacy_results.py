#!/usr/bin/env python3
"""
Migration script to import legacy generation results into new generations.csv format.

This script:
1. Reads legacy results_elaborated_enriched.csv
2. Reads current items.csv for source metadata
3. Maps legacy columns to new generations.csv schema
4. Converts legacy paths to new workspace-relative format
5. Upserts to tables/generations.csv

Usage:
    python scripts/migrate_legacy_results.py \
        --legacy-csv /path/to/legacy/results_elaborated_enriched.csv \
        --legacy-root /path/to/legacy/folder \
        [--dry-run]
"""

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from archi3d.config.loader import load_config
from archi3d.config.paths import PathResolver
from archi3d.db.generations import compute_image_set_hash, upsert_generations


def compute_legacy_job_id(product_id: str, variant: str, algo: str, n_images: int) -> str:
    """
    Compute job ID matching legacy v0.1.0 hash function.

    Legacy version used different hash inputs than current version.
    This preserves the original job_id for continuity.

    Args:
        product_id: Product identifier
        variant: Variant name (empty string if none)
        algo: Algorithm key
        n_images: Number of images used

    Returns:
        12-character hex job ID (first 12 chars of SHA1)
    """
    # Legacy hash used pipe separator and included n_images
    composite = f"{product_id}|{variant}|{algo}|{n_images}"
    full_hash = hashlib.sha1(composite.encode("utf-8")).hexdigest()
    return full_hash[:12]


def convert_legacy_path_to_workspace_relative(
    legacy_path: str,
    legacy_root: Path,
    workspace_root: Path
) -> str | None:
    """
    Convert legacy path to workspace-relative format.

    Legacy paths are relative to the legacy folder (e.g., "runs\\2025-08-17_v1\\outputs\\...").
    New paths must be relative to workspace root (e.g., "runs/2025-08-17_v1/outputs/...").

    Args:
        legacy_path: Legacy relative path (backslash separators)
        legacy_root: Absolute path to legacy folder root
        workspace_root: Absolute path to new workspace root

    Returns:
        Workspace-relative path (forward slashes) or None if file doesn't exist
    """
    if pd.isna(legacy_path) or not legacy_path:
        return None

    # Convert backslashes to forward slashes
    legacy_path_normalized = legacy_path.replace("\\", "/")

    # Resolve absolute path from legacy root
    legacy_abs = (legacy_root / legacy_path_normalized).resolve()

    # Check if file exists in legacy location
    if not legacy_abs.exists():
        print(f"  WARNING: Legacy file not found: {legacy_abs}")
        return None

    # Convert to workspace-relative path
    # The file should be copied to the same relative path in the new workspace
    # For now, return the normalized relative path (user will copy files later)
    return legacy_path_normalized


def parse_legacy_worker(worker_str: str) -> tuple[str | None, str | None]:
    """
    Parse legacy worker string into host and user.

    Legacy format: "Gabriele_user" or "hostname_username"

    Args:
        worker_str: Legacy worker identifier

    Returns:
        Tuple of (worker_host, worker_user) or (None, None)
    """
    if pd.isna(worker_str) or not worker_str:
        return None, None

    # Try to split on underscore
    parts = worker_str.split("_", 1)
    if len(parts) == 2:
        # Could be "hostname_username" or "firstname_user"
        # If second part is "user", treat first part as username
        if parts[1] == "user":
            return None, parts[0]  # Only username available
        else:
            return parts[0], parts[1]  # hostname, username

    # Single word - treat as username
    return None, worker_str


def migrate_legacy_results(
    legacy_csv_path: Path,
    legacy_root: Path,
    paths: PathResolver,
    dry_run: bool = False
) -> dict:
    """
    Migrate legacy generation results to new generations.csv format.

    Args:
        legacy_csv_path: Path to legacy results_elaborated_enriched.csv
        legacy_root: Path to legacy folder root (for resolving relative paths)
        paths: PathResolver instance for new workspace
        dry_run: If True, don't write to generations.csv

    Returns:
        Summary dict with migration statistics
    """
    print(f"\n=== Legacy Results Migration ===\n")
    print(f"Legacy CSV: {legacy_csv_path}")
    print(f"Legacy root: {legacy_root}")
    print(f"Workspace: {paths.workspace_root}")
    print(f"Dry run: {dry_run}\n")

    # Read legacy CSV
    print("Reading legacy results...")
    legacy_df = pd.read_csv(
        legacy_csv_path,
        encoding="utf-8-sig",
        dtype={"product_id": str, "variant": str, "run_id": str, "job_id": str}
    )
    print(f"  Found {len(legacy_df)} legacy records\n")

    # Read items.csv for source metadata
    print("Reading items.csv for source metadata...")
    items_csv_path = paths.items_csv_path()
    if not items_csv_path.exists():
        raise FileNotFoundError(
            f"items.csv not found at {items_csv_path}.\n"
            "Run 'archi3d catalog build' first."
        )

    items_df = pd.read_csv(
        items_csv_path,
        dtype={"product_id": str, "variant": str},
        encoding="utf-8-sig"
    ).fillna("")
    print(f"  Found {len(items_df)} items\n")

    # Merge legacy results with items.csv to get source metadata
    # Legacy CSV already has some metadata (manufacturer, product_name, categories)
    # but is missing source images and GT paths
    print("Merging with items metadata...")

    # Select columns from items.csv that we need
    # NOTE: items.csv uses "n_images" and "image_*_path", not "source_*"
    items_columns_needed = [
        "product_id", "variant",
        "n_images",
        "image_1_path", "image_2_path", "image_3_path",
        "image_4_path", "image_5_path", "image_6_path",
        "gt_object_path",
        "description"  # Legacy might not have this
    ]

    # Also include metadata columns if they're in items.csv (for filling missing values)
    optional_metadata = ["manufacturer", "product_name", "category_l1", "category_l2", "category_l3"]
    for col in optional_metadata:
        if col in items_df.columns and col not in items_columns_needed:
            items_columns_needed.append(col)

    # Only select columns that exist in items_df
    items_columns_to_merge = [col for col in items_columns_needed if col in items_df.columns]

    merged_df = legacy_df.merge(
        items_df[items_columns_to_merge],
        on=["product_id", "variant"],
        how="left",
        suffixes=("", "_from_items")
    )

    # Check for items not found in items.csv (missing source images)
    # Check for n_images being NaN (indicates no match in items.csv)
    missing_items = merged_df[merged_df["n_images"].isna()]
    if not missing_items.empty:
        print(f"  WARNING: {len(missing_items)} legacy records have no match in items.csv:")
        for _, row in missing_items.head(5).iterrows():
            print(f"    - {row['product_id']} | {row['variant']}")
        if len(missing_items) > 5:
            print(f"    ... and {len(missing_items) - 5} more")
        print()

    # Build new generation records
    print("Transforming records to new schema...")
    new_records = []
    path_warnings = 0

    for idx, row in merged_df.iterrows():
        # Convert legacy path
        legacy_glb_path = row.get("output_glb_relpath")
        new_glb_path = convert_legacy_path_to_workspace_relative(
            legacy_glb_path,
            legacy_root,
            paths.workspace_root
        )

        if legacy_glb_path and not new_glb_path:
            path_warnings += 1

        # Parse worker info
        worker_host, worker_user = parse_legacy_worker(row.get("worker"))

        # Determine status (legacy didn't track status, assume completed if has output)
        status = "completed" if new_glb_path else "failed"

        # Build new record
        # Use legacy metadata first, fallback to items.csv if needed
        def get_field(field_name, default=""):
            """Get field from row, trying both legacy and items.csv sources."""
            # Try direct field (from legacy CSV or merged items.csv)
            if field_name in row.index and pd.notna(row[field_name]):
                val = row[field_name]
                # Return the value as-is (preserves type)
                return val if val != "" else default
            # Try items.csv fallback
            items_field = f"{field_name}_from_items"
            if items_field in row.index and pd.notna(row[items_field]):
                val = row[items_field]
                return val if val != "" else default
            return default

        record = {
            # Identity fields (from legacy CSV or items.csv)
            "product_id": row["product_id"],
            "variant": row["variant"] if row["variant"] else "",
            "manufacturer": get_field("manufacturer", ""),
            "product_name": get_field("product_name", ""),
            "category_l1": get_field("category_l1", ""),
            "category_l2": get_field("category_l2", ""),
            "category_l3": get_field("category_l3", ""),
            "description": get_field("description", ""),

            # Source images (from items.csv)
            # NOTE: items.csv uses "n_images" and "image_*_path", map to "source_*" for generations.csv
            "source_n_images": get_field("n_images", 0),
            "source_image_1_path": get_field("image_1_path", ""),
            "source_image_2_path": get_field("image_2_path", ""),
            "source_image_3_path": get_field("image_3_path", ""),
            "source_image_4_path": get_field("image_4_path", ""),
            "source_image_5_path": get_field("image_5_path", ""),
            "source_image_6_path": get_field("image_6_path", ""),
            "gt_object_path": get_field("gt_object_path", ""),

            # Job metadata
            "run_id": row["run_id"],
            "job_id": row["job_id"],  # Preserve original job_id
            "algo": row["algo"],
            "algo_version": "legacy-v0.1.0",  # Mark as legacy

            # Used images (legacy didn't track individual used images)
            # We only know n_images, not which specific images were used
            "used_n_images": row.get("n_images", 0),
            "used_image_1_path": "",  # Unknown - legacy didn't track this
            "used_image_2_path": "",
            "used_image_3_path": "",
            "used_image_4_path": "",
            "used_image_5_path": "",
            "used_image_6_path": "",
            "image_set_hash": "",  # Cannot compute without knowing exact images used

            # Status
            "status": status,
            "created_at": "",  # Unknown - legacy didn't track this
            "notes": "Migrated from legacy v0.1.0 results",

            # Execution metadata
            "generation_start": "",  # Unknown
            "generation_end": "",  # Unknown
            "generation_duration_s": row.get("duration_s", 0.0),
            "unit_price_usd": row.get("unit_price_usd", 0.0),
            "price_source": "legacy-csv",

            # Outputs
            "gen_object_path": new_glb_path if new_glb_path else "",
            "preview_1_path": "",  # Legacy didn't track previews
            "preview_2_path": "",
            "preview_3_path": "",
            "error_msg": "" if status == "completed" else "Legacy record with missing output",

            # Worker metadata
            "worker_host": worker_host if worker_host else "",
            "worker_user": worker_user if worker_user else "",
            "worker_gpu": "",  # Unknown
            "worker_env": "legacy",
            "worker_commit": "unknown",

            # VFScore metrics (not present in legacy, all empty)
            # These will be filled in if user runs vfscore computation later
            "vf_status": "",
            "vf_error": "",
            "vfscore_overall": "",
        }

        new_records.append(record)

    print(f"  Transformed {len(new_records)} records")
    if path_warnings > 0:
        print(f"  WARNING: {path_warnings} records have missing output files\n")

    # Create DataFrame
    new_df = pd.DataFrame(new_records)

    # Preview
    print("\nPreview of first 3 records:")
    preview_cols = ["product_id", "variant", "run_id", "algo", "status", "gen_object_path"]
    print(new_df[preview_cols].head(3).to_string(index=False))
    print()

    # Upsert to generations.csv
    if not dry_run:
        print("Upserting to generations.csv...")
        generations_csv_path = paths.generations_csv_path()

        # Ensure parent directory exists
        generations_csv_path.parent.mkdir(parents=True, exist_ok=True)

        inserted, updated = upsert_generations(generations_csv_path, new_df)
        print(f"  Inserted: {inserted}")
        print(f"  Updated: {updated}\n")

        summary = {
            "total_records": len(new_df),
            "inserted": inserted,
            "updated": updated,
            "missing_items": len(missing_items),
            "path_warnings": path_warnings
        }
    else:
        print("DRY RUN - no changes written to generations.csv\n")
        summary = {
            "total_records": len(new_df),
            "missing_items": len(missing_items),
            "path_warnings": path_warnings,
            "dry_run": True
        }

    print("=== Migration Summary ===")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print()

    # Print file copy instructions
    if path_warnings > 0 and not dry_run:
        print("\n=== IMPORTANT: File Copy Instructions ===")
        print(f"You need to copy {path_warnings} generated .glb files from legacy to new workspace:")
        print(f"  FROM: {legacy_root}")
        print(f"  TO:   {paths.workspace_root}")
        print("\nExample copy command (adjust paths as needed):")
        print(f'  cp -r "{legacy_root}/runs" "{paths.workspace_root}/"')
        print()

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Migrate legacy generation results to new generations.csv format",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--legacy-csv",
        type=Path,
        required=True,
        help="Path to legacy results_elaborated_enriched.csv"
    )
    parser.add_argument(
        "--legacy-root",
        type=Path,
        required=True,
        help="Path to legacy folder root (for resolving relative paths)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migration without writing to generations.csv"
    )

    args = parser.parse_args()

    # Validate inputs
    if not args.legacy_csv.exists():
        print(f"ERROR: Legacy CSV not found: {args.legacy_csv}")
        sys.exit(1)

    if not args.legacy_root.exists():
        print(f"ERROR: Legacy root not found: {args.legacy_root}")
        sys.exit(1)

    # Load workspace config
    config = load_config()
    paths = PathResolver(config)

    # Run migration
    try:
        summary = migrate_legacy_results(
            legacy_csv_path=args.legacy_csv,
            legacy_root=args.legacy_root,
            paths=paths,
            dry_run=args.dry_run
        )

        if summary.get("path_warnings", 0) > 0:
            print("\nNOTE: Remember to copy the generated .glb files from legacy to new workspace!")

        sys.exit(0)

    except Exception as e:
        print(f"\nERROR: Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

# Legacy Data Migration Guide

This guide explains how to import legacy generation results into the new archi3D format.

## Overview

The migration script (`migrate_legacy_results.py`) transforms data from your previous archi3D version (v0.1.0) into the new generations.csv format, preserving all metadata and enabling you to reuse existing generated 3D models.

## Prerequisites

1. **Legacy CSV file**: `results_elaborated_enriched.csv` from your previous workspace
2. **Legacy GLB files**: Generated 3D models at paths referenced in the legacy CSV
3. **Current workspace**: New archi3D workspace with `items.csv` already built
4. **Same dataset**: Both workspaces should reference the same dataset items

## Step 1: Preview Migration (Dry Run)

First, run the script in dry-run mode to preview what will be migrated:

```bash
cd /path/to/archi3D

python scripts/migrate_legacy_results.py \
    --legacy-csv "C:/Users/matti/testing/legacy/results_elaborated_enriched.csv" \
    --legacy-root "C:/Users/matti/testing/legacy" \
    --dry-run
```

This will show:
- How many records will be migrated
- Which items are found/missing in current items.csv
- Which output files are missing (need to be copied)
- Preview of first 3 records

Example output:
```
=== Legacy Results Migration ===

Legacy CSV: C:\Users\matti\testing\legacy\results_elaborated_enriched.csv
Legacy root: C:\Users\matti\testing\legacy
Workspace: C:\Users\matti\testing
Dry run: True

Reading legacy results...
  Found 802 legacy records

Reading items.csv for source metadata...
  Found 136 items

Merging with items metadata...
  Transformed 802 records
  WARNING: 802 records have missing output files

Preview of first 3 records:
product_id variant        run_id                     algo status
    682267          2025-08-17_v1 trellis_multi_stochastic failed
    682276          2025-08-17_v1 trellis_multi_stochastic failed
    682284          2025-08-17_v1 trellis_multi_stochastic failed

DRY RUN - no changes written to generations.csv

=== Migration Summary ===
  total_records: 802
  missing_items: 0
  path_warnings: 802
  dry_run: True
```

## Step 2: Copy Generated Files

Before running the actual migration, copy all generated .glb files from the legacy workspace to the new workspace:

```bash
# Copy the entire runs directory
cp -r "C:/Users/matti/testing/legacy/runs" "C:/Users/matti/testing/"

# OR copy specific run folder
cp -r "C:/Users/matti/testing/legacy/runs/2025-08-17_v1" "C:/Users/matti/testing/runs/"
```

**IMPORTANT**: The script preserves the original relative paths, so files must be copied to the same relative locations in the new workspace.

## Step 3: Run Migration

Once files are copied, run the migration without `--dry-run`:

```bash
python scripts/migrate_legacy_results.py \
    --legacy-csv "C:/Users/matti/testing/legacy/results_elaborated_enriched.csv" \
    --legacy-root "C:/Users/matti/testing/legacy"
```

This will:
1. Read legacy CSV and merge with current items.csv
2. Transform records to new schema
3. Upsert to `tables/generations.csv` (atomic, thread-safe)
4. Preserve original job_ids for continuity

Example output:
```
Upserting to generations.csv...
  Inserted: 802
  Updated: 0

=== Migration Summary ===
  total_records: 802
  inserted: 802
  updated: 0
  missing_items: 0
  path_warnings: 0
```

## Step 4: Verify Migration

Check that the migration was successful:

```bash
# Count migrated records
cd /path/to/workspace
grep -c "2025-08-17_v1" tables/generations.csv

# View sample records
head -n 5 tables/generations.csv
```

## Data Mapping

The script maps legacy columns to new format as follows:

### Preserved Fields (1:1 mapping)
- `run_id` → `run_id`
- `job_id` → `job_id` (preserved for continuity)
- `product_id` → `product_id`
- `variant` → `variant`
- `algo` → `algo`
- `duration_s` → `generation_duration_s`
- `unit_price_usd` → `unit_price_usd`
- `manufacturer`, `product_name`, `category_l1/l2/l3` → same

### Transformed Fields
- `output_glb_relpath` → `gen_object_path` (path normalization: backslash → forward slash)
- `n_images` → `used_n_images`
- `worker` → `worker_host` and `worker_user` (parsed from legacy format)

### Enriched Fields (from items.csv)
- `n_images` → `source_n_images` (total available images)
- `image_*_path` → `source_image_*_path` (all available source images)
- `gt_object_path` (ground truth object path)
- `description` (product description)

### New Fields (defaults)
- `algo_version` → `"legacy-v0.1.0"` (marks as migrated)
- `price_source` → `"legacy-csv"`
- `status` → `"completed"` (if output exists) or `"failed"`
- `notes` → `"Migrated from legacy v0.1.0 results"`
- `worker_env` → `"legacy"`
- `worker_commit` → `"unknown"`
- All VFScore metrics → empty (can be computed later with `archi3d compute vfscore`)

### Missing Fields (legacy didn't track)
- `used_image_*_path` → empty (legacy didn't track which specific images were used)
- `image_set_hash` → empty (cannot compute without knowing exact images)
- `created_at`, `generation_start`, `generation_end` → empty
- `preview_*_path` → empty (legacy didn't generate previews)

## Troubleshooting

### "No match in items.csv"

Some legacy records might not have a corresponding item in the current items.csv. This happens when:
- The dataset has changed (items added/removed)
- Product IDs or variants were renamed

**Solution**: Run `archi3d catalog build` to rebuild items.csv from the current dataset.

### "Legacy file not found"

The script checks if referenced .glb files exist. If files are missing:
- Verify you copied the `runs/` directory correctly
- Check that paths in legacy CSV match actual file locations
- Missing files will be marked as `status="failed"` in migrations

### "Different workspace paths"

Legacy and new workspaces must have compatible structure. The script assumes:
- Legacy paths: `runs\<run_id>\outputs\<algo>\<filename>.glb`
- New paths: same (converted to forward slashes)

## Advanced Usage

### Migrate Specific Run

To migrate only a specific run_id, pre-filter the legacy CSV:

```bash
# Extract specific run
grep "2025-08-17_v1" legacy/results_elaborated_enriched.csv > filtered.csv

# Migrate filtered CSV
python scripts/migrate_legacy_results.py \
    --legacy-csv filtered.csv \
    --legacy-root "C:/Users/matti/testing/legacy"
```

### Re-run Migration (Upsert)

The script uses atomic upsert (via job_id key), so you can safely re-run it:
- Existing records with same (run_id, job_id) will be updated
- New records will be inserted
- No duplicates will be created

### Compute VFScore for Migrated Data

After migration, you can compute visual fidelity metrics:

```bash
archi3d compute vfscore --run-id "2025-08-17_v1"
```

This will:
- Find all migrated jobs from that run
- Compute VFScore metrics for jobs with completed status
- Upsert metrics back to generations.csv

## Notes

- **Job ID Preservation**: Original job_ids are preserved to maintain continuity
- **Atomic Operations**: All CSV writes use file locks (safe for concurrent access)
- **Idempotent**: Safe to re-run - won't create duplicates
- **Backward Compatible**: Migrated records are marked with `algo_version="legacy-v0.1.0"`

## Support

For issues or questions about migration, check the logs or contact the development team.

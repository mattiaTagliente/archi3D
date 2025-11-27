# File Naming Redesign - Meaningful Asset Names

**Date**: 2025-11-26
**Status**: In Progress

## Problem

Current file naming uses generic names that don't include identifying metadata:
- Generated GLB: `generated.glb`
- VFScore GT: `refs/gt_selected.png`
- VFScore HQ render: `renders/hq_render.png`

This creates confusion when examining files, makes debugging harder, and doesn't provide context about what each file represents.

## Solution: Metadata-Rich Filenames

### Proposed Naming Scheme

#### Generated GLB Files
**Current**: `generated.glb`

**New**: `{product_id}_{variant}_{algo}_{job_id[:8]}.glb`

**Examples**:
```
335888_default_tripo3d_v2p5_a1b2c3d4.glb
335888_curved-backrest_meshy_v4_multi_b2c3d4e5.glb
123456_default_imagetothe3d_turbo_c3d4e5f6.glb
```

**Benefits**:
- Self-documenting: filename tells you what it is
- Unique: job_id prefix ensures no collisions
- Searchable: can grep for product_id, algo, etc.
- Portable: can move file and still know what it is

#### VFScore Artifact Directory
**Current**: `vfscore_artifacts/`

**New**: `vfscore_{job_id[:8]}/`

**Example**: `vfscore_a1b2c3d4/`

**Benefits**:
- Links directory to job_id immediately
- Shorter than full job_id
- Allows multiple VFScore runs in same output dir (future use case)

#### VFScore GT Image
**Current**: `refs/gt_selected.png`

**New**: `refs/gt_{product_id}_{variant}.png`

**Examples**:
```
refs/gt_335888_default.png
refs/gt_335888_curved-backrest.png
refs/gt_123456_default.png
```

**Benefits**:
- Know which product GT this is
- Easy to verify correctness
- Can identify mismatches

#### VFScore HQ Render
**Current**: `renders/hq_render.png`

**New**: `renders/render_{algo}_{job_id[:8]}.png`

**Examples**:
```
renders/render_tripo3d_v2p5_a1b2c3d4.png
renders/render_meshy_v4_multi_b2c3d4e5.png
renders/render_imagetothe3d_turbo_c3d4e5f6.png
```

**Benefits**:
- Know which algorithm generated this render
- Links to job_id for traceability
- Algorithm version visible in filename

### Filename Component Formatting

**variant**:
- Replace spaces with hyphens: `"Curved backrest"` → `"curved-backrest"`
- Lowercase for consistency
- Remove special characters except hyphens and underscores
- Use `"default"` if variant is empty string

**product_id**:
- Use as-is (already a string identifier)

**algo**:
- Use full algo key from REGISTRY: `"tripo3d_v2p5_multi"`, `"meshy_v4_multi"`, etc.
- Preserves version information

**job_id**:
- Use first 8 characters: `job_id[:8]`
- Sufficient for uniqueness in human-readable context
- Full job_id still in CSV for exact matching

## Implementation Plan

### Phase 1: Core Worker Changes ✅ (This Update)

**File**: `src/archi3d/orchestrator/worker.py`

Changes:
1. Add helper function `_format_variant_for_filename(variant: str) -> str`
2. Add helper function `_generate_glb_filename(product_id, variant, algo, job_id) -> str`
3. Update `_simulate_dry_run()` to use new filename
4. Update `_execute_job()` to use new filename for GLB download/copy

### Phase 2: Consolidation Updates ✅ (This Update)

**File**: `src/archi3d/orchestrator/consolidate.py`

Changes:
1. Update `_gather_job_artifacts()` to check for new GLB pattern
2. Add fallback to old `generated.glb` for backward compatibility
3. Update `_reconcile_job_status()` to use new pattern

### Phase 3: VFScore_RT Pipeline Changes (Separate PR/Rebuild)

**File**: `VFScore_RT/src/vfscore/objective2/pipeline_objective2.py`

Changes in `_export_artifacts()` method:
1. Accept additional parameters: `product_id`, `variant`, `algo`, `job_id`
2. Update directory name: `vfscore_{job_id[:8]}`
3. Update GT filename: `refs/gt_{product_id}_{variant}.png`
4. Update render filename: `renders/render_{algo}_{job_id[:8]}.png`
5. Update `artifacts.json` with new paths

**File**: `VFScore_RT/src/vfscore/evaluator.py`

Changes:
1. Extract metadata from `record` dict (product_id, variant, algo, job_id)
2. Pass metadata to pipeline's `_export_artifacts()` call

### Phase 4: Metrics Adapter Updates ✅ (This Update)

**File**: `src/archi3d/metrics/vfscore.py`

Changes:
1. Pass job metadata to VFScore adapter
2. No changes to VFScoreRequest (metadata comes from record in VFScore)

**File**: `src/archi3d/metrics/fscore.py`

Changes:
1. Update `_process_job()` to look for new GLB filename pattern
2. Add fallback to old `generated.glb` for backward compatibility

### Phase 5: Test Updates ✅ (This Update)

Update all test files to use new naming:
- `test_phase3_run_worker.py`
- `test_phase4_consolidate.py`
- `test_phase5_compute_fscore.py`
- `test_phase6_compute_vfscore.py`

### Phase 6: Documentation Updates ✅ (This Update)

Update all documentation files:
- `CLAUDE.md`
- `readme.md`
- `changelog.md`
- Plan files in `plans/`

## Backward Compatibility Strategy

To ensure existing runs continue to work:

1. **GLB Lookup Order**:
   - First try new pattern: `{product_id}_{variant}_{algo}_{job_id[:8]}.glb`
   - Fallback to old: `generated.glb`
   - Log when fallback is used

2. **VFScore Artifacts**:
   - New runs create new structure
   - Old runs keep old structure
   - CSV columns remain the same (paths are stored as-is)

3. **Migration**:
   - No forced migration of existing files
   - New jobs use new naming automatically
   - Can add `--migrate` command later if needed

## File Locations After Change

### Example Job: Product 335888, Variant "Curved backrest", Algo "tripo3d_v2p5_multi"

```
runs/2025-11-22-run/outputs/a1b2c3d4ef56.../
├── 335888_curved-backrest_tripo3d_v2p5_a1b2c3d4.glb  # Generated model (NEW)
└── vfscore_a1b2c3d4/                                  # VFScore artifacts (NEW)
    ├── refs/
    │   └── gt_335888_curved-backrest.png              # GT image (NEW)
    ├── renders/
    │   └── render_tripo3d_v2p5_a1b2c3d4.png          # HQ render (NEW)
    └── artifacts.json                                 # Descriptor
```

### CSV Path References

**generations.csv `gen_object_path` column**:
```
runs/2025-11-22-run/outputs/a1b2c3d4ef56.../335888_curved-backrest_tripo3d_v2p5_a1b2c3d4.glb
```

**generations.csv `vf_artifacts_dir` column**:
```
vfscore_a1b2c3d4
```

**generations.csv `vf_gt_image_path` column** (relative to artifacts_dir):
```
refs/gt_335888_curved-backrest.png
```

**generations.csv `vf_render_image_path` column** (relative to artifacts_dir):
```
renders/render_tripo3d_v2p5_a1b2c3d4.png
```

## Testing Checklist

- [ ] Worker creates new GLB filenames correctly
- [ ] Dry-run mode uses new filenames
- [ ] Consolidation finds new GLB files
- [ ] Consolidation falls back to old GLB files for legacy runs
- [ ] FScore finds new GLB files
- [ ] VFScore creates new artifact structure (after VFScore_RT rebuild)
- [ ] VFScore reads artifacts correctly from new structure
- [ ] All phase tests pass with new naming
- [ ] Backward compatibility: old runs still work

## Rollout Plan

### Step 1: archi3D Changes (This Session)
1. Update worker.py, consolidate.py, metrics adapters
2. Update tests
3. Update documentation
4. Test with dry-run mode
5. Commit and tag

### Step 2: VFScore_RT Changes (Next Session)
1. Update pipeline_objective2.py
2. Update evaluator.py
3. Test standalone
4. Build new wheel
5. User rebuilds and reinstalls VFScore_RT

### Step 3: Integration Testing
1. Run full archi3D workflow with new VFScore_RT
2. Verify all files have new names
3. Verify metrics computation works
4. Check backward compatibility with old runs

### Step 4: Documentation Update
1. Update user-facing documentation
2. Add migration notes if needed
3. Update examples and screenshots

## Success Criteria

✅ GLB files have meaningful names with metadata
✅ VFScore artifacts have meaningful names
✅ Files are self-documenting and searchable
✅ Backward compatibility preserved for old runs
✅ All tests pass
✅ Documentation updated

## Notes

- This is a breaking change for new runs but doesn't affect existing runs
- Users will need to rebuild VFScore_RT to get new artifact names
- CSV schema doesn't change - just the values stored in path columns
- Improves debuggability and reduces confusion significantly

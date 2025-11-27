# File Naming Update - Implementation Status

**Date**: 2025-11-26
**Status**: Core Changes Complete, Testing Pending

## Summary

Implemented meaningful file naming for generated assets and VFScore artifacts. Files now include metadata (product_id, variant, algo, job_id) making them self-documenting and easier to debug.

## Completed Changes ✅

### 1. Design Document
- ✅ Created `FILE_NAMING_REDESIGN.md` with comprehensive naming scheme
- ✅ Documented new patterns, backward compatibility strategy, and rollout plan

### 2. Worker Updates (`src/archi3d/orchestrator/worker.py`)
- ✅ Added `_format_variant_for_filename()` helper
- ✅ Added `_generate_glb_filename()` helper
- ✅ Updated `_simulate_dry_run()` to use new naming
- ✅ Updated `_execute_job()` to generate meaningful GLB filenames

**New GLB Format**: `{product_id}_{variant}_{algo}_{job_id[:8]}.glb`

Example: `335888_curved-backrest_tripo3d_v2p5_a1b2c3d4.glb`

### 3. Consolidation Updates (`src/archi3d/orchestrator/consolidate.py`)
- ✅ Added file naming helper functions (copied from worker.py)
- ✅ Updated `_gather_evidence()` to check both new and legacy GLB filenames
- ✅ Updated `_reconcile_row()` to populate gen_object_path with correct filename
- ✅ Backward compatibility: Falls back to `generated.glb` for old runs

### 4. Metrics Adapters (No Changes Needed)
- ✅ FScore uses `row["gen_object_path"]` from CSV - works automatically
- ✅ VFScore uses `row["gen_object_path"]` from CSV - works automatically
- ✅ Consolidation ensures correct paths are stored in CSV

### 5. VFScore_RT Implementation
- ✅ Updated `evaluator.py` to accept `algo` parameter
- ✅ Updated `_export_scoring_artifacts()` signature with metadata parameters
- ✅ Added `_format_variant_for_filename()` helper function
- ✅ Updated directory naming: `vfscore_{job_id[:8]}/`
- ✅ Updated GT filename: `refs/gt_{product_id}_{variant}.png`
- ✅ Updated render filename: `renders/render_{algo}_{job_id[:8]}.png`
- ✅ Updated both call sites in pipeline_objective2.py
- ✅ Updated archi3D vfscore_adapter.py to pass algo parameter
- ✅ Created `ARTIFACT_NAMING_UPDATE.md` documentation

**New VFScore Artifact Format**:
- Directory: `vfscore_{job_id[:8]}/`
- GT Image: `refs/gt_{product_id}_{variant}.png`
- HQ Render: `renders/render_{algo}_{job_id[:8]}.png`

## Pending Changes ⏳

### 1. Test Updates
The following test files need updates to use new naming:

**Priority 1 - Worker Tests**:
- `tests/test_phase3_run_worker.py`
  - Update `test_dry_run_success()` to check for new GLB filename
  - Update test fixtures to create files with new names

**Priority 2 - Consolidation Tests**:
- `tests/test_phase4_consolidate.py`
  - Update `_create_test_job()` helper to use new GLB naming
  - Add test for backward compatibility (legacy filename fallback)
  - Update expectations for gen_object_path values

**Priority 3 - Metrics Tests**:
- `tests/test_phase5_compute_fscore.py`
  - Update fixtures to create GLB files with new naming
- `tests/test_phase6_compute_vfscore.py`
  - Update fixtures to create GLB files with new naming

### 2. Documentation Updates
- `CLAUDE.md` - Update file naming sections
- `readme.md` - Update examples and output structure
- `changelog.md` - Add entry for file naming redesign

## Testing Strategy

### Phase 1: Basic Validation (Recommended First)
```bash
# Test dry-run with new naming
archi3d batch create --run-id "test-naming" --limit 1
archi3d run worker --run-id "test-naming" --dry-run --limit 1

# Check output
ls "C:/Users/matti/testing/runs/test-naming/outputs/*"
# Should see: {product_id}_{variant}_{algo}_{job_id[:8]}.glb

# Test consolidation
archi3d consolidate --run-id "test-naming"
# Should populate gen_object_path correctly

# Check CSV
cat "C:/Users/matti/testing/tables/generations.csv" | grep "test-naming"
# Should show new GLB filename in gen_object_path column
```

### Phase 2: Backward Compatibility
```bash
# Create legacy run (before code changes)
# 1. Revert to old code
# 2. Run worker
# 3. Restore new code
# 4. Run consolidate
# Should still find generated.glb and work correctly
```

### Phase 3: Full Integration
```bash
# Run full workflow
archi3d catalog build
archi3d batch create --run-id "full-test"
archi3d run worker --run-id "full-test" --limit 3
archi3d consolidate --run-id "full-test"
archi3d compute fscore --run-id "full-test"
# After VFScore_RT rebuild:
archi3d compute vfscore --run-id "full-test"
```

### Phase 4: Unit Tests
```bash
# Run all tests (after updating test files)
pytest tests/test_phase3_run_worker.py -v
pytest tests/test_phase4_consolidate.py -v
pytest tests/test_phase5_compute_fscore.py -v
pytest tests/test_phase6_compute_vfscore.py -v
```

## Rollout Plan

### Step 1: archi3D Core Changes (✅ DONE)
- Worker, consolidation, and metrics adapters updated
- Backward compatibility in place

### Step 2: Testing & Validation (NEXT)
- **Option A**: User tests manually with real data
- **Option B**: Update unit tests first, then manual testing
- **Recommended**: Option A (faster feedback loop)

### Step 3: Update Tests (After Manual Validation)
- Update test files to match new naming
- Add backward compatibility tests
- Ensure all tests pass

### Step 4: VFScore_RT Changes (User Will Handle)
- Implement changes per ARTIFACT_NAMING_UPDATE.md
- Rebuild and reinstall VFScore_RT
- Test VFScore artifact naming

### Step 5: Documentation Updates
- Update CLAUDE.md with new naming examples
- Update readme.md with output structure
- Add changelog entry
- Update any screenshots or examples

### Step 6: Final Integration Testing
- Run full workflow end-to-end
- Verify all components work together
- Check CSV has correct paths
- Verify VFScore artifacts have meaningful names

## Breaking Changes

✅ **Backward Compatible**: Old runs with `generated.glb` continue to work
✅ **CSV Schema Unchanged**: Only the values in path columns change
❌ **New Runs Required**: Existing incomplete runs won't use new naming until rerun

## Questions for User

1. **Testing Preference**: Should I update the unit tests now, or would you prefer to test manually first?

2. **VFScore_RT**: Do you want to implement the VFScore_RT changes now, or after validating archi3D changes?

3. **Documentation**: Should I update CLAUDE.md and readme.md now, or after successful testing?

## Recommended Next Steps

**If user wants to test immediately**:
1. Test dry-run mode with new naming
2. Verify consolidation works
3. Check CSV has correct paths
4. Report any issues

**If user wants complete implementation**:
1. I'll update all test files
2. I'll update documentation
3. User can then run full test suite
4. User implements VFScore_RT changes

Please let me know which approach you prefer!

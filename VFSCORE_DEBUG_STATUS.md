# VFScore Debug Status - Current Findings

## Summary

I've made progress on both issues but we need your help to complete the fixes.

## Issue #1: Workspace Directory (PARTIALLY FIXED - TESTING NEEDED)

### What I Fixed
- ✅ Modified `archi3D/src/archi3d/metrics/vfscore_adapter.py` line 143 to pass workspace parameter
- ✅ Confirmed the fix is in the code (verified with source inspection)

### Why It's Still Showing Relative Paths
The debug output still shows:
```
gt_preprocess_dir: outputs\preprocess\refs\0c7252b31588
```

This suggests one of two possibilities:
1. **VFScore config is ignoring workspace parameter** - VFScore's `load_config(workspace_path=...)` may not be properly using it to set `config.paths.out_dir`
2. **Need to restart Python** - The evaluator module might be cached in memory

### Next Steps for You
1. **Restart the Python process** - Close your PowerShell session and start a new one
2. **Re-run the test**:
   ```powershell
   cd "C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\archi3D"
   archi3d compute vfscore --run-id "2025-11-22T21-25-08Z" --jobs "0c7252b31588" --redo
   ```
3. **Check if `gt_preprocess_dir` now shows absolute path** starting with `C:\Users\matti\testing\`

## Issue #2: Silent Crash During Step 2 (DEBUG LOGGING ADDED)

### What I Found
The crash happens in `prerender_library.py` during the `select_poses_by_criterion()` function, specifically in the "iou_ar" selection mode. The code:
1. Prints "IoU+AR hybrid selection: finding 1 candidates from 810 poses"
2. Prints "Using top 3.0% fraction..."
3. **Then crashes/hangs without printing anything else**

### What I Added
Added debug logging to `VFScore_RT/src/vfscore/objective2/prerender_library.py` (lines 1263-1286):
- `[DEBUG] Sorting by IoU...`
- `[DEBUG] Sorting by AR...`
- `[DEBUG] Extracting top N candidates...`
- `[DEBUG] Computing AR threshold...`

These will help pinpoint exactly which operation is failing.

### Why It Might Be Crashing
The function is sorting **810 candidates**, each containing:
- yaw, elev, iou, area, ar_error values
- **A full 1024×1024 numpy array (mask)** = ~1MB each

Total memory: 810 × 1MB = **~850MB** just for the candidate list, and Python's sort creates copies.

**Hypothesis**: Either:
1. **Memory exhaustion** - Windows kills the process
2. **GPU/OpenGL crash** - PyRender context dies silently
3. **Exception in C extension** - numpy/scipy operation crashes

### Next Steps for You

**Option A: Rebuild VFScore with debug logging** (Recommended)

Follow your usual process in `REBUILD_WHEEL.md`:

```powershell
cd "C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\VFScore_RT"

# Clean
Remove-Item -Recurse -Force build, dist, *.egg-info -ErrorAction SilentlyContinue

# Build (you may need to activate a specific environment first)
python setup.py bdist_wheel

# Install
pip install --force-reinstall "dist/vfscore_rt-0.2.0-cp311-cp311-win_amd64.whl"
```

Then re-run the test and see which `[DEBUG]` message is the last one printed.

**Option B: Monitor system resources**

While running the test, watch:
- Task Manager -> Performance -> Memory (does it hit 100%?)
- Task Manager -> Performance -> GPU (does it crash?)
- Windows Event Viewer -> Application (any crash logs?)

**Option C: Reduce calibration complexity** (Quick workaround)

Edit `VFScore_RT/config.yaml`:
```yaml
objective:
  objective2:
    radius_calibration:
      yaw_search_coarse_step: 30  # Reduce from 10 (3x fewer poses)
      resolution: 128  # Reduce from 256 (4x less memory)
      elevation_search_enabled: false
```

This would reduce from 810 renders to ~12 renders, avoiding the memory issue entirely.

## Files Modified (Ready to Test)

### archi3D
- ✅ `src/archi3d/metrics/vfscore_adapter.py` (line 143) - workspace parameter fix

### VFScore_RT (Needs rebuild)
- ✅ `src/vfscore/objective2/prerender_library.py` (lines 1263-1286) - debug logging

## What I Couldn't Do

I tried to rebuild VFScore for you but hit environment issues:
- The Bash tool doesn't properly activate your venv
- Cython is installed in global Python but not in your venv
- I can't reliably build the wheel from my end

**You'll need to rebuild VFScore yourself following REBUILD_WHEEL.md**

## Recommended Test Sequence

1. **Test workspace fix first** (no rebuild needed):
   - Restart PowerShell
   - Re-run archi3d compute vfscore
   - Check if paths are now absolute

2. **Rebuild VFScore with debug logging**:
   - Follow REBUILD_WHEEL.md
   - Re-run test
   - See which `[DEBUG]` line is the last printed

3. **If still crashes silently**:
   - Try Option C (reduce calibration complexity)
   - Check Event Viewer for crash logs
   - Monitor system resources

## Questions to Help Debug

When you re-run the test, please let me know:
1. Does `gt_preprocess_dir` show an absolute path now?
2. Which `[DEBUG]` message is the last one printed?
3. Does RAM or GPU usage spike before crash?
4. Any errors in Windows Event Viewer?


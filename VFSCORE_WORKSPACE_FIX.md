# VFScore Workspace Directory Fix

## Issue #1: Wrong Workspace Directory (FIXED)

### Problem
VFScore preprocessing was saving artifacts to a relative path (`outputs/preprocess/refs/...`) in the current working directory instead of using the configured `ARCHI3D_WORKSPACE` directory.

### Root Cause
The `vfscore_adapter.py` was not passing the `workspace` parameter to the VFScore `evaluate_visual_fidelity()` function, even though:
1. The workspace path was available in `VFScoreRequest.workspace` (line 242 in `vfscore.py`)
2. The evaluator function signature accepts `workspace: str | None = None` (line 21 in `evaluator.py`)
3. The evaluator properly uses workspace to configure VFScore paths (line 101-102 in `evaluator.py`)

### Fix Applied
**File**: `C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\archi3D\src\archi3d\metrics\vfscore_adapter.py`

**Line 136-144** (modified):
```python
start_total = time.perf_counter()
result = evaluate_visual_fidelity(
    cand_glb=str(req.cand_glb),
    ref_images=[str(p) for p in req.ref_images],
    out_dir=str(req.out_dir),
    repeats=req.repeats,
    timeout_s=req.timeout_s,
    workspace=str(req.workspace) if req.workspace else None,  # <- NEW LINE
)
total_runtime = time.perf_counter() - start_total
```

### Expected Result
After this fix, VFScore preprocessing will save artifacts to:
```
{ARCHI3D_WORKSPACE}/outputs/preprocess/refs/{gt_id}/
```

Instead of:
```
./outputs/preprocess/refs/{gt_id}/
```

## Issue #2: Silent Pipeline Failure During Step 2 (UNDER INVESTIGATION)

### Symptoms
The VFScore pipeline stops execution during Step 2 with the last output:
```
Step2_iter1IoU+AR hybrid selection: finding 1 candidates from 810 poses
Step2_iter1  Using top 3.0% fraction for both IoU and AR constraints
```

Then the program silently exits without error messages or completing the pipeline.

### Analysis
Based on code inspection:

1. **Step 2 Location**: The message comes from the radius calibration phase in `prerender_library.py:1261-1262`

2. **What Should Happen Next**:
   - Print AR threshold (line 1275)
   - Print qualified candidates (line 1283 or 1287)
   - Print selected candidates (lines 1335-1346)
   - Continue with library generation and matching

3. **Why It Might Fail Silently**:
   - **Long computation**: The calibration involves rendering 810 poses at 256px resolution, which can take significant time (estimate: 5-15 minutes depending on model complexity)
   - **Memory exhaustion**: Processing 810 renders might exhaust RAM, causing Python to be killed by OS
   - **GPU/renderer issues**: PyRender/OpenGL context issues on Windows
   - **Exception in Cython code**: If exception occurs in compiled `.pyd` module, it might not propagate properly

4. **Exception Handling Check**: The evaluator has proper try-catch (lines 168-176 in `evaluator.py`) that should print exceptions, so the failure is likely:
   - Process killed by OS (memory/timeout)
   - Infinite loop or hang (unlikely given the tqdm progress bars in code)
   - GPU driver crash (silent on Windows)

### Recommended Investigation Steps

1. **Check System Resources**:
   ```bash
   # Monitor RAM and GPU usage while running
   # Task Manager -> Performance tab
   ```

2. **Add Debug Logging**:
   - Modify `prerender_library.py:1262` to add flush:
   ```python
   print(f"{label}  Using top {iou_ar_top_fraction*100:.1f}% fraction for both IoU and AR constraints")
   import sys; sys.stdout.flush()
   ```

3. **Reduce Calibration Complexity**:
   - Check `VFScore_RT/config.yaml` for calibration settings:
   ```yaml
   objective:
     objective2:
       radius_calibration:
         yaw_search_coarse_step: 30  # Increase from 10 to reduce poses (reduces 810 -> 270)
         resolution: 128  # Reduce from 256 to save memory
   ```

4. **Test with Simpler Model**:
   - Try with a low-poly test GLB to verify it's not model-specific

5. **Check Windows Event Viewer**:
   - Look for application crashes or "Application Error" events
   - Path: Windows Logs -> Application

6. **Add Timeout**:
   - Use `--timeout-s 600` flag to force timeout after 10 minutes
   ```bash
   archi3d compute vfscore --run-id "..." --jobs "..." --timeout-s 600
   ```

### Potential Quick Fix (Untested)
If the issue is excessive calibration complexity, edit VFScore config to use faster settings:

**File**: `C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\VFScore_RT\config.yaml`

```yaml
objective:
  objective2:
    radius_calibration:
      # Faster calibration (less accurate but more stable)
      yaw_search_coarse_step: 30  # Default: 10 (reduces poses 3x)
      elevation_search_enabled: false  # Disable elevation search
      resolution: 128  # Default: 256 (reduces memory 4x)
      coarse_iterations: 1  # Default: 2 (reduces iterations 2x)
```

This would reduce Step 2 from ~810 renders to ~12 renders.

## Next Steps

1. **Test Workspace Fix**:
   - No need to recompile VFScore (fix is in archi3D only)
   - Just re-run the test:
   ```bash
   cd "C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\archi3D"
   archi3d compute vfscore --run-id "2025-11-22T21-25-08Z" --jobs "0c7252b31588" --redo
   ```

2. **If Still Hangs on Step 2**:
   - Monitor system resources (RAM, GPU)
   - Check Windows Event Viewer
   - Try reducing calibration complexity as described above
   - Consider adding debug logging to pinpoint exact failure location

3. **Recompile VFScore** (only needed for preprocessing changes from earlier):
   - Follow instructions in `VFScore_RT\REBUILD_WHEEL.md`
   - Note: The workspace fix doesn't require VFScore recompilation

## Files Modified

- `archi3D/src/archi3d/metrics/vfscore_adapter.py` (line 143): Added workspace parameter

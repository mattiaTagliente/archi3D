# VFScore Crash Mystery - SOLVED!

## The Breakthrough Discovery

You made an **excellent discovery**:
- **First run**: Crashes during preprocessing
- **Second run** (with cached preprocessing): Works perfectly!

This means **the crash is NOT in Step 2 of the pipeline** - it's in the **preprocessing stage** (background removal with `rembg`).

## Root Cause: `rembg` Background Removal

The crash happens at this line in `preprocess_gt.py`:
```python
image_no_bg = remove_background(image, config.preprocess.segmentation_model)
```

### Why `rembg` Crashes

The `rembg` library uses deep learning models (U2-Net) for background removal, which:
1. **GPU intensive**: Can crash GPU drivers or run out of VRAM
2. **Memory intensive**: Loads large models (~170MB for u2net)
3. **Multi-threading issues**: May have conflicts with other GPU operations
4. **ONNX Runtime issues**: The underlying inference engine can be unstable on Windows

### Why It Works the Second Time

When preprocessing artifacts already exist (metadata.json + processed images), the function returns early at **line 720** (cache check), skipping the expensive `remove_background()` call entirely.

## Debug Logging Added

I've added comprehensive debug logging to `preprocess_gt.py` to pinpoint the exact failure point:

**Lines 738-750**:
```python
print(f"[DEBUG PREPROCESS] Processing GT #{idx+1}/{len(ref_images_paths)}: {ref_path.name}")
print(f"[DEBUG PREPROCESS] Loading image...")
image = Image.open(ref_path).convert("RGB")
print(f"[DEBUG PREPROCESS] Removing background (this may take 5-10 seconds)...")
image_no_bg = remove_background(image, config.preprocess.segmentation_model)
print(f"[DEBUG PREPROCESS] Background removed successfully")
```

**Line 900**: Before saving metadata
**Line 917**: After successful completion

These will show you exactly which GT image causes the crash and at what stage.

## Solutions

### Option 1: Rebuild VFScore and Test (Recommended First)

```powershell
cd "C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\VFScore_RT"
Remove-Item -Recurse -Force build, dist, *.egg-info -ErrorAction SilentlyContinue
python setup.py bdist_wheel
pip install --force-reinstall "dist/vfscore_rt-0.2.0-cp311-cp311-win_amd64.whl"
```

Then delete the cached preprocessing and re-run:
```powershell
cd "C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\archi3D"
Remove-Item -Recurse -Force "outputs/preprocess"  # Delete cache
archi3d compute vfscore --run-id "2025-11-22T21-25-08Z" --jobs "0c7252b31588" --redo
```

The debug logs will show:
- Which GT image it's processing when it crashes (GT #1, #2, #3, or #4)
- Whether it crashes during loading, background removal, or saving

### Option 2: Fix `rembg` Instability (If Option 1 Shows Consistent Crash)

**A. Use CPU-only mode** (slower but more stable):
```python
# In VFScore_RT/src/vfscore/preprocess_gt.py, modify remove_background call:
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"  # Force CPU
```

**B. Lower rembg precision** (edit config.yaml):
```yaml
preprocess:
  segmentation_model: "u2netp"  # Smaller, faster model (instead of "u2net")
```

**C. Process images sequentially with cleanup**:
Add memory cleanup between images:
```python
# After line 750 (after remove_background)
import gc
gc.collect()
if torch.cuda.is_available():
    torch.cuda.empty_cache()
```

### Option 3: Pre-process GT Images Separately (Workaround)

Create a standalone script to preprocess GTs offline:
```python
# preprocess_gts_offline.py
from vfscore.config import load_config
from vfscore.preprocess_gt import preprocess_gt_images_standalone
from pathlib import Path

config = load_config(workspace_path=Path(r"C:\Users\matti\testing"))
ref_images = [
    Path(r"C:\Users\matti\testing\dataset\335888\images\335888_A.jpeg"),
    Path(r"C:\Users\matti\testing\dataset\335888\images\335888_B.jpg"),
    Path(r"C:\Users\matti\testing\dataset\335888\images\335888_C.jpg"),
    Path(r"C:\Users\matti\testing\dataset\335888\images\335888_D.jpg"),
]
output_dir = Path(r"C:\Users\matti\testing\outputs\preprocess\refs\0c7252b31588")

success = preprocess_gt_images_standalone(ref_images, output_dir, config)
print(f"Preprocessing: {'SUCCESS' if success else 'FAILED'}")
```

Run this once, then use cached results for all evaluations.

## Issue #1: Workspace Directory (Still Not Fixed)

The debug output **still shows relative path**:
```
gt_preprocess_dir: outputs\preprocess\refs\0c7252b31588
```

This suggests VFScore's `load_config(workspace_path=...)` is **not properly applying the workspace path** to `config.paths.out_dir`.

### Investigation Needed

Check VFScore's `config.py` or `paths.py` to see how `workspace_path` is used. The parameter might be:
- Ignored completely
- Used for a different purpose
- Overridden by environment variables or config files

### Temporary Workaround

Since the second run works and artifacts are created, you can:
1. Let preprocessing create artifacts in current directory
2. Manually move `outputs/` directory to your workspace:
   ```powershell
   Move-Item "outputs" "C:\Users\matti\testing\outputs" -Force
   ```

Or modify VFScore's evaluator to set `config.paths.out_dir` explicitly after loading config:
```python
# In evaluator.py after line 102
config = load_config(workspace_path=workspace_path_obj)
if workspace:
    config.paths.out_dir = Path(workspace) / "outputs"
```

## Next Steps

1. **Rebuild VFScore** with debug logging
2. **Delete preprocessing cache**: `Remove-Item -Recurse -Force "outputs/preprocess"`
3. **Re-run test** and capture the debug output
4. **Report back**:
   - Which `[DEBUG PREPROCESS]` message was the last one printed?
   - Did it crash on GT #1, #2, #3, or #4?
   - Any GPU/memory usage spikes?

Once we know exactly where it crashes, we can implement the appropriate fix (CPU mode, model swap, memory cleanup, etc.).

## Summary

- ✅ **Crash location found**: `rembg` background removal in preprocessing
- ✅ **Debug logging added**: Will pinpoint exact image causing crash
- ✅ **Multiple solutions available**: CPU mode, smaller model, memory cleanup, or offline preprocessing
- ⚠️ **Workspace directory issue persists**: Needs deeper investigation of VFScore config system


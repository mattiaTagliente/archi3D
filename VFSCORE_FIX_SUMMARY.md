# VFScore Integration Fix Summary

## Problem Analysis

The VFScore `evaluate_visual_fidelity()` API was failing with "No GT images found" because:

1. **Root Cause**: The evaluator was copying raw reference images (`ref_00.jpeg`, `ref_01.jpg`, etc.) to the GT directory, but the VFScore pipeline expected **preprocessed** images with:
   - Background removed via `rembg`
   - Specific naming convention (`gt_1_square.png`, `gt_1_mask_square.png`, etc.)
   - A `metadata.json` file with structured information about the preprocessed images

2. **Pipeline Expectations**: The `Objective2Pipeline` calls `MultiGTMatcher.load_gt_images_from_metadata()` which:
   - First looks for `metadata.json` in the GT directory
   - If found, loads images based on paths specified in metadata
   - If not found, falls back to looking for files named `gt_1.png`, `gt_2.png`, etc.

3. **Why Raw Images Failed**:
   - No `metadata.json` file existed
   - Fallback expected `gt_1.png` format but found `ref_00.jpeg`, `ref_01.jpg`, etc.
   - Even if renamed, raw images lack background removal and proper cropping

## Solution Implemented

### 1. Created Standalone Preprocessing Function

**File**: `C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\VFScore_RT\src\vfscore\preprocess_gt.py`

Added function `preprocess_gt_images_standalone()` at line 673:
- Accepts list of reference image paths, output directory, and config
- Performs complete preprocessing workflow:
  1. **Background Removal**: Uses `rembg` with specified segmentation model
  2. **GT Analysis**: Calculates background ratio for each image
  3. **GT Selection** (optional): Selects image with minimum background ratio
  4. **Square Crop**: Creates square versions via `crop_exact_and_pad_square()`
  5. **Exact Crop** (optional): Creates exact bbox versions
  6. **Mask Extraction**: Extracts alpha channel as separate mask files
  7. **Metadata Generation**: Creates `metadata.json` with all file paths and metadata

**Key Features**:
- Cache-aware: Skips preprocessing if valid outputs already exist
- Configurable: Respects all VFScore preprocessing config options
- Standalone: No manifest file required (unlike `run_preprocess_gt()`)

### 2. Modified Evaluator to Call Preprocessing

**File**: `C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\VFScore_RT\src\vfscore\evaluator.py`

Changes at lines 113-160:
- Imports `preprocess_gt_images_standalone` and `make_gt_id`
- Calls preprocessing function before pipeline execution
- Verifies `metadata.json` exists after preprocessing
- Removed old code that just copied raw files

**Before**:
```python
# Copy reference images to preprocessing directory
for idx, ref_img in enumerate(ref_images_paths):
    dest = gt_preprocess_dir / f"ref_{idx:02d}{ref_img.suffix}"
    if not dest.exists():
        shutil.copy2(ref_img, dest)
```

**After**:
```python
# Preprocess GT images (removes background, creates square/exact versions, generates metadata)
print(f"[DEBUG] Preprocessing {len(ref_images_paths)} GT image(s)...")
preprocessing_success = preprocess_gt_images_standalone(
    ref_images_paths,
    gt_preprocess_dir,
    config
)

if not preprocessing_success:
    raise RuntimeError("Failed to preprocess GT images")

# Verify preprocessing outputs
metadata_path = gt_preprocess_dir / "metadata.json"
if not metadata_path.exists():
    raise RuntimeError(f"GT preprocessing did not produce metadata.json at {metadata_path}")
```

## Files Modified

1. **`vfscore/preprocess_gt.py`**: Added `preprocess_gt_images_standalone()` function (lines 673-902)
2. **`vfscore/evaluator.py`**: Modified GT handling (lines 113-160) to call preprocessing

## Expected Output Structure

After preprocessing, the GT directory will contain:

```
outputs/preprocess/refs/0c7252b31588/
├── metadata.json              # Structured metadata about all GTs
├── gt_1_square.png            # Square-cropped RGB image (for pose estimation)
├── gt_1_mask_square.png       # Alpha mask of square version
├── gt_2_square.png            # Second GT (if multiple images, selection disabled)
├── gt_2_mask_square.png
├── gt_3_square.png
├── gt_3_mask_square.png
├── gt_4_square.png
└── gt_4_mask_square.png
```

If `exact_crop_enabled: true` in config:
```
├── gt_1_exact.png             # Exact bbox crop (for final scoring)
├── gt_1_exact_mask.png
└── ...
```

## Testing Instructions

### Step 1: Recompile VFScore Wheel

```bash
cd "C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\VFScore_RT"
pip install -e . --no-build-isolation --force-reinstall
```

This will recompile the Cython modules with the new preprocessing function.

### Step 2: Test VFScore Evaluation

```bash
cd "C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\archi3D"
archi3d compute vfscore --run-id "2025-11-22T21-25-08Z" --jobs "0c7252b31588" --redo
```

### Expected Success Output

```
╭───────────────────────────╮
│ Compute VFScore (Phase 6) │
│ Run: 2025-11-22T21-25-08Z │
│ Jobs filter: 0c7252b31588 │
│ Only status: completed    │
│ Use images from: used     │
│ Repeats: 1                │
│ Redo: True                │
│ Timeout: —s               │
│ Max parallel: 1           │
│ Limit: —                  │
│ Dry-run: False            │
╰───────────────────────────╯
[DEBUG] Preprocessing 4 GT image(s)...
[DEBUG] VFScore record:
  item_id: 0c7252b31588
  product_id: 0c7252b31588
  variant:
  glb_path: C:\Users\matti\testing\runs\2025-11-22T21-25-08Z\outputs\0c7252b31588\generated.glb
  glb exists: True
  gt_id: 0c7252b31588
  gt_preprocess_dir: outputs\preprocess\refs\0c7252b31588
  gt_preprocess_dir exists: True
  gt files (9): ['gt_1_mask_square.png', 'gt_1_square.png', 'gt_2_mask_square.png', ...]
[DEBUG] Calling pipeline._process_item()
0c7252b31588: STAGE 1 - Loading ground truth images...
  Found 1 GT image(s) in single (selected) mode (0.2s)
0c7252b31588: STAGE 2 - Rendering library (1024x1024)...
  ...
VFScore computation complete!
 VFScore Computation Summary
┏━━━━━━━━━━━┳━━━━━━━┓
┃ Metric    ┃ Count ┃
┡━━━━━━━━━━━╇━━━━━━━┩
│ Selected  │     1 │
│ Processed │     1 │
│ OK        │     1 │
│ Error     │     0 │
│ Skipped   │     0 │
└───────────┴───────┘
```

## Configuration Notes

The preprocessing behavior is controlled by VFScore config:

**Key Settings**:
- `preprocess.gt_selection_enabled`: If `true`, selects single best GT (minimum background ratio)
- `preprocess.exact_crop_enabled`: If `true`, generates exact crop versions for final scoring
- `preprocess.segmentation_model`: Background removal model (default: "u2net")
- `preprocess.canvas_px`: Square canvas size (default: 2048)
- `preprocess.bg_rgb`: Background color for padding (default: [128, 128, 128])

## Performance Considerations

1. **Preprocessing is Cached**: If `metadata.json` and all required files exist, preprocessing is skipped
2. **Background Removal is Slow**: Expect 3-10 seconds per image depending on resolution
3. **First Run Only**: Subsequent evaluations with same GT images will reuse preprocessed files

## Troubleshooting

If preprocessing fails:

1. **Check rembg Installation**: Ensure `rembg` package is installed with model weights
2. **Check Disk Space**: Preprocessing creates multiple files per GT image
3. **Check Permissions**: Ensure write access to `outputs/preprocess/` directory
4. **Check Image Format**: Ensure reference images are valid (JPEG/PNG)

## Backward Compatibility

This fix maintains full backward compatibility:

- Existing VFScore workflows unchanged (manifest-based preprocessing still works)
- New `preprocess_gt_images_standalone()` is additive, doesn't modify existing functions
- Cache mechanism prevents redundant preprocessing
- All existing config options are respected

## Next Steps

After successful testing:

1. Update archi3D CLAUDE.md with VFScore preprocessing notes
2. Update VFScore wheel distribution if needed
3. Document preprocessing cache behavior for users
4. Consider adding `--skip-preprocessing` flag for advanced users with pre-prepared GTs

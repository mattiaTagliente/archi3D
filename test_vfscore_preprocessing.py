"""Test script to verify VFScore GT preprocessing integration.

This script tests whether the evaluator properly preprocesses GT images
before passing them to the VFScore pipeline.
"""

import sys
from pathlib import Path
import json
import tempfile
import shutil

# Add VFScore to path
vfscore_path = Path(r"C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\VFScore_RT\src")
sys.path.insert(0, str(vfscore_path))

from vfscore.config import load_config
from vfscore.preprocess_gt import remove_background, get_exact_bbox, calculate_background_ratio, resize_to_max_dimension, crop_to_square_or_exact
from PIL import Image
import numpy as np

def preprocess_gt_images_standalone(
    ref_images: list[Path],
    output_dir: Path,
    config
) -> bool:
    """Standalone GT preprocessing for archi3D integration.

    This function replicates the essential preprocessing steps without
    requiring a manifest file.

    Args:
        ref_images: List of paths to reference images
        output_dir: Directory to write preprocessed images
        config: VFScore config object

    Returns:
        True if successful, False otherwise
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Stage 1: Analyze all GTs
    print(f"Analyzing {len(ref_images)} GT image(s)...")
    gt_analysis = []

    for idx, ref_path in enumerate(ref_images):
        if not ref_path.exists():
            print(f"Warning: GT image not found: {ref_path}")
            continue

        # Load and remove background
        image = Image.open(ref_path).convert("RGB")
        image_no_bg = remove_background(image, config.preprocess.segmentation_model)

        # Extract alpha channel
        if image_no_bg.mode == "RGBA":
            alpha_full = np.array(image_no_bg.split()[3])
        else:
            alpha_full = np.ones((image_no_bg.height, image_no_bg.width), dtype=np.uint8) * 255

        # Get exact crop bbox
        x_min, y_min, x_max, y_max = get_exact_bbox(alpha_full, border_safety_margin_fraction=0.0)

        # Extract cropped alpha region
        alpha_cropped = alpha_full[y_min:y_max, x_min:x_max]

        # Calculate background ratio
        ratio = calculate_background_ratio(alpha_cropped)
        object_area = int(np.count_nonzero(alpha_cropped > 0))
        total_area = int(alpha_cropped.shape[0] * alpha_cropped.shape[1])
        background_area = total_area - object_area

        crop_bbox = (int(x_min), int(y_min), int(x_max), int(y_max))
        crop_dimensions = (int(x_max - x_min), int(y_max - y_min))

        gt_analysis.append({
            "index": idx,
            "path": str(ref_path),
            "ratio": float(ratio),
            "object_area": object_area,
            "background_area": background_area,
            "total_area": total_area,
            "crop_bbox": crop_bbox,
            "crop_dimensions": crop_dimensions,
            "image_no_bg": image_no_bg,
            "alpha_full": alpha_full
        })

        print(f"  GT#{idx+1}: ratio={ratio:.3f}, object={object_area:,}px, "
              f"background={background_area:,}px ({background_area*100.0/total_area:.1f}% background) "
              f"[crop: {crop_dimensions[0]}x{crop_dimensions[1]}]")

    if not gt_analysis:
        print("Error: No valid GT images")
        return False

    # Stage 2: Select best GT (minimum ratio) if selection is enabled
    gt_selection_enabled = config.preprocess.gt_selection_enabled
    exact_crop_enabled = config.preprocess.exact_crop_enabled

    if gt_selection_enabled:
        best_gt = min(gt_analysis, key=lambda x: x["ratio"])
        selected_index = best_gt["index"]
        gts_to_process = [best_gt]
        print(f"Selected GT#{selected_index+1} (ratio={best_gt['ratio']:.3f}, best of {len(gt_analysis)} GTs)")
    else:
        gts_to_process = gt_analysis
        selected_index = -1
        print(f"Processing all {len(gt_analysis)} GTs (selection disabled)")

    # Stage 3: Process GTs
    metadata_list = []

    for gt_data in gts_to_process:
        idx = gt_data["index"]
        image_no_bg = gt_data["image_no_bg"]
        alpha_full = gt_data["alpha_full"]
        ratio = gt_data["ratio"]
        is_selected = (idx == selected_index) if gt_selection_enabled else False

        # Generate output paths
        base_idx = idx + 1  # 1-indexed
        square_filename = f"gt_{base_idx}.png"
        mask_square_filename = f"gt_{base_idx}_mask.png"
        exact_filename = f"gt_{base_idx}_exact.png"
        mask_exact_filename = f"gt_{base_idx}_exact_mask.png"

        # Resize to max dimension
        max_dim = config.preprocess.max_gt_dimension
        image_resized = resize_to_max_dimension(image_no_bg, max_dim)

        # Get alpha after resize
        if image_resized.mode == "RGBA":
            alpha_resized = np.array(image_resized.split()[3])
        else:
            alpha_resized = np.ones((image_resized.height, image_resized.width), dtype=np.uint8) * 255

        # Crop to square (for pose estimation)
        canvas_size = config.preprocess.canvas_size
        image_square = crop_to_square_or_exact(
            image_resized,
            canvas_size,
            mode="square",
            bg_color=tuple(config.preprocess.background_color),
            border_safety_margin_fraction=config.objective.objective2.border_safety_margin_fraction
        )

        # Save square version
        square_path = output_dir / square_filename
        image_square.save(square_path, "PNG")

        # Extract and save square mask
        if image_square.mode == "RGBA":
            mask_square = image_square.split()[3]
            mask_square_path = output_dir / mask_square_filename
            mask_square.save(mask_square_path, "PNG")

        # Create metadata entry
        gt_meta = {
            "gt_index": idx,
            "source_path": str(Path(gt_data["path"]).name),
            "square_path": square_filename,
            "mask_square_path": mask_square_filename,
            "background_ratio": ratio,
            "is_selected": is_selected
        }

        # Process exact crop if enabled
        if exact_crop_enabled:
            image_exact = crop_to_square_or_exact(
                image_resized,
                canvas_size,
                mode="exact",
                bg_color=tuple(config.preprocess.background_color),
                border_safety_margin_fraction=config.objective.objective2.border_safety_margin_fraction,
                edge_threshold=config.objective.objective2.edge_threshold
            )

            exact_path = output_dir / exact_filename
            image_exact.save(exact_path, "PNG")

            if image_exact.mode == "RGBA":
                mask_exact = image_exact.split()[3]
                mask_exact_path = output_dir / mask_exact_filename
                mask_exact.save(mask_exact_path, "PNG")

            gt_meta["exact_path"] = exact_filename
            gt_meta["mask_exact_path"] = mask_exact_filename

        metadata_list.append(gt_meta)
        print(f"  Processed GT#{base_idx}: {square_filename}")

    # Save metadata
    metadata = {
        "version": "1.0",
        "gt_selection_enabled": gt_selection_enabled,
        "exact_crop_enabled": exact_crop_enabled,
        "gts": metadata_list
    }

    metadata_path = output_dir / "metadata.json"
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)

    print(f"Saved metadata to {metadata_path}")
    return True


def test_preprocessing():
    """Test the preprocessing function."""
    # Load config
    workspace = Path(r"C:\Users\matti\testing")
    from vfscore.config import load_config
    config = load_config(workspace_path=workspace)

    # Test with actual reference images from the job
    ref_images = [
        Path(r"C:\Users\matti\testing\dataset\335888\images\335888_A.jpeg"),
        Path(r"C:\Users\matti\testing\dataset\335888\images\335888_B.jpg"),
        Path(r"C:\Users\matti\testing\dataset\335888\images\335888_C.jpg"),
        Path(r"C:\Users\matti\testing\dataset\335888\images\335888_D.jpg"),
    ]

    # Create temp output directory
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "test_gt"

        print("=" * 80)
        print("Testing VFScore GT Preprocessing")
        print("=" * 80)

        success = preprocess_gt_images_standalone(ref_images, output_dir, config)

        if success:
            print("\n" + "=" * 80)
            print("SUCCESS: Preprocessing completed")
            print("=" * 80)

            # Verify outputs
            metadata_path = output_dir / "metadata.json"
            if metadata_path.exists():
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
                print(f"\nGenerated files:")
                for gt in metadata["gts"]:
                    print(f"  - {gt['square_path']}")
                    print(f"  - {gt['mask_square_path']}")
                    if "exact_path" in gt:
                        print(f"  - {gt['exact_path']}")
                        print(f"  - {gt['mask_exact_path']}")

            # List all files
            print(f"\nAll files in {output_dir}:")
            for f in sorted(output_dir.iterdir()):
                print(f"  - {f.name} ({f.stat().st_size} bytes)")

            return True
        else:
            print("\n" + "=" * 80)
            print("FAILED: Preprocessing failed")
            print("=" * 80)
            return False


if __name__ == "__main__":
    test_preprocessing()

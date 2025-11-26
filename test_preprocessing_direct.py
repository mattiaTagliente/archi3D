"""Test preprocessing function directly without recompiling VFScore.

This script imports the preprocessing function from the source .py file
to test it before recompiling the Cython module.
"""

import sys
from pathlib import Path

# Add VFScore source to path
vfscore_src = Path(r"C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\VFScore_RT\src")
sys.path.insert(0, str(vfscore_src))

# Import directly from source
from vfscore.config import load_config
from vfscore.preprocess_gt import preprocess_gt_images_standalone
from vfscore.utils import make_gt_id

def test_preprocessing():
    """Test the standalone preprocessing function."""

    # Load config
    workspace = Path(r"C:\Users\matti\testing")
    config = load_config(workspace_path=workspace)

    # Test with actual reference images from job 0c7252b31588
    ref_images = [
        Path(r"C:\Users\matti\testing\dataset\335888\images\335888_A.jpeg"),
        Path(r"C:\Users\matti\testing\dataset\335888\images\335888_B.jpg"),
        Path(r"C:\Users\matti\testing\dataset\335888\images\335888_C.jpg"),
        Path(r"C:\Users\matti\testing\dataset\335888\images\335888_D.jpg"),
    ]

    # Use the same output directory structure as the evaluator would
    item_id = "0c7252b31588"
    gt_id = make_gt_id(item_id, "")
    gt_preprocess_dir = config.paths.out_dir / "preprocess" / "refs" / gt_id

    print("=" * 80)
    print("Testing VFScore GT Preprocessing (Direct Source)")
    print("=" * 80)
    print(f"Output directory: {gt_preprocess_dir}")
    print(f"Number of reference images: {len(ref_images)}")
    print()

    # Call preprocessing
    success = preprocess_gt_images_standalone(ref_images, gt_preprocess_dir, config)

    print()
    print("=" * 80)

    if success:
        print("SUCCESS: Preprocessing completed")
        print("=" * 80)

        # Verify outputs
        metadata_path = gt_preprocess_dir / "metadata.json"
        if metadata_path.exists():
            import json
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)

            print(f"\nMetadata summary:")
            print(f"  GT ID: {metadata.get('gt_id')}")
            print(f"  Num GTs: {metadata.get('num_gts')}")
            print(f"  Num processed: {metadata.get('num_processed')}")
            print(f"  Selection enabled: {metadata.get('selection_enabled')}")
            print(f"  Exact crop enabled: {metadata.get('exact_crop_enabled')}")

            print(f"\nGenerated files:")
            for gt in metadata["gts"]:
                print(f"  GT #{gt['gt_number']} (selected: {gt['is_selected']}):")
                print(f"    - {gt['square_path']}")
                print(f"    - {gt['mask_square_path']}")
                if "exact_path" in gt:
                    print(f"    - {gt['exact_path']}")
                    print(f"    - {gt['mask_exact_path']}")

            # List all files to verify
            print(f"\nAll files in {gt_preprocess_dir.name}/:")
            for f in sorted(gt_preprocess_dir.iterdir()):
                size_kb = f.stat().st_size / 1024
                print(f"  - {f.name} ({size_kb:.1f} KB)")

            return True
        else:
            print("ERROR: metadata.json not found!")
            return False
    else:
        print("FAILED: Preprocessing returned False")
        print("=" * 80)
        return False


if __name__ == "__main__":
    try:
        success = test_preprocessing()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\nEXCEPTION: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

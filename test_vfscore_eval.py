"""Test calling vfscore.evaluator.evaluate_visual_fidelity."""
import sys
from pathlib import Path

print(f"Python: {sys.executable}")
print(f"sitecustomize loaded: {'sitecustomize' in sys.modules}")

# Get the job data
cand_glb = Path("C:/Users/matti/testing/runs/2025-11-22T21-25-08Z/0c7252b31588_tripo3d_v2p5_multi/result.glb")
ref_images = [Path("C:/Users/matti/testing/dataset/335888/images/COUCOU_COUCOU_A.jpg")]
out_dir = Path("C:/Users/matti/testing/runs/2025-11-22T21-25-08Z/metrics/vfscore/0c7252b31588")

print(f"\nCandidate GLB: {cand_glb}")
print(f"Exists: {cand_glb.exists()}")
print(f"Reference images: {ref_images}")
print(f"All exist: {all(p.exists() for p in ref_images)}")
print(f"Output dir: {out_dir}")

print("\nImporting vfscore.evaluator...")
try:
    from vfscore.evaluator import evaluate_visual_fidelity
    print("SUCCESS!")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\nCalling evaluate_visual_fidelity...")
out_dir.mkdir(parents=True, exist_ok=True)
try:
    result = evaluate_visual_fidelity(
        cand_glb=str(cand_glb),
        ref_images=[str(p) for p in ref_images],
        out_dir=str(out_dir),
        repeats=1,
        timeout_s=None,
    )
    print(f"SUCCESS! Result keys: {list(result.keys())}")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

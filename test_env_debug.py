"""Debug environment when importing vfscore from archi3d context."""
import sys
import os

print(f"Python: {sys.executable}")
print(f"sys.prefix: {sys.prefix}")
print(f"sitecustomize loaded: {'sitecustomize' in sys.modules}")
print(f"\nPATH:")
for i, p in enumerate(os.environ.get("PATH", "").split(os.pathsep)[:10]):
    print(f"  {i}: {p}")

print("\n_DllDirectory (if set):")
torch_lib = r"C:\Users\matti\venvs\archi3D\Lib\site-packages\torch\lib"
print(f"  Torch lib in PATH: {torch_lib in os.environ.get('PATH', '')}")

print("\nAttempting vfscore import...")
try:
    # This is what archi3d does
    from archi3d.metrics.vfscore_adapter import _try_import_api, VFScoreRequest
    from pathlib import Path

    print("SUCCESS: Imported vfscore_adapter")

    # Try calling the import function
    req = VFScoreRequest(
        cand_glb=Path("dummy.glb"),
        ref_images=[Path("dummy.jpg")],
        out_dir=Path("dummy_out"),
        repeats=1,
    )

    print("\nCalling _try_import_api (this will import vfscore.evaluator)...")
    response = _try_import_api(req)

    if response is None:
        print("Import failed (returned None)")
    elif response.ok:
        print("SUCCESS!")
    else:
        print(f"ERROR: {response.error[:500]}")

except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

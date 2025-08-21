from __future__ import annotations
from pathlib import Path
import yaml

def load_adapters_cfg(repo_root: Path) -> dict:
    # Adjust the path to account for the 'src' directory layout
    p = repo_root / "src" / "archi3d" / "config" / "adapters.yaml"
    if not p.exists():
        # Fallback for non-src layout, just in case
        p = repo_root / "archi3d" / "config" / "adapters.yaml"

    with p.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}

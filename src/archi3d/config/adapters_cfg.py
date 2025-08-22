from __future__ import annotations
from pathlib import Path
import yaml
import sys # <-- Add this import

def load_adapters_cfg() -> dict:
    """
    Loads the adapters configuration, handling both development 
    and bundled (PyInstaller) environments.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # --- Running in a PyInstaller Bundle ---
        # The base path is the temporary folder created by the executable
        base_path = Path(sys._MEIPASS) # type: ignore
        # The path inside the bundle is determined by your --add-data flag.
        # Your command uses: --add-data ".\src\archi3d\config\adapters.yaml;archi3d\config"
        # This means the file is located at: <base_path>/archi3d/config/adapters.yaml
        p = base_path / "archi3d" / "config" / "adapters.yaml"
    else:
        # --- Running in a normal development environment ---
        # Find the repo root by searching for pyproject.toml
        # Note: This requires importing _find_repo_root, but we do it locally
        # to avoid circular dependency issues at the top level.
        from archi3d.config.loader import _find_repo_root
        repo_root = _find_repo_root()
        p = repo_root / "src" / "archi3d" / "config" / "adapters.yaml"

    with p.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}

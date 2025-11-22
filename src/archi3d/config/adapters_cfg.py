from __future__ import annotations
from pathlib import Path
import yaml
import sys


def load_adapters_cfg() -> dict:
    """
    Loads the adapters configuration, handling both development
    and bundled (PyInstaller) environments.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # --- Running in a PyInstaller Bundle ---
        # The base path is the temporary folder created by the executable
        base_path = Path(sys._MEIPASS)  # type: ignore
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


def get_adapter_image_mode(adapter_key: str) -> str:
    """
    Get the image_mode for an adapter ("single" or "multi").

    Falls back to inferring from adapter name suffix if not specified.

    Args:
        adapter_key: The adapter key (e.g., "trellis_single", "tripo3d_v2p5_multi")

    Returns:
        "single" or "multi"
    """
    cfg = load_adapters_cfg()
    adapters = cfg.get("adapters", {})
    adapter_cfg = adapters.get(adapter_key, {})

    # Check explicit image_mode field
    image_mode = adapter_cfg.get("image_mode", "")
    if image_mode in ("single", "multi"):
        return image_mode

    # Fallback: infer from naming convention
    if adapter_key.endswith("_single"):
        return "single"
    elif adapter_key.endswith("_multi"):
        return "multi"

    # Default to single if cannot determine
    return "single"


def get_algos_by_image_mode(
    allowed_algos: list[str],
    mode: str | None = None,
) -> dict[str, list[str]]:
    """
    Partition algorithms by their image_mode.

    Args:
        allowed_algos: List of algorithm keys to partition.
        mode: Optional filter - if "single" or "multi", return only those.

    Returns:
        Dict with keys "single" and "multi", each containing list of algo keys.
        If mode is specified, returns only that subset.
    """
    result: dict[str, list[str]] = {"single": [], "multi": []}

    for algo in allowed_algos:
        algo_mode = get_adapter_image_mode(algo)
        if algo_mode in result:
            result[algo_mode].append(algo)

    if mode in ("single", "multi"):
        return {mode: result[mode]}

    return result

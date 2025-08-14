# archi3d/config/loader.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import yaml

from .schema import GlobalConfig, UserConfig, EffectiveConfig


REPO_GLOBAL_FILENAME = "global.yaml"
ENV_WORKSPACE = "ARCHI3D_WORKSPACE"


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"YAML not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def _find_repo_root(start: Optional[Path] = None, max_depth: int = 6) -> Path:
    """
    Find the repository root by walking up from 'start' (cwd if None) until we find
    either a pyproject.toml or a global.yaml sentinel. Stops after max_depth levels.
    """
    cur = (start or Path.cwd()).resolve()
    for _ in range(max_depth + 1):
        if (cur / "pyproject.toml").exists() or (cur / REPO_GLOBAL_FILENAME).exists():
            return cur
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise FileNotFoundError(
        "Could not locate repository root (no pyproject.toml/global.yaml found within search depth). "
        "Run from inside the repo, or set PYTHONPATH appropriately."
    )


def _load_global_config(repo_root: Path) -> GlobalConfig:
    global_path = repo_root / REPO_GLOBAL_FILENAME
    g = _read_yaml(global_path)
    try:
        return GlobalConfig.model_validate(g)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Invalid global config at {global_path}: {e}") from e


def _load_user_config() -> Optional[UserConfig]:
    """
    Load per-user config from ~/.archi3d/config.yaml if present.
    """
    user_cfg_path = Path.home() / ".archi3d" / "config.yaml"
    if not user_cfg_path.exists():
        return None
    data = _read_yaml(user_cfg_path)
    try:
        return UserConfig.model_validate(data)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Invalid user config at {user_cfg_path}: {e}") from e


def _apply_env_overrides(user_cfg: Optional[UserConfig]) -> UserConfig:
    """
    ENV has highest precedence for workspace.
    If ARCHI3D_WORKSPACE is set, use it; otherwise return user_cfg as-is.
    """
    ws = os.environ.get(ENV_WORKSPACE, "").strip()
    if not ws:
        if user_cfg is None:
            raise RuntimeError(
                "Workspace is not configured. Please set either:\n"
                f" - ENV {ENV_WORKSPACE}=<absolute path to Testing>, or\n"
                " - ~/.archi3d/config.yaml with:\n"
                '     workspace: "C:\\\\path\\\\to\\\\Testing"\n'
            )
        return user_cfg

    if not Path(ws).is_absolute():
        raise ValueError(f"{ENV_WORKSPACE} must be an absolute path, got: {ws}")

    # Override or create a UserConfig with env workspace
    return UserConfig(workspace=ws)


def load_config(start: Optional[Path] = None) -> EffectiveConfig:
    """
    Public entry point used by the CLI.
    Merge order:
      repo global.yaml  ->  user (~/.archi3d/config.yaml)  ->  ENV ARCHI3D_WORKSPACE
    Returns an EffectiveConfig with validated models.
    """
    repo_root = _find_repo_root(start)
    global_cfg = _load_global_config(repo_root)
    user_cfg_file = _load_user_config()
    user_cfg_final = _apply_env_overrides(user_cfg_file)

    # Final assembled config object
    eff = EffectiveConfig(global_config=global_cfg, user_config=user_cfg_final)

    # Final checks (do not create dirs here; just validate presence)
    ws = Path(eff.user_config.workspace)
    if not ws.exists():
        # We do not auto-create the workspace; the path must already exist and contain the expected tree.
        raise FileNotFoundError(
            f"Configured workspace does not exist: {ws}\n"
            "Please create it (with dataset/runs/tables/reports) or point to the correct location."
        )
    return eff

# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

# src/archi3d/config/loader.py
"""
Configuration loading with 3-layer merge and .env support.

Configuration Precedence (highest to lowest):
1. Environment variable ARCHI3D_WORKSPACE
2. .env file in repository root (auto-loaded)
3. User config at platform-specific location:
   - Windows: %LOCALAPPDATA%/archi3d/archi3d/config.yaml
   - Linux: ~/.config/archi3d/config.yaml
   - macOS: ~/Library/Application Support/archi3d/config.yaml

The .env file is loaded early, populating os.environ before other checks.
This allows `ARCHI3D_WORKSPACE=...` in .env to work seamlessly.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from platformdirs import user_config_path

from archi3d.config.schema import EffectiveConfig, GlobalConfig, UserConfig

REPO_GLOBAL_FILENAME = "global.yaml"
ENV_WORKSPACE = "ARCHI3D_WORKSPACE"
DOTENV_FILENAME = ".env"


def _read_yaml(path: Path) -> dict[str, Any]:
    """
    Reads and parses a YAML file.

    Args:
        path: The path to the YAML file.

    Returns:
        A dictionary containing the parsed YAML data.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If the root of the YAML file is not a mapping.
    """
    if not path.exists():
        raise FileNotFoundError(f"YAML not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def _load_dotenv(repo_root: Path) -> bool:
    """
    Load .env file from the repository root if it exists.

    Args:
        repo_root: The path to the repository root.

    Returns:
        True if a .env file was found and loaded, False otherwise.
    """
    dotenv_path = repo_root / DOTENV_FILENAME
    if dotenv_path.exists():
        # override=False means existing env vars take precedence over .env
        # This allows system-level ARCHI3D_WORKSPACE to override .env
        load_dotenv(dotenv_path, override=False)
        return True
    return False


def _find_repo_root(start: Path | None = None, max_depth: int = 6) -> Path:
    """
    Find the repository root by walking up from 'start' (cwd if None) until we find
    either a pyproject.toml or a global.yaml sentinel. Stops after max_depth levels.

    Args:
        start: The starting directory for the search. Defaults to the current working directory.
        max_depth: The maximum number of parent directories to search.

    Returns:
        The path to the repository root.

    Raises:
        FileNotFoundError: If the repository root cannot be located.
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
        "Could not locate repository root "
        "(no pyproject.toml/global.yaml found within search depth). "
        "Run from inside the repo, or set PYTHONPATH appropriately."
    )


def _load_global_config(repo_root: Path) -> GlobalConfig:
    """
    Loads the global configuration from the repository root.

    Args:
        repo_root: The path to the repository root.

    Returns:
        A GlobalConfig object.

    Raises:
        ValueError: If the global config file is invalid.
    """
    global_path = repo_root / REPO_GLOBAL_FILENAME
    g = _read_yaml(global_path)
    try:
        return GlobalConfig.model_validate(g)
    except Exception as e:
        raise ValueError(f"Invalid global config at {global_path}: {e}") from e


def _load_user_config() -> UserConfig | None:
    """
    Load per-user config from the OS-specific user config directory.
    e.g., ~/.config/archi3d/config.yaml on Linux
    e.g., C:/Users/<user>/AppData/Roaming/archi3d/config.yaml on Windows
    """
    # Use platformdirs to get the standard user config path
    config_dir = user_config_path(appname="archi3d", ensure_exists=True)
    user_cfg_path = config_dir / "config.yaml"

    if not user_cfg_path.exists():
        return None
    data = _read_yaml(user_cfg_path)
    try:
        return UserConfig.model_validate(data)
    except Exception as e:
        raise ValueError(f"Invalid user config at {user_cfg_path}: {e}") from e


def _apply_env_overrides(user_cfg: UserConfig | None) -> UserConfig:
    """
    ENV has highest precedence for workspace.
    If ARCHI3D_WORKSPACE is set, use it; otherwise return user_cfg as-is.

    Args:
        user_cfg: The user configuration loaded from a file.

    Returns:
        The final UserConfig, with environment variable overrides applied.

    Raises:
        RuntimeError: If the workspace is not configured via ENV or file.
        ValueError: If the workspace path from ENV is not absolute.
    """
    ws = os.environ.get(ENV_WORKSPACE, "").strip()
    if not ws:
        if user_cfg is None:
            # Generate the expected path for the error message
            expected_config_path = user_config_path(appname="archi3d") / "config.yaml"
            raise RuntimeError(
                "Workspace is not configured. Please set one of:\n"
                f" 1. Create a .env file in the repository root with:\n"
                f'    {ENV_WORKSPACE}="C:/path/to/workspace"\n'
                f" 2. Set environment variable {ENV_WORKSPACE}\n"
                f" 3. Create a config file at '{expected_config_path}' with:\n"
                '    workspace: "C:/path/to/workspace"\n'
            )
        return user_cfg

    if not Path(ws).is_absolute():
        raise ValueError(f"{ENV_WORKSPACE} must be an absolute path, got: {ws}")

    # Override or create a UserConfig with env workspace
    return UserConfig(workspace=ws)


def load_config(start: Path | None = None) -> EffectiveConfig:
    """
    Public entry point used by the CLI.

    Configuration precedence (highest to lowest):
      1. Environment variable ARCHI3D_WORKSPACE (system-level)
      2. .env file in repo root (loaded into os.environ)
      3. User config file (platform-specific location)

    Tool paths precedence (highest to lowest):
      1. User config tools overrides
      2. Global config tools defaults

    The .env file is loaded early, so ARCHI3D_WORKSPACE in .env works seamlessly.
    System-level env vars always take precedence over .env values.

    Args:
        start: The starting directory for finding the repo root.

    Returns:
        An EffectiveConfig object with the final configuration.

    Raises:
        FileNotFoundError: If the configured workspace directory does not exist.
    """
    repo_root = _find_repo_root(start)

    # Load .env BEFORE checking env vars, so .env values become available
    # Note: override=False in _load_dotenv means system env vars win over .env
    _load_dotenv(repo_root)

    global_cfg = _load_global_config(repo_root)
    user_cfg_file = _load_user_config()
    user_cfg_final = _apply_env_overrides(user_cfg_file)

    # Final assembled config object
    eff = EffectiveConfig(global_config=global_cfg, user_config=user_cfg_final)

    # Final checks (do not create dirs here; just validate presence)
    # The logic in _apply_env_overrides should prevent user_config from being None,
    # but we assert here to satisfy the type checker and ensure correctness.
    assert eff.user_config is not None, "User config should not be None at this point."
    ws_path = Path(eff.user_config.workspace)
    if not ws_path.exists():
        raise FileNotFoundError(
            f"Configured workspace does not exist: {ws_path}\n"
            "Please create it or point to the correct location."
        )
    return eff


def get_tool_path(config: EffectiveConfig, tool_name: str) -> Path:
    """
    Get effective tool path with user config overrides.

    Tool paths are resolved with the following precedence:
      1. User config tools override (if set)
      2. Global config tools default

    Args:
        config: Loaded configuration
        tool_name: Name of the tool (e.g., "blender_exe")

    Returns:
        Path to the tool

    Raises:
        AttributeError: If tool_name doesn't exist in ToolPaths schema
    """
    # Start with global default
    global_tools = config.global_config.tools
    tool_path = getattr(global_tools, tool_name)

    # Override with user config if present
    if config.user_config and config.user_config.tools:
        user_override = getattr(config.user_config.tools, tool_name, None)
        if user_override is not None:
            tool_path = user_override

    return Path(tool_path)
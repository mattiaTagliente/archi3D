# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

"""
Adapter discovery layer for metric tools.

Implements resolution order:
1. Python import (preferred) - try importing package
2. Entry points - third-party plugins via archi3d.metrics_adapters
3. CLI invocation (fallback) - subprocess execution

Configuration sources (precedence):
1. Environment variables:
   - ARCHI3D_FSCORE_IMPL={import|cli}
   - ARCHI3D_VFSCORE_IMPL={import|cli}
   - ARCHI3D_FSCORE_CLI=/path/to/command
   - ARCHI3D_VFSCORE_CLI=/path/to/command
2. global.yaml keys (future extensibility)

Raises friendly, actionable errors when adapters are unavailable.
"""

import importlib.util
import logging
import os
from collections.abc import Callable
from typing import Literal

from archi3d.metrics.fscore_adapter import (
    FScoreRequest,
    FScoreResponse,
)
from archi3d.metrics.fscore_adapter import (
    _try_cli_invocation as _fscore_cli,
)
from archi3d.metrics.fscore_adapter import (
    _try_import_api as _fscore_import,
)
from archi3d.metrics.vfscore_adapter import (
    VFScoreRequest,
    VFScoreResponse,
)
from archi3d.metrics.vfscore_adapter import (
    _try_cli_invocation as _vfscore_cli,
)
from archi3d.metrics.vfscore_adapter import (
    _try_import_api as _vfscore_import,
)
from archi3d.plugins.metrics import load_entry_point_adapter

logger = logging.getLogger(__name__)


class AdapterNotFoundError(Exception):
    """Raised when no suitable adapter implementation is available."""

    pass


def _discover_fscore_adapter(
    force_mode: Literal["import", "cli", "auto"] | None = None,
) -> tuple[Callable[[FScoreRequest], FScoreResponse | None], str]:
    """
    Discover FScore adapter implementation.

    Args:
        force_mode: Override resolution mode (from env ARCHI3D_FSCORE_IMPL)

    Returns:
        Tuple of (adapter_function, mode_name)
        - adapter_function: Callable taking FScoreRequest, returning FScoreResponse or None
        - mode_name: "import" | "cli" | "entry_point"

    Raises:
        AdapterNotFoundError: No implementation available

    Environment variables:
        ARCHI3D_FSCORE_IMPL: "import" | "cli" | "auto" (default: "auto")
        ARCHI3D_FSCORE_CLI: Path to CLI command (e.g., "python -m fscore")
    """
    # Determine resolution mode
    mode = force_mode or os.getenv("ARCHI3D_FSCORE_IMPL", "auto")

    # Try import path (if not forced to CLI)
    if mode in ("import", "auto"):
        logger.debug("Attempting FScore import API")
        # Test if import works without executing
        if importlib.util.find_spec("fscore") is not None:
            logger.info("FScore adapter resolved via import")
            return (_fscore_import, "import")
        else:
            logger.debug("FScore module not installed, trying entry points")

    # Try entry points (if import failed)
    if mode in ("import", "auto"):
        logger.debug("Attempting FScore entry point discovery")
        adapter_class = load_entry_point_adapter("archi3d.metrics_adapters", "fscore")
        if adapter_class is not None:
            logger.info("FScore adapter resolved via entry point")
            # Wrap entry point adapter to match signature
            def _entry_point_wrapper(req: FScoreRequest) -> FScoreResponse | None:
                try:
                    return adapter_class().evaluate(req)
                except Exception as e:
                    return FScoreResponse(ok=False, error=str(e))

            return (_entry_point_wrapper, "entry_point")

    # Try CLI fallback (if not forced to import)
    if mode in ("cli", "auto"):
        cli_cmd = os.getenv("ARCHI3D_FSCORE_CLI")
        if cli_cmd:
            logger.info(f"FScore adapter resolved via CLI: {cli_cmd}")
            return (_fscore_cli, "cli")
        else:
            logger.debug("No ARCHI3D_FSCORE_CLI environment variable set")

    # No adapter found - raise friendly error
    raise AdapterNotFoundError(
        "FScore adapter not found. To enable FScore metrics:\n\n"
        "  Option 1 (Recommended): Install FScore as a Python package:\n"
        "    pip install archi3d[fscore]\n"
        "    # or for editable install:\n"
        '    pip install -e "path/to/FScore"\n\n'
        "  Option 2: Use FScore as external CLI:\n"
        "    export ARCHI3D_FSCORE_CLI='python -m fscore'\n"
        "    # or set Windows environment variable:\n"
        "    set ARCHI3D_FSCORE_CLI=python -m fscore\n\n"
        "  Option 3: Install third-party plugin with entry point:\n"
        "    pip install your-fscore-plugin\n"
        "    # (plugin must expose 'archi3d.metrics_adapters' entry point)"
    )


def _discover_vfscore_adapter(
    force_mode: Literal["import", "cli", "auto"] | None = None,
) -> tuple[Callable[[VFScoreRequest], VFScoreResponse | None], str]:
    """
    Discover VFScore adapter implementation.

    Args:
        force_mode: Override resolution mode (from env ARCHI3D_VFSCORE_IMPL)

    Returns:
        Tuple of (adapter_function, mode_name)
        - adapter_function: Callable taking VFScoreRequest, returning VFScoreResponse or None
        - mode_name: "import" | "cli" | "entry_point"

    Raises:
        AdapterNotFoundError: No implementation available

    Environment variables:
        ARCHI3D_VFSCORE_IMPL: "import" | "cli" | "auto" (default: "auto")
        ARCHI3D_VFSCORE_CLI: Path to CLI command (e.g., "python -m vfscore")
    """
    # Determine resolution mode
    mode = force_mode or os.getenv("ARCHI3D_VFSCORE_IMPL", "auto")

    # Try import path (if not forced to CLI)
    if mode in ("import", "auto"):
        logger.debug("Attempting VFScore import API")
        # Test if import works without executing
        if importlib.util.find_spec("vfscore") is not None:
            logger.info("VFScore adapter resolved via import")
            return (_vfscore_import, "import")
        else:
            logger.debug("VFScore module not installed, trying entry points")

    # Try entry points (if import failed)
    if mode in ("import", "auto"):
        logger.debug("Attempting VFScore entry point discovery")
        adapter_class = load_entry_point_adapter("archi3d.metrics_adapters", "vfscore")
        if adapter_class is not None:
            logger.info("VFScore adapter resolved via entry point")
            # Wrap entry point adapter to match signature
            def _entry_point_wrapper(req: VFScoreRequest) -> VFScoreResponse | None:
                try:
                    return adapter_class().evaluate(req)
                except Exception as e:
                    return VFScoreResponse(ok=False, error=str(e))

            return (_entry_point_wrapper, "entry_point")

    # Try CLI fallback (if not forced to import)
    if mode in ("cli", "auto"):
        cli_cmd = os.getenv("ARCHI3D_VFSCORE_CLI")
        if cli_cmd:
            logger.info(f"VFScore adapter resolved via CLI: {cli_cmd}")
            return (_vfscore_cli, "cli")
        else:
            logger.debug("No ARCHI3D_VFSCORE_CLI environment variable set")

    # No adapter found - raise friendly error
    raise AdapterNotFoundError(
        "VFScore adapter not found. To enable VFScore metrics:\n\n"
        "  Option 1 (Recommended): Install VFScore as a Python package:\n"
        "    pip install archi3d[vfscore]\n"
        "    # or for editable install:\n"
        '    pip install -e "path/to/VFScore"\n\n'
        "  Option 2: Use VFScore as external CLI:\n"
        "    export ARCHI3D_VFSCORE_CLI='python -m vfscore'\n"
        "    # or set Windows environment variable:\n"
        "    set ARCHI3D_VFSCORE_CLI=python -m vfscore\n\n"
        "  Option 3: Install third-party plugin with entry point:\n"
        "    pip install your-vfscore-plugin\n"
        "    # (plugin must expose 'archi3d.metrics_adapters' entry point)"
    )


def get_fscore_adapter() -> Callable[[FScoreRequest], FScoreResponse | None]:
    """
    Get FScore adapter function.

    Returns:
        Adapter function taking FScoreRequest and returning FScoreResponse or None

    Raises:
        AdapterNotFoundError: No implementation available with actionable message
    """
    adapter_fn, mode = _discover_fscore_adapter()
    logger.info(f"Using FScore adapter mode: {mode}")
    return adapter_fn


def get_vfscore_adapter() -> Callable[[VFScoreRequest], VFScoreResponse | None]:
    """
    Get VFScore adapter function.

    Returns:
        Adapter function taking VFScoreRequest and returning VFScoreResponse or None

    Raises:
        AdapterNotFoundError: No implementation available with actionable message
    """
    adapter_fn, mode = _discover_vfscore_adapter()
    logger.info(f"Using VFScore adapter mode: {mode}")
    return adapter_fn

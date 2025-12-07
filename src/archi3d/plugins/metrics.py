# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

"""
Metric adapter protocols and entry-point discovery.

Defines Protocol interfaces for FScore and VFScore adapters, enabling
third-party plugins via entry points in the `archi3d.metrics_adapters`
namespace.
"""

import logging
from typing import Protocol, runtime_checkable

from archi3d.metrics.fscore_adapter import FScoreRequest, FScoreResponse
from archi3d.metrics.vfscore_adapter import VFScoreRequest, VFScoreResponse

logger = logging.getLogger(__name__)


@runtime_checkable
class FScoreAdapter(Protocol):
    """
    Protocol for FScore geometry metric adapters.

    Adapters must implement an `evaluate` method that takes an FScoreRequest
    and returns an FScoreResponse with canonical payload schema.
    """

    def evaluate(self, req: FScoreRequest) -> FScoreResponse:
        """
        Evaluate geometry metrics for a 3D model against ground truth.

        Args:
            req: FScore evaluation request with paths and parameters

        Returns:
            FScoreResponse with ok=True and canonical payload on success,
            ok=False with error message on failure
        """
        ...


@runtime_checkable
class VFScoreAdapter(Protocol):
    """
    Protocol for VFScore visual fidelity metric adapters.

    Adapters must implement an `evaluate` method that takes a VFScoreRequest
    and returns a VFScoreResponse with canonical payload schema.
    """

    def evaluate(self, req: VFScoreRequest) -> VFScoreResponse:
        """
        Evaluate visual fidelity metrics for a 3D model against reference images.

        Args:
            req: VFScore evaluation request with paths and parameters

        Returns:
            VFScoreResponse with ok=True and canonical payload on success,
            ok=False with error message on failure
        """
        ...


def load_entry_point_adapter(namespace: str, name: str):
    """
    Load adapter from entry points.

    Args:
        namespace: Entry point group (e.g., "archi3d.metrics_adapters")
        name: Entry point name (e.g., "fscore", "vfscore")

    Returns:
        Adapter class or None if not found

    Example:
        In third-party pyproject.toml:
        [project.entry-points."archi3d.metrics_adapters"]
        fscore = "my_package:MyFScoreAdapter"
        vfscore = "my_package:MyVFScoreAdapter"
    """
    try:
        # Python 3.10+ importlib.metadata
        from importlib.metadata import entry_points  # noqa: PLC0415
    except ImportError:
        # Python 3.9 backport
        try:
            from importlib_metadata import entry_points  # noqa: PLC0415
        except ImportError:
            logger.debug(
                "importlib.metadata not available; entry-point discovery disabled"
            )
            return None

    try:
        # Python 3.10+ API
        eps = entry_points(group=namespace)
    except TypeError:
        # Python 3.9 API (returns dict)
        eps_dict = entry_points()
        eps = eps_dict.get(namespace, [])

    for ep in eps:
        if ep.name == name:
            logger.info(f"Loading adapter from entry point: {ep.value}")
            return ep.load()

    return None

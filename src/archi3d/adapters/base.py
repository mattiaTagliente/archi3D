from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AdapterTransientError(RuntimeError): ...


class AdapterPermanentError(RuntimeError): ...


# -------------------------
# Legacy Types (Phase 0-2)
# -------------------------


@dataclass(frozen=True)
class Token:
    run_id: str
    algo: str
    product_id: str
    variant: str
    image_files: list[str]  # relpaths under workspace (prefixed with 'dataset/…')
    img_suffixes: str
    job_id: str


@dataclass
class ExecResult:
    glb_path: str | Path
    timings: dict[str, Any]  # raw timings if provider returns them
    request_id: str | None


# -------------------------
# Phase 3 Types
# -------------------------


@dataclass
class GenerationRequest:
    """
    Request for a 3D generation job.
    Phase 3 adapter contract input type.
    """

    job_id: str
    product_id: str
    variant: str
    algo: str
    used_images: list[Path]  # workspace-absolute resolved paths
    out_dir: Path  # runs/<run_id>/outputs/<job_id>
    workspace: Path  # workspace root
    extra: dict  # future extension


@dataclass
class GenerationResult:
    """
    Result from a 3D generation job.
    Phase 3 adapter contract output type.
    """

    success: bool
    generated_glb: Path | None  # absolute path to generated GLB file
    previews: list[Path]  # 0..3 preview images (absolute paths)
    algo_version: str | None
    unit_price_usd: float | None
    price_source: str | None
    raw_metadata: dict | None  # optional dump-through


class ModelAdapter:
    def __init__(self, cfg: dict[str, Any], workspace: Path, logs_dir: Path):
        self.cfg = cfg
        self.workspace = workspace
        self.logs_dir = logs_dir
        # Create a logger specific to the adapter instance
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.INFO)

    def unit_price_usd(self) -> float:
        return float(self.cfg.get("unit_price_usd", 0.0))

    def price_source(self) -> str:
        return str(self.cfg.get("price_source", "unknown"))

    def execute(self, token: Token, deadline_s: int = 480) -> ExecResult:
        """Full execution hook: upload/prepare → invoke → materialize."""
        raise NotImplementedError

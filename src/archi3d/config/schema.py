# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

# archi3d/config/schema.py
from __future__ import annotations

from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class Thresholds(BaseModel):
    """Acceptance thresholds configured at repo level (global.yaml)."""
    model_config = ConfigDict(extra="forbid")

    lpips_max: float = Field(..., gt=0, description="Max acceptable LPIPS (lower is better)")
    fscore_min: float = Field(..., gt=0, lt=1, description="Min acceptable F-score in [0,1]")


class BatchConfig(BaseModel):
    """Configuration for batch creation."""
    model_config = ConfigDict(extra="forbid")

    # CORRECTED: Simplified the default value assignment to be more explicit for Pylance.
    # By removing Field(), we avoid the function call syntax that was causing the issue.
    single_image_policy: str = "exact_one"


class ToolPaths(BaseModel):
    """External tool paths."""
    model_config = ConfigDict(extra="forbid")

    blender_exe: Path = Field(
        default=Path("C:/Program Files/Blender Foundation/Blender 4.5/blender.exe"),
        description="Path to Blender executable (used by VFScore for HQ rendering)"
    )


class FScoreDefaults(BaseModel):
    """Default parameters for FScore computation."""
    model_config = ConfigDict(extra="forbid")

    default_n_points: int = 100000
    default_timeout_s: int = 300


class VFScoreDefaults(BaseModel):
    """Default parameters for VFScore computation."""
    model_config = ConfigDict(extra="forbid")

    default_repeats: int = 1
    default_timeout_s: int = 600


class MetricsConfig(BaseModel):
    """Metrics computation defaults."""
    model_config = ConfigDict(extra="forbid")

    fscore: FScoreDefaults = Field(default_factory=FScoreDefaults)
    vfscore: VFScoreDefaults = Field(default_factory=VFScoreDefaults)


class GlobalConfig(BaseModel):
    """Global (repo) configuration."""
    model_config = ConfigDict(extra="forbid")

    algorithms: List[str] = Field(
        ...,
        description="Canonical list of algorithm keys enabled for the project."
    )
    thresholds: Thresholds = Field(
        ...,
        description="Quality thresholds used in reporting and gating."
    )
    # This now correctly references BatchConfig, which has a clear default.
    batch: BatchConfig = Field(default_factory=BatchConfig)
    tools: ToolPaths = Field(
        default_factory=ToolPaths,
        description="External tool paths (Blender, etc.)"
    )
    metrics: MetricsConfig = Field(
        default_factory=MetricsConfig,
        description="Default parameters for metrics computation"
    )


class UserConfig(BaseModel):
    """Per-user overrides (e.g., ~/.archi3d/config.yaml)."""
    model_config = ConfigDict(extra="forbid")

    workspace: str = Field(
        ...,
        description="Absolute path to the shared Testing workspace on this machine."
    )
    tools: Optional[ToolPaths] = Field(
        default=None,
        description="Optional tool path overrides for non-standard installations"
    )


class EffectiveConfig(BaseModel):
    """
    Fully-resolved configuration after merging:
    defaults -> repo global.yaml -> user (~/.archi3d/config.yaml) -> environment.
    """
    model_config = ConfigDict(extra="forbid")

    global_config: GlobalConfig
    user_config: Optional[UserConfig] = None
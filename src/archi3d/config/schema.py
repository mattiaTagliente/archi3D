# archi3d/config/schema.py
from __future__ import annotations

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


class UserConfig(BaseModel):
    """Per-user overrides (e.g., ~/.archi3d/config.yaml)."""
    model_config = ConfigDict(extra="forbid")

    workspace: str = Field(
        ...,
        description="Absolute path to the shared Testing workspace on this machine."
    )


class EffectiveConfig(BaseModel):
    """
    Fully-resolved configuration after merging:
    defaults -> repo global.yaml -> user (~/.archi3d/config.yaml) -> environment.
    """
    model_config = ConfigDict(extra="forbid")

    global_config: GlobalConfig
    user_config: Optional[UserConfig] = None
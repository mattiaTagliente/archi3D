# Configuration Architecture Redesign

## Current State Analysis

### Files
1. **`.env`** (repo root, gitignored)
   - `ARCHI3D_WORKSPACE`: Workspace path
   - `FAL_KEY`: API key for fal.ai services
   - `ARCHI3D_WORKER_ID`: Optional worker identity override

2. **`global.yaml`** (repo root, checked into git)
   - `algorithms`: List of enabled algorithms
   - `thresholds`: Quality thresholds (lpips_max, fscore_min)
   - `batch`: Batch creation settings (single_image_policy)

3. **User config** (`~/.config/archi3d/config.yaml` or platform equivalent)
   - `workspace`: Alternative workspace path location

### Issues Identified
1. **Blender path hardcoded** in VFScore (`C:/Program Files/Blender Foundation/Blender 4.5/blender.exe`)
2. **No centralized tool paths** configuration
3. **Unclear separation** between secrets (.env) and configuration (global.yaml)
4. **Missing validation** for tool paths (Blender not checked on startup)
5. **Thresholds in global.yaml** may not be actively used (need verification)

## Proposed Architecture

### Principle: Secrets vs Configuration

**.env file** (gitignored, per-machine secrets):
- API keys and credentials
- Machine-specific overrides (worker ID)
- NO paths or configuration parameters

**global.yaml** (checked into git, shared project configuration):
- Project settings (algorithms, policies)
- Tool paths with sane defaults (Blender, etc.)
- Quality thresholds (if actively used)
- Business logic configuration

### New Structure

#### .env (Secrets Only)
```bash
# =============================================================================
# archi3D Environment Variables (Secrets & Overrides)
# =============================================================================
# This file is gitignored and should never be committed.
# Copy from .env.template and fill in your values.

# --- Workspace Configuration ---
# REQUIRED: Absolute path to your workspace directory
# Use forward slashes (/) even on Windows for compatibility
ARCHI3D_WORKSPACE="C:/Users/matti/testing"

# --- API Credentials ---
# REQUIRED for fal.ai adapters (Trellis, TripoSR, Rodin, Hunyuan3D, Tripo3D)
# Get your key from: https://fal.ai/dashboard/keys
FAL_KEY="your-fal-key-here"

# --- Optional Overrides ---
# Worker identity (defaults to OS username if not set)
# ARCHI3D_WORKER_ID=matti

# Git commit hash (auto-populated by CI, leave empty for local dev)
# ARCHI3D_COMMIT=
```

#### global.yaml (Project Configuration)
```yaml
# =============================================================================
# archi3D Global Configuration (Project Settings)
# =============================================================================
# This file is checked into git and shared across all developers.
# Machine-specific settings should go in .env or user config.

# --- Enabled Algorithms ---
algorithms:
  - trellis_multi_stochastic
  - trellis_multi_multidiffusion
  - tripo3d_v2p5_multi
  - rodin_multi
  - hunyuan3d_v2_multi
  - trellis_single
  - tripoSR_single
  - tripo3d_v2p5_single
  - hunyuan3d_v2_single
  - hunyuan3d_v2p1_single

# --- Quality Thresholds ---
# (Note: Currently defined but may not be actively enforced.
#  Kept for future use in gating/reporting.)
thresholds:
  lpips_max: 0.15  # Max acceptable LPIPS (lower is better)
  fscore_min: 0.65  # Min acceptable F-score in [0,1]

# --- Batch Creation Settings ---
batch:
  single_image_policy: exact_one  # exact_one | allow_any

# --- External Tool Paths ---
# Default paths work for standard installations.
# Override in user config (~/.config/archi3d/config.yaml) if needed.
tools:
  blender_exe: "C:/Program Files/Blender Foundation/Blender 4.5/blender.exe"  # Windows default
  # blender_exe: "/usr/bin/blender"  # Linux alternative
  # blender_exe: "/Applications/Blender.app/Contents/MacOS/Blender"  # macOS alternative

# --- Metrics Configuration ---
metrics:
  fscore:
    default_n_points: 100000  # Poisson disk samples per mesh
    default_timeout_s: 300    # Per-job timeout (5 minutes)

  vfscore:
    default_repeats: 1        # LLM scoring repeats (objective2 uses 1)
    default_timeout_s: 600    # Per-job timeout (10 minutes)
```

#### User Config (~/.config/archi3d/config.yaml)
```yaml
# User-specific overrides (optional)
# Lowest priority, overridden by .env

# Alternative workspace location (if not using .env)
workspace: "D:/my-custom-workspace"

# Tool path overrides (if non-standard installation)
tools:
  blender_exe: "C:/custom/blender/blender.exe"
```

## Configuration Schema Changes

### New Pydantic Models

```python
class ToolPaths(BaseModel):
    """External tool paths."""
    blender_exe: Path = Field(
        default=Path("C:/Program Files/Blender Foundation/Blender 4.5/blender.exe"),
        description="Path to Blender executable (used by VFScore for HQ rendering)"
    )

class MetricsConfig(BaseModel):
    """Metrics computation defaults."""
    fscore: FScoreDefaults = Field(default_factory=FScoreDefaults)
    vfscore: VFScoreDefaults = Field(default_factory=VFScoreDefaults)

class FScoreDefaults(BaseModel):
    """Default parameters for FScore computation."""
    default_n_points: int = 100000
    default_timeout_s: int = 300

class VFScoreDefaults(BaseModel):
    """Default parameters for VFScore computation."""
    default_repeats: int = 1
    default_timeout_s: int = 600

class GlobalConfig(BaseModel):
    """Global (repo) configuration."""
    algorithms: List[str]
    thresholds: Thresholds
    batch: BatchConfig = Field(default_factory=BatchConfig)
    tools: ToolPaths = Field(default_factory=ToolPaths)  # NEW
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)  # NEW
```

## Migration Plan

### Phase 1: Schema Update
1. Add `ToolPaths` model to `schema.py`
2. Add `MetricsConfig` with nested defaults to `schema.py`
3. Update `GlobalConfig` to include `tools` and `metrics` fields
4. Update `UserConfig` to allow optional `tools` override

### Phase 2: Configuration Files
1. Update `global.yaml` with new `tools` and `metrics` sections
2. Create `.env.template` with comprehensive comments
3. Update existing `.env` to match new format (secrets only)

### Phase 3: Loader Changes
1. Update `loader.py` to merge tool paths (global → user → env?)
2. Add validation: check Blender path exists if VFScore will be used
3. Add helper: `get_tool_path(tool_name: str) -> Path`

### Phase 4: Adapter Integration
1. Update VFScore adapter to pass `blender_exe` from config
2. Verify FScore doesn't need tool paths
3. Document how to override tool paths per-machine

### Phase 5: Documentation
1. Update CLAUDE.md with new config architecture
2. Update README with .env.template → .env instructions
3. Add troubleshooting section for missing tools

## Tool Path Validation

```python
def validate_tool_paths(config: EffectiveConfig, required_tools: list[str]) -> None:
    """
    Validate that required tool paths exist.

    Args:
        config: Loaded configuration
        required_tools: List of tool names to validate (e.g., ["blender_exe"])

    Raises:
        FileNotFoundError: If a required tool path doesn't exist
    """
    tools = config.global_config.tools

    # Allow user config to override global tools
    if config.user_config and hasattr(config.user_config, 'tools'):
        # Merge user tool overrides
        for tool_name in required_tools:
            user_override = getattr(config.user_config.tools, tool_name, None)
            if user_override:
                setattr(tools, tool_name, user_override)

    # Validate each required tool
    missing = []
    for tool_name in required_tools:
        tool_path = getattr(tools, tool_name, None)
        if tool_path and not Path(tool_path).exists():
            missing.append(f"{tool_name}: {tool_path}")

    if missing:
        raise FileNotFoundError(
            f"Required tools not found:\n" +
            "\n".join(f"  - {m}" for m in missing) +
            f"\n\nUpdate paths in:\n"
            f"  - global.yaml (tools section)\n"
            f"  - ~/.config/archi3d/config.yaml (user override)\n"
        )
```

## Usage in VFScore Adapter

```python
# Before calling VFScore evaluator
from archi3d.config.loader import load_config

config = load_config()
blender_exe = config.global_config.tools.blender_exe

# Pass to VFScore
result = evaluate_visual_fidelity(
    ...,
    blender_exe=str(blender_exe)  # Override VFScore's hardcoded default
)
```

## Backward Compatibility

- **Existing .env files** will continue to work (workspace + FAL_KEY)
- **Existing global.yaml** will work (tools/metrics have defaults)
- **No breaking changes** to existing workflows
- **Validation warnings** if Blender not found (non-fatal for FScore-only usage)

## Benefits

1. **Clear separation**: Secrets in .env, config in YAML
2. **Discoverable defaults**: Tool paths documented in global.yaml
3. **Machine-specific overrides**: User config for custom installations
4. **Validation**: Early warning if tools missing
5. **Extensibility**: Easy to add new tool paths (e.g., FScore CLI)

## Implementation Checklist

- [ ] Update schema.py with ToolPaths and MetricsConfig
- [ ] Update global.yaml with tools and metrics sections
- [ ] Create .env.template with comprehensive documentation
- [ ] Update loader.py to merge and validate tool paths
- [ ] Update VFScore adapter to use config.tools.blender_exe
- [ ] Test configuration loading with various scenarios
- [ ] Update CLAUDE.md with new architecture
- [ ] Create migration guide for existing users

---

**Status**: Design complete, awaiting implementation
**Date**: 2025-11-26

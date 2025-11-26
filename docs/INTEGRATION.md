# Metric Adapter Integration Guide

This guide explains how to integrate FScore and VFScore metric tools with archi3D using the plug-in adapter system introduced in Phase 8.

## Overview

Archi3D uses an **adapter discovery system** that supports three integration methods (in priority order):

1. **Python Import** (Recommended): Install metric tools as Python packages
2. **Entry Points**: Third-party plugins via setuptools entry points
3. **CLI Invocation** (Fallback): External commands via environment variables

## FScore Integration

FScore provides geometry-based quality metrics (F-score, precision, recall, Chamfer distance).

### Method 1: Python Import (Recommended)

Install FScore as an editable package:

```bash
# From FScore project directory
pip install -e "path/to/FScore"

# Or if distributed as a wheel
pip install fscore
```

Archi3D will automatically detect the `fscore` module and use its Python API.

**Verification:**
```bash
python -c "import fscore; print('FScore installed')"
```

### Method 2: CLI Fallback

If FScore is not installable as a Python package, configure it as an external CLI:

**Linux/macOS:**
```bash
export ARCHI3D_FSCORE_CLI="python -m fscore"
```

**Windows (PowerShell):**
```powershell
$env:ARCHI3D_FSCORE_CLI="python -m fscore"
```

**Windows (CMD):**
```cmd
set ARCHI3D_FSCORE_CLI=python -m fscore
```

### Force Resolution Mode

Override automatic discovery:

```bash
# Force import mode (fail if not installable)
export ARCHI3D_FSCORE_IMPL="import"

# Force CLI mode (even if package is installed)
export ARCHI3D_FSCORE_IMPL="cli"

# Auto mode (default: try import → entry points → CLI)
export ARCHI3D_FSCORE_IMPL="auto"
```

## VFScore Integration

VFScore provides visual fidelity metrics via LLM-based rendering comparison.

### Method 1: Python Import (Recommended)

```bash
# From VFScore project directory
pip install -e "path/to/VFScore"

# Or if distributed as a wheel
pip install vfscore
```

**Verification:**
```bash
python -c "import vfscore; print('VFScore installed')"
```

### Method 2: CLI Fallback

**Linux/macOS:**
```bash
export ARCHI3D_VFSCORE_CLI="python -m vfscore"
```

**Windows (PowerShell):**
```powershell
$env:ARCHI3D_VFSCORE_CLI="python -m vfscore"
```

**Windows (CMD):**
```cmd
set ARCHI3D_VFSCORE_CLI=python -m vfscore
```

### Force Resolution Mode

```bash
export ARCHI3D_VFSCORE_IMPL="import"  # Force import
export ARCHI3D_VFSCORE_IMPL="cli"     # Force CLI
export ARCHI3D_VFSCORE_IMPL="auto"    # Auto (default)
```

## Third-Party Plugins

Custom metric adapters can be registered via setuptools entry points.

### Plugin Development

1. **Implement the Adapter Protocol**

Create a class that implements the `FScoreAdapter` or `VFScoreAdapter` protocol:

```python
# my_fscore_plugin/adapter.py
from archi3d.plugins.metrics import FScoreAdapter
from archi3d.metrics.fscore_adapter import FScoreRequest, FScoreResponse

class CustomFScoreAdapter:
    """Custom FScore implementation."""

    def evaluate(self, req: FScoreRequest) -> FScoreResponse:
        """Implement your custom FScore evaluation logic."""
        try:
            # Your custom evaluation here
            result = {...}  # Canonical payload schema

            return FScoreResponse(
                ok=True,
                payload=result,
                tool_version="1.0.0",
                runtime_s=123.45,
            )
        except Exception as e:
            return FScoreResponse(ok=False, error=str(e))
```

2. **Register via Entry Points**

In your plugin's `pyproject.toml`:

```toml
[project.entry-points."archi3d.metrics_adapters"]
fscore = "my_fscore_plugin.adapter:CustomFScoreAdapter"
```

Or in `setup.py`:

```python
setup(
    name="my-fscore-plugin",
    # ...
    entry_points={
        "archi3d.metrics_adapters": [
            "fscore=my_fscore_plugin.adapter:CustomFScoreAdapter",
        ],
    },
)
```

3. **Install the Plugin**

```bash
pip install my-fscore-plugin
```

Archi3D will automatically discover and use your plugin if the built-in adapters are unavailable.

### Canonical Payload Schema

Custom adapters **must** return responses matching the canonical schema:

**FScore Response:**
```python
{
    "fscore": float,
    "precision": float,
    "recall": float,
    "chamfer_l2": float,
    "n_points": int,
    "alignment": {
        "scale": float,
        "rotation_quat": {"w": float, "x": float, "y": float, "z": float},
        "translation": {"x": float, "y": float, "z": float}
    },
    "dist_stats": {"mean": float, "median": float, "p95": float, "p99": float, "max": float},
    "mesh_meta": {
        "gt_vertices": int, "gt_triangles": int,
        "pred_vertices": int, "pred_triangles": int
    }
}
```

**VFScore Response:**
```python
{
    "overall": float,
    "finish": float,
    "texture_identity": float,
    "texture_scale_placement": float,
    "repeats_n": int,
    "iqr": float,
    "std": float,
    "llm_model": str,
    "rubric_weights": dict,
    "config_hash": str,
    "rationales": list[str],
    "render_runtime_s": float,
    "scoring_runtime_s": float
}
```

## Troubleshooting

### FScore Not Found

**Error:**
```
FScore adapter not found. To enable FScore metrics:

  Option 1 (Recommended): Install FScore as a Python package:
    pip install archi3d[fscore]
    # or for editable install:
    pip install -e "path/to/FScore"

  Option 2: Use FScore as external CLI:
    export ARCHI3D_FSCORE_CLI='python -m fscore'
    ...
```

**Solution:**
1. Check if `fscore` module is installed: `python -c "import fscore"`
2. If using CLI, verify `ARCHI3D_FSCORE_CLI` is set and executable
3. Check discovery mode: `echo $ARCHI3D_FSCORE_IMPL`

### VFScore Not Found

**Error:**
```
VFScore adapter not found. To enable VFScore metrics:
  ...
```

**Solution:**
1. Check if `vfscore` module is installed: `python -c "import vfscore"`
2. If using CLI, verify `ARCHI3D_VFSCORE_CLI` is set and executable
3. Check discovery mode: `echo $ARCHI3D_VFSCORE_IMPL`

### Plugin Not Discovered

**Issue:** Third-party plugin installed but not used.

**Solution:**
1. Verify entry point registration:
   ```bash
   python -c "from importlib.metadata import entry_points; print(list(entry_points(group='archi3d.metrics_adapters')))"
   ```

2. Check that built-in adapters are disabled (entry points have lowest priority):
   ```bash
   # Uninstall fscore to test custom plugin
   pip uninstall fscore
   ```

3. Verify plugin module imports correctly:
   ```python
   from my_plugin import MyAdapter
   adapter = MyAdapter()
   ```

## Discovery Priority

The adapter resolution order is:

1. **Import:** Check if `fscore`/`vfscore` module is installed
2. **Entry Points:** Load from `archi3d.metrics_adapters` namespace
3. **CLI:** Use command from `ARCHI3D_FSCORE_CLI`/`ARCHI3D_VFSCORE_CLI`

Environment variables (`ARCHI3D_*_IMPL`) can force a specific mode, overriding auto-detection.

## Best Practices

1. **Use Import Mode for Development:** Editable installs allow rapid iteration
2. **Use CLI Mode for CI/CD:** Docker containers with external tools
3. **Version Your Adapters:** Include `tool_version` in responses for reproducibility
4. **Test Your Plugins:** Verify canonical schema compliance
5. **Document Requirements:** Specify dependencies in plugin's `pyproject.toml`

## Configuration Summary

| Environment Variable       | Values                | Default | Description                   |
|----------------------------|-----------------------|---------|-------------------------------|
| `ARCHI3D_FSCORE_IMPL`      | `import`/`cli`/`auto` | `auto`  | Force FScore resolution mode  |
| `ARCHI3D_VFSCORE_IMPL`     | `import`/`cli`/`auto` | `auto`  | Force VFScore resolution mode |
| `ARCHI3D_FSCORE_CLI`       | Command string        | -       | FScore CLI command            |
| `ARCHI3D_VFSCORE_CLI`      | Command string        | -       | VFScore CLI command           |

---

For more details on the underlying architecture, see `CLAUDE.md` (Phase 8 section).

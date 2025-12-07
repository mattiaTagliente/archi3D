# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

# archi3d/__init__.py
"""
Archi3D: run-centric Image-to-3D testing harness.

This package exposes a Typer-based CLI and a small set of modules for:
- catalog building (dataset -> tables/items.csv)
- batch creation (freeze a run, create a queue)
- run worker (claim jobs, run adapters, append results.parquet)
- metrics compute (fill/update metrics)
- report build (summaries for analysis)

Versioning policy: semantic (MAJOR.MINOR.PATCH)
"""

__all__ = ["__version__"]

# Keep in sync with pyproject.toml [project].version
__version__ = "0.3.0"

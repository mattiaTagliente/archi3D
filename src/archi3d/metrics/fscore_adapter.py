"""
FScore Adapter — isolates external FScore tool integration.

Provides a unified interface to invoke the FScore evaluator, with
fallback between:
1. Python import (preferred)
2. CLI invocation (fallback)

The adapter normalizes the tool's output into a canonical payload
schema for persistence and CSV upserts.
"""

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FScoreRequest:
    """Input specification for FScore evaluation."""

    gt_path: Path
    cand_path: Path
    n_points: int
    out_dir: Path
    timeout_s: int | None = None


@dataclass
class FScoreResponse:
    """Normalized FScore evaluation result."""

    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    tool_version: str | None = None
    config_hash: str | None = None
    runtime_s: float | None = None
    visualization_path: str | None = None
    error: str | None = None


def _normalize_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize FScore tool output into canonical payload schema.

    Expected canonical schema:
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

    Missing fields are filled with None.
    """
    normalized = {
        "fscore": raw.get("fscore"),
        "precision": raw.get("precision"),
        "recall": raw.get("recall"),
        "chamfer_l2": raw.get("chamfer_l2"),
        "n_points": raw.get("n_points"),
        "alignment": {
            "scale": None,
            "rotation_quat": {"w": None, "x": None, "y": None, "z": None},
            "translation": {"x": None, "y": None, "z": None},
        },
        "dist_stats": {
            "mean": None,
            "median": None,
            "p95": None,
            "p99": None,
            "max": None,
        },
        "mesh_meta": {
            "gt_vertices": None,
            "gt_triangles": None,
            "pred_vertices": None,
            "pred_triangles": None,
        },
    }

    # Merge alignment if present
    if "alignment" in raw and raw["alignment"]:
        align = raw["alignment"]
        if "scale" in align:
            normalized["alignment"]["scale"] = align["scale"]
        if "rotation_quat" in align:
            normalized["alignment"]["rotation_quat"].update(align["rotation_quat"])
        if "translation" in align:
            normalized["alignment"]["translation"].update(align["translation"])

    # Merge dist_stats if present
    if "dist_stats" in raw and raw["dist_stats"]:
        normalized["dist_stats"].update(raw["dist_stats"])

    # Merge mesh_meta if present
    if "mesh_meta" in raw and raw["mesh_meta"]:
        normalized["mesh_meta"].update(raw["mesh_meta"])

    # Pass through additional fields (alignment_log, timing, version, config_hash)
    if "alignment_log" in raw:
        normalized["alignment_log"] = raw["alignment_log"]
    if "timing" in raw:
        normalized["timing"] = raw["timing"]
    if "version" in raw:
        normalized["version"] = raw["version"]
    if "config_hash" in raw:
        normalized["config_hash"] = raw["config_hash"]
    if "visualization_path" in raw:
        normalized["visualization_path"] = raw["visualization_path"]

    return normalized


def _try_import_api(req: FScoreRequest) -> FScoreResponse | None:
    """
    Attempt to use FScore via Python import.

    Returns FScoreResponse if successful, None if import fails.
    """
    try:
        # Try importing FScore evaluator
        from fscore.evaluator import evaluate_one  # type: ignore  # noqa: PLC0415

        start = time.perf_counter()
        result = evaluate_one(
            gt_path=str(req.gt_path),
            cand_path=str(req.cand_path),
            n_points=req.n_points,
            out_dir=str(req.out_dir),
            timeout_s=req.timeout_s,
        )
        runtime = time.perf_counter() - start

        # Normalize result
        payload = _normalize_payload(result)

        return FScoreResponse(
            ok=True,
            payload=payload,
            tool_version=result.get("version"),
            config_hash=result.get("config_hash"),
            runtime_s=runtime,
            visualization_path=result.get("visualization_path"),
        )

    except ImportError:
        return None  # Import failed, will try CLI fallback
    except Exception as e:
        return FScoreResponse(
            ok=False,
            error=f"FScore error: {str(e)[:200]}",
        )


def _try_cli_invocation(req: FScoreRequest) -> FScoreResponse:
    """
    Fallback: invoke FScore via CLI.

    Expected CLI interface:
    python -m fscore --gt <path> --cand <path> --n-points <n> --out-dir <dir>

    Returns FScoreResponse with ok=True on success, ok=False on error.
    """
    try:
        cmd = [
            "python",
            "-m",
            "fscore",
            "--gt",
            str(req.gt_path),
            "--cand",
            str(req.cand_path),
            "--n-points",
            str(req.n_points),
            "--out-dir",
            str(req.out_dir),
        ]

        start = time.perf_counter()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=req.timeout_s,
            check=True,
        )
        runtime = time.perf_counter() - start

        # Try to parse result from stdout or result.json in out_dir
        result_path = req.out_dir / "result.json"
        if result_path.exists():
            with open(result_path, encoding="utf-8") as f:
                raw = json.load(f)
        else:
            # Try parsing stdout as JSON
            raw = json.loads(result.stdout)

        payload = _normalize_payload(raw)

        return FScoreResponse(
            ok=True,
            payload=payload,
            tool_version=raw.get("version"),
            config_hash=raw.get("config_hash"),
            runtime_s=runtime,
            visualization_path=raw.get("visualization_path"),
        )

    except subprocess.TimeoutExpired:
        return FScoreResponse(ok=False, error="FScore timeout")
    except subprocess.CalledProcessError as e:
        return FScoreResponse(
            ok=False,
            error=f"FScore failed (exit {e.returncode}): {e.stderr[:150]}",
        )
    except Exception as e:
        return FScoreResponse(
            ok=False,
            error=f"FScore error: {str(e)[:200]}",
        )


def evaluate_fscore(req: FScoreRequest) -> FScoreResponse:
    """
    Main entry point for FScore evaluation.

    Uses adapter discovery to find suitable implementation (import/CLI/entry-point).

    Args:
        req: FScore evaluation request

    Returns:
        FScoreResponse with ok=True on success, ok=False with error on failure
    """
    # Ensure output directory exists
    req.out_dir.mkdir(parents=True, exist_ok=True)

    # Discover and invoke adapter
    try:
        from archi3d.metrics.discovery import get_fscore_adapter  # noqa: PLC0415

        adapter_fn = get_fscore_adapter()
        response = adapter_fn(req)

        if response is None:
            return FScoreResponse(ok=False, error="Adapter returned None")

        return response

    except Exception as e:
        # Return error response (includes AdapterNotFoundError)
        return FScoreResponse(ok=False, error=str(e))

"""
VFScore Adapter — isolates external VFScore tool integration.

Provides a unified interface to invoke the VFScore evaluator, with
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
class VFScoreRequest:
    """Input specification for VFScore evaluation."""

    cand_glb: Path
    ref_images: list[Path]
    out_dir: Path
    repeats: int
    timeout_s: int | None = None
    workspace: Path | None = None
    blender_exe: Path | None = None


@dataclass
class VFScoreResponse:
    """Normalized VFScore evaluation result."""

    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    tool_version: str | None = None
    config_hash: str | None = None
    render_runtime_s: float | None = None
    scoring_runtime_s: float | None = None
    error: str | None = None


def _normalize_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize VFScore tool output into canonical payload schema.

    Objective2 pipeline schema (NEW):
    - Core metrics: vfscore_overall, lpips_distance, lpips_model, iou, mask_error, pose_confidence
    - Score params: gamma, pose_compensation_c
    - Pose params: azimuth_deg, elevation_deg, radius, fov_deg, obj_yaw_deg
    - Pipeline stats: pipeline_mode, num_step2_candidates, num_step4_candidates, num_selected_candidates, best_lpips_idx
    - Provenance: artifacts_dir, gt_image_path, render_image_path

    Legacy fields (DEPRECATED, kept for backward compatibility):
    - vf_subscores_median, repeats_n, scores_all, subscores_all, iqr, std
    - llm_model, rubric_weights, render_settings

    Missing fields are filled with None.
    """
    normalized = {
        # Core metrics
        "vfscore_overall_median": raw.get("vfscore_overall_median"),
        "lpips_distance": raw.get("lpips_distance"),
        "lpips_model": raw.get("lpips_model"),
        "iou": raw.get("iou"),
        "mask_error": raw.get("mask_error"),
        "pose_confidence": raw.get("pose_confidence"),

        # Score combination parameters
        "gamma": raw.get("gamma"),
        "pose_compensation_c": raw.get("pose_compensation_c"),

        # Final pose parameters
        "azimuth_deg": raw.get("azimuth_deg"),
        "elevation_deg": raw.get("elevation_deg"),
        "radius": raw.get("radius"),
        "fov_deg": raw.get("fov_deg"),
        "obj_yaw_deg": raw.get("obj_yaw_deg"),

        # Pipeline statistics
        "pipeline_mode": raw.get("pipeline_mode"),
        "num_step2_candidates": raw.get("num_step2_candidates"),
        "num_step4_candidates": raw.get("num_step4_candidates"),
        "num_selected_candidates": raw.get("num_selected_candidates"),
        "best_lpips_idx": raw.get("best_lpips_idx"),

        # Artifact paths (workspace-relative)
        "artifacts_dir": raw.get("artifacts_dir"),
        "gt_image_path": raw.get("gt_image_path"),
        "render_image_path": raw.get("render_image_path"),

        # DEPRECATED fields (kept for backward compatibility)
        "vf_subscores_median": raw.get("vf_subscores_median", {
            "finish": None,
            "texture_identity": None,
            "texture_scale_placement": None,
        }),
        "repeats_n": raw.get("repeats_n"),
        "scores_all": raw.get("scores_all", []),
        "subscores_all": raw.get("subscores_all", []),
        "iqr": raw.get("iqr"),
        "std": raw.get("std"),
        "llm_model": raw.get("llm_model"),
        "rubric_weights": raw.get("rubric_weights", {
            "finish": None,
            "texture_identity": None,
            "texture_scale_placement": None,
        }),
        "render_settings": raw.get("render_settings", {
            "engine": "pyrender",
            "hdri": None,
            "camera": None,
            "seed": None,
        }),
    }

    return normalized


def _try_import_api(req: VFScoreRequest) -> VFScoreResponse | None:
    """
    Attempt to use VFScore via Python import.

    Returns VFScoreResponse if successful, None if import fails.
    """
    try:
        # Try importing VFScore evaluator
        # This is a placeholder for the actual import path
        # Expected interface:
        # evaluate_visual_fidelity(cand_glb, ref_images, out_dir, repeats, timeout_s)
        from vfscore.evaluator import evaluate_visual_fidelity  # type: ignore  # noqa: PLC0415

        start_total = time.perf_counter()
        result = evaluate_visual_fidelity(
            cand_glb=str(req.cand_glb),
            ref_images=[str(p) for p in req.ref_images],
            out_dir=str(req.out_dir),
            repeats=req.repeats,
            timeout_s=req.timeout_s,
            workspace=str(req.workspace) if req.workspace else None,
            blender_exe=str(req.blender_exe) if req.blender_exe else None,
            quiet=True,  # Enable quiet mode when called from archi3D
        )
        total_runtime = time.perf_counter() - start_total

        # Normalize result
        payload = _normalize_payload(result)

        # Extract render and scoring runtimes if available
        render_runtime = result.get("render_runtime_s")
        scoring_runtime = result.get("scoring_runtime_s")

        # If not provided separately, estimate from total
        if render_runtime is None and scoring_runtime is None:
            # Heuristic: assume 60% render, 40% scoring
            render_runtime = total_runtime * 0.6
            scoring_runtime = total_runtime * 0.4

        return VFScoreResponse(
            ok=True,
            payload=payload,
            tool_version=result.get("version"),
            config_hash=result.get("config_hash"),
            render_runtime_s=render_runtime,
            scoring_runtime_s=scoring_runtime,
        )

    except ImportError:
        return None  # Import failed, will try CLI fallback
    except Exception as e:
        # Include exception type and full traceback for debugging
        import traceback
        tb_str = traceback.format_exc()
        return VFScoreResponse(
            ok=False,
            error=f"VFScore error: {str(e)}\nTraceback:\n{tb_str}",
        )


def _try_cli_invocation(req: VFScoreRequest) -> VFScoreResponse:
    """
    Fallback: invoke VFScore via CLI.

    Expected CLI interface:
    python -m vfscore --cand-glb <path> --ref-images <path1> <path2> ...
                      --out-dir <dir> --repeats <n>

    Returns VFScoreResponse with ok=True on success, ok=False on error.
    """
    try:
        cmd = [
            "python",
            "-m",
            "vfscore",
            "--cand-glb",
            str(req.cand_glb),
            "--ref-images",
        ]

        # Add all reference image paths
        cmd.extend([str(p) for p in req.ref_images])

        cmd.extend([
            "--out-dir",
            str(req.out_dir),
            "--repeats",
            str(req.repeats),
        ])

        start = time.perf_counter()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=req.timeout_s,
            check=True,
        )
        total_runtime = time.perf_counter() - start

        # Try to parse result from result.json in out_dir
        result_path = req.out_dir / "result.json"
        if result_path.exists():
            with open(result_path, encoding="utf-8") as f:
                raw = json.load(f)
        else:
            # Try parsing stdout as JSON
            raw = json.loads(result.stdout)

        payload = _normalize_payload(raw)

        # Extract runtimes
        render_runtime = raw.get("render_runtime_s")
        scoring_runtime = raw.get("scoring_runtime_s")

        if render_runtime is None and scoring_runtime is None:
            render_runtime = total_runtime * 0.6
            scoring_runtime = total_runtime * 0.4

        return VFScoreResponse(
            ok=True,
            payload=payload,
            tool_version=raw.get("version"),
            config_hash=raw.get("config_hash"),
            render_runtime_s=render_runtime,
            scoring_runtime_s=scoring_runtime,
        )

    except subprocess.TimeoutExpired:
        return VFScoreResponse(ok=False, error="VFScore timeout")
    except subprocess.CalledProcessError as e:
        return VFScoreResponse(
            ok=False,
            error=f"VFScore failed (exit {e.returncode}): {e.stderr[:150]}",
        )
    except Exception as e:
        return VFScoreResponse(
            ok=False,
            error=f"VFScore error: {str(e)[:200]}",
        )


def evaluate_vfscore(req: VFScoreRequest) -> VFScoreResponse:
    """
    Main entry point for VFScore evaluation.

    Tries import API first, falls back to CLI invocation.

    Args:
        req: VFScore evaluation request

    Returns:
        VFScoreResponse with ok=True on success, ok=False with error on failure
    """
    # Ensure output directory exists
    req.out_dir.mkdir(parents=True, exist_ok=True)

    # Validate inputs
    if not req.cand_glb.exists():
        return VFScoreResponse(
            ok=False,
            error=f"Candidate GLB not found: {req.cand_glb}",
        )

    if not req.ref_images:
        return VFScoreResponse(
            ok=False,
            error="No reference images provided",
        )

    # Check that at least one reference image exists
    existing_refs = [p for p in req.ref_images if p.exists()]
    if not existing_refs:
        return VFScoreResponse(
            ok=False,
            error="No reference images found on disk",
        )

    # Update request to only use existing images
    req.ref_images = existing_refs

    # Discover and invoke adapter
    try:
        from archi3d.metrics.discovery import get_vfscore_adapter  # noqa: PLC0415

        adapter_fn = get_vfscore_adapter()
        response = adapter_fn(req)

        if response is None:
            return VFScoreResponse(ok=False, error="Adapter returned None")

        return response

    except Exception as e:
        # Return error response (includes AdapterNotFoundError)
        return VFScoreResponse(ok=False, error=str(e))

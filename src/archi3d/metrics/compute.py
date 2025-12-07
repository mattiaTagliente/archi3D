# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

# archi3d/metrics/compute.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from filelock import FileLock

from archi3d import __version__
from archi3d.config.paths import PathResolver


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_metric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add lpips/fscore columns if missing (kept None for placeholders)."""
    if "lpips" not in df.columns:
        df["lpips"] = None
    if "fscore" not in df.columns:
        df["fscore"] = None
    return df


def _metrics_json_path(paths: PathResolver, run_id: str, output_glb_relpath: str) -> Path:
    """
    Derive metrics JSON filename from the GLB artifact name.
    GLB relpath looks like: runs/<run_id>/outputs/<algo>/<core>.glb
    Metrics JSON lives at:  runs/<run_id>/metrics/<core>.json
    """
    glb_name = Path(output_glb_relpath).name
    json_name = Path(glb_name).with_suffix(".json")
    return paths.metrics_dir(run_id) / json_name


def run(
    run_id: str,
    algo: Optional[str],
    recompute: bool,
    paths: PathResolver,
) -> int:
    """
    Update placeholder metrics sidecars and ensure lpips/fscore columns exist.
    Rules:
      - Touch only rows with status == 'completed' for the given run_id (and algo if provided).
      - If metrics JSON exists and has 'computed_at' not null, skip unless --recompute.
      - We DO NOT compute real metrics here; placeholders remain (None).
    Returns the number of updated records (sidecars touched and registry persisted).
    """
    paths.validate_expected_tree()
    parquet_path = paths.results_parquet
    if not parquet_path.exists():
        raise FileNotFoundError(
            f"results.parquet not found at {parquet_path}. "
            "Run at least one worker to create it."
        )

    df = pd.read_parquet(parquet_path)
    if df.empty:
        return 0

    # Filter rows
    mask = (df["run_id"] == run_id) & (df["status"] == "completed")
    if algo:
        mask &= df["algo"] == algo
    work = df[mask].copy()
    if work.empty:
        return 0

    # Ensure metric columns exist in full DF
    df = _ensure_metric_columns(df)

    updated = 0

    for idx, row in work.iterrows():
        output_rel = row.get("output_glb_relpath", "") or ""
        if not output_rel:
            # No artifact path → nothing to write a sidecar for
            continue

        mpath = _metrics_json_path(paths, run_id, output_rel)

        # Skip if already computed and not recomputing
        if mpath.exists() and not recompute:
            try:
                payload = json.loads(mpath.read_text(encoding="utf-8"))
                if payload.get("computed_at"):
                    continue
            except Exception:
                # If unreadable, we will overwrite it
                pass

        # Prepare placeholder payload (keep metrics None)
        payload = {
            "job_id": row.get("job_id", ""),
            "product_id": row.get("product_id", ""),
            "algo": row.get("algo", ""),
            "n_images": int(row.get("n_images", 0)) if row.get("n_images", "") != "" else 0,
            "image_suffixes": row.get("img_suffixes", ""),
            "run_id": run_id,
            "code_version": __version__,
            "lpips": None,
            "fscore": None,
            "computed_at": _now_iso(),
        }
        mpath.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        # Ensure DF has placeholder columns; do not change values (remain None)
        # Touching DF only to guarantee schema; no per-row value update needed now.
        updated += 1

    # Persist DF (only if we ensured new columns or touched anything)
    if updated > 0:
        lock_path = parquet_path.with_suffix(".parquet.lock")
        with FileLock(str(lock_path)):
            df.to_parquet(parquet_path, index=False)

    return updated

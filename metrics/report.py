# archi3d/reporting/report.py
from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pandas as pd
import yaml

from archi3d.config.loader import load_config
from archi3d.config.paths import PathResolver


def _safe_read_parquet(p: Path) -> pd.DataFrame:
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    return df if not df.empty else pd.DataFrame()


def _safe_read_csv(p: Path, dtype=None) -> pd.DataFrame:
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, dtype=dtype).fillna("")
    return df


def _ensure_metric_cols(df: pd.DataFrame) -> pd.DataFrame:
    if "lpips" not in df.columns:
        df["lpips"] = None
    if "fscore" not in df.columns:
        df["fscore"] = None
    return df


def build(run_id: str, out_dir: Path, paths: PathResolver) -> List[Path]:
    """
    Produce lightweight, CSV-first artifacts for a run:
      - overview.yaml                 (counts, skip reasons, thresholds)
      - by_algo.csv                   (completed/failed, durations, pass rates)
      - failures.csv                  (error rows)
      - outputs_index.csv             (handy list of artifacts)
    Returns the list of generated artifact paths.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    eff = load_config()
    thr = eff.global_config.thresholds

    # Inputs
    results_path = paths.results_parquet
    manifest_path = paths.manifest_inputs_csv(run_id)

    df = _safe_read_parquet(results_path)
    man = _safe_read_csv(manifest_path, dtype=str)

    # Filter this run
    if not df.empty:
        df = df[df["run_id"] == run_id].copy()

    # Overview counts
    queued = int(man[man.get("reason", "") == ""].shape[0]) if not man.empty else 0
    skipped = int(man[man.get("reason", "") != ""].shape[0]) if not man.empty else 0
    completed = int(df[df.get("status", "") == "completed"].shape[0]) if not df.empty else 0
    failed = int(df[df.get("status", "") == "failed"].shape[0]) if not df.empty else 0

    # Acceptance (only on completed rows with metrics present)
    df = _ensure_metric_cols(df)
    has_metrics = (
        ~pd.isna(df["lpips"]) & ~pd.isna(df["fscore"])
        if not df.empty and {"lpips", "fscore"}.issubset(df.columns)
        else pd.Series([], dtype=bool)
    )
    passed_mask = has_metrics & (df["lpips"] <= thr.lpips_max) & (df["fscore"] >= thr.fscore_min)
    passed = int(df[passed_mask].shape[0]) if not df.empty else 0

    overview = {
        "run_id": run_id,
        "counts": {
            "queued": queued,
            "skipped": skipped,
            "completed": completed,
            "failed": failed,
            "passed_thresholds": passed,
        },
        "skip_reasons": (
            man["reason"].value_counts(dropna=False).to_dict() if not man.empty and "reason" in man else {}
        ),
        "thresholds": {"lpips_max": float(thr.lpips_max), "fscore_min": float(thr.fscore_min)},
    }

    # by-algorithm summary
    by_algo_rows = []
    if not df.empty:
        for algo, g in df.groupby("algo"):
            g_completed = g[g["status"] == "completed"]
            g_failed = g[g["status"] == "failed"]
            n_completed = int(g_completed.shape[0])
            n_failed = int(g_failed.shape[0])
            mean_dur = float(g_completed["duration_s"].mean()) if "duration_s" in g_completed else float("nan")
            med_dur = float(g_completed["duration_s"].median()) if "duration_s" in g_completed else float("nan")

            # pass rate (only rows with metrics)
            g_cm = g_completed.dropna(subset=["lpips", "fscore"]) if {"lpips", "fscore"}.issubset(g_completed.columns) else pd.DataFrame()
            if not g_cm.empty:
                pass_mask = (g_cm["lpips"] <= thr.lpips_max) & (g_cm["fscore"] >= thr.fscore_min)
                pass_rate = float(pass_mask.mean())
                n_with_metrics = int(g_cm.shape[0])
            else:
                pass_rate = float("nan")
                n_with_metrics = 0

            by_algo_rows.append(
                {
                    "algo": algo,
                    "completed": n_completed,
                    "failed": n_failed,
                    "mean_duration_s": round(mean_dur, 6) if mean_dur == mean_dur else "",  # NaN-safe
                    "median_duration_s": round(med_dur, 6) if med_dur == med_dur else "",
                    "with_metrics": n_with_metrics,
                    "pass_rate": round(pass_rate, 6) if pass_rate == pass_rate else "",  # NaN-safe
                }
            )

    # failures detail
    failures_df = pd.DataFrame()
    if not df.empty:
        failures_df = df[df["status"] == "failed"][
            ["product_id", "algo", "img_suffixes", "error_msg", "started_at", "finished_at"]
        ].copy()

    # outputs index
    outputs_df = pd.DataFrame()
    if not df.empty:
        outputs_df = df[df["status"] == "completed"][
            ["product_id", "algo", "img_suffixes", "output_glb_relpath"]
        ].copy()

    # Write artifacts
    artifacts: List[Path] = []

    overview_path = out_dir / "overview.yaml"
    overview_path.write_text(yaml.safe_dump(overview, sort_keys=False), encoding="utf-8")
    artifacts.append(overview_path)

    by_algo_path = out_dir / "by_algo.csv"
    pd.DataFrame(by_algo_rows).to_csv(by_algo_path, index=False, encoding="utf-8")
    artifacts.append(by_algo_path)

    failures_path = out_dir / "failures.csv"
    failures_df.to_csv(failures_path, index=False, encoding="utf-8")
    artifacts.append(failures_path)

    outputs_idx_path = out_dir / "outputs_index.csv"
    outputs_df.to_csv(outputs_idx_path, index=False, encoding="utf-8")
    artifacts.append(outputs_idx_path)

    return artifacts

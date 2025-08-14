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
    """Safely reads a Parquet file, returning an empty DataFrame if it doesn't exist."""
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    return df if not df.empty else pd.DataFrame()


def _safe_read_csv(p: Path, dtype=None) -> pd.DataFrame:
    """Safely reads a CSV file, returning an empty DataFrame if it doesn't exist."""
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, dtype=dtype).fillna("")
    return df


def _ensure_metric_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Ensures that the DataFrame has 'lpips' and 'fscore' columns, adding them as None if missing."""
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

    # --- Load all necessary data ---
    results_path = paths.results_parquet
    manifest_path = paths.manifest_inputs_csv(run_id)
    items_path = paths.items_csv

    df = _safe_read_parquet(results_path)
    man = _safe_read_csv(manifest_path, dtype=str)
    items_df = _safe_read_csv(items_path, dtype=str)

    # Filter this run
    if not df.empty:
        df = df[df["run_id"] == run_id].copy()

    # --- Merge results with item metadata to get categories ---
    report_df = df
    if not df.empty and not items_df.empty:
        # Define all the columns we want to bring in from the items catalog
        merge_cols = [
            "product_id", "variant", "product_name",
            "category_l1", "category_l2", "category_l3"
        ]
        # Ensure we only try to merge columns that actually exist in items_df
        cols_to_merge = [col for col in merge_cols if col in items_df.columns]
        
        # USE A COMPOUND KEY FOR MERGING
        report_df = pd.merge(
            df,
            items_df[cols_to_merge],
            on=["product_id", "variant"], # <-- COMPOUND KEY
            how="left"
        )
    
    # All subsequent operations will use `report_df` which is the filtered and enriched dataframe
    df = report_df

    # Write artifacts
    artifacts: List[Path] = []

    # ---- PLOT GENERATION ----
    # Now you can use report_df to generate the plots from the screenshots.
    # This is where you would use libraries like seaborn and matplotlib.
    # For example, to create the LPIPS distribution boxplots:
    #
    # import seaborn as sns
    # import matplotlib.pyplot as plt
    #
    # if 'category_l2' in report_df.columns:
    #     g = sns.catplot(
    #         data=report_df,
    #         x='category_l2', y='lpips', hue='algo',
    #         col='n_images', kind='box', col_wrap=2
    #     )
    #     plot_path = out_dir / "lpips_distribution.png"
    #     g.savefig(plot_path)
    #     artifacts.append(plot_path)

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
    if not report_df.empty:
        failed_subset = report_df[report_df["status"] == "failed"]
        # UPDATED: Add 'variant' to the list of columns
        failure_cols = [
            "product_id", "variant", "product_name", "category_l1", "category_l2", "category_l3", 
            "algo", "img_suffixes", "error_msg", "started_at", "finished_at"
        ]
        cols_to_select = [col for col in failure_cols if col in failed_subset.columns]
        failures_df = failed_subset[cols_to_select].copy()

    # outputs index
    outputs_df = pd.DataFrame()
    if not report_df.empty:
        completed_subset = report_df[report_df["status"] == "completed"]
        # UPDATED: Add 'variant' to the list of columns
        output_cols = [
            "product_id", "variant", "product_name", "category_l1", "category_l2", "category_l3", 
            "algo", "img_suffixes", "output_glb_relpath"
        ]
        cols_to_select = [col for col in output_cols if col in completed_subset.columns]
        outputs_df = completed_subset[cols_to_select].copy()

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
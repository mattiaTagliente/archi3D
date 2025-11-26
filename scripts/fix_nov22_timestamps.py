#!/usr/bin/env python
"""
One-time fix script to restore correct timestamps for Nov 22 jobs.

The worker log contains the true completion times and durations.
This script reconstructs generation_start, generation_end, and generation_duration_s
from the worker log entries.

Also:
- Clears error_msg for completed jobs
- Fills missing prices from adapters.yaml
"""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml


def load_adapters_config() -> dict:
    """Load adapters.yaml configuration."""
    config_path = Path(__file__).parent.parent / "src" / "archi3d" / "config" / "adapters.yaml"
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def parse_worker_log(log_path: Path) -> dict[str, dict]:
    """
    Parse worker.log and extract job completion data.

    Returns:
        Dict mapping job_id -> {end_time, duration_s}
    """
    completions = {}

    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            # Parse timestamp and JSON
            match = re.match(r"^(\S+)\s+(.*)$", line.strip())
            if not match:
                continue

            timestamp_str, json_str = match.groups()

            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            if data.get("event") == "job_completed":
                job_id = data.get("job_id")
                duration_s = data.get("duration_s", 0)

                # Parse end time from log timestamp
                end_time = datetime.fromisoformat(timestamp_str)

                completions[job_id] = {
                    "end_time": end_time,
                    "duration_s": duration_s,
                }

    return completions


def fix_generations_csv(csv_path: Path, log_path: Path, run_id: str, dry_run: bool = True):
    """
    Fix generations.csv with correct timestamps from worker log.

    Args:
        csv_path: Path to generations.csv
        log_path: Path to worker.log
        run_id: Run ID to fix
        dry_run: If True, print changes but don't write
    """
    # Load adapters config for prices
    adapters_cfg = load_adapters_config()
    adapters = adapters_cfg.get("adapters", {})

    # Parse worker log
    completions = parse_worker_log(log_path)
    print(f"Found {len(completions)} job completions in worker log")

    # Read CSV
    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"product_id": str, "variant": str})
    print(f"Read {len(df)} rows from CSV")

    # Filter to target run
    mask = df["run_id"] == run_id
    print(f"Found {mask.sum()} rows for run_id={run_id}")

    timestamp_fixes = []
    price_fixes = []

    for idx, row in df[mask].iterrows():
        job_id = row["job_id"]
        status = row["status"]
        algo = row["algo"]

        # Check if we have log data for this job
        if job_id in completions:
            log_data = completions[job_id]
            end_time = log_data["end_time"]
            duration_s = log_data["duration_s"]
            start_time = end_time - timedelta(seconds=duration_s)

            old_duration = row.get("generation_duration_s", 0)

            # Only fix if duration is wrong (< 1 second means it was from consolidate)
            if old_duration < 1.0:
                df.at[idx, "generation_start"] = start_time.isoformat()
                df.at[idx, "generation_end"] = end_time.isoformat()
                df.at[idx, "generation_duration_s"] = duration_s

                timestamp_fixes.append({
                    "job_id": job_id,
                    "old_duration": old_duration,
                    "new_duration": duration_s,
                })

        # Fill missing prices from adapters.yaml
        if status == "completed" and (pd.isna(row.get("unit_price_usd")) or row.get("unit_price_usd") == 0):
            if algo in adapters:
                algo_cfg = adapters[algo]
                unit_price = float(algo_cfg.get("unit_price_usd", 0.0))
                price_source = algo_cfg.get("price_source", "adapters.yaml")

                if unit_price > 0:
                    df.at[idx, "unit_price_usd"] = unit_price
                    df.at[idx, "price_source"] = price_source

                    price_fixes.append({
                        "job_id": job_id,
                        "algo": algo,
                        "price": unit_price,
                    })

        # Clear error_msg for completed jobs
        if status == "completed" and pd.notna(row.get("error_msg")) and row["error_msg"]:
            if not dry_run:
                df.at[idx, "error_msg"] = ""
            print(f"  Clearing error_msg for completed job {job_id}")

    print(f"\nTimestamp fixes ({len(timestamp_fixes)} jobs):")
    for fix in timestamp_fixes:
        print(f"  {fix['job_id']}: {fix['old_duration']:.6f}s -> {fix['new_duration']:.2f}s")

    print(f"\nPrice fixes ({len(price_fixes)} jobs):")
    for fix in price_fixes:
        print(f"  {fix['job_id']} ({fix['algo']}): ${fix['price']:.3f}")

    if dry_run:
        print("\n[DRY RUN] No changes written. Run with --apply to write changes.")
    else:
        # Write back
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"\nWrote {len(df)} rows to {csv_path}")


if __name__ == "__main__":
    import sys

    # Configuration
    workspace = Path("C:/Users/matti/testing")
    csv_path = workspace / "tables" / "generations.csv"
    log_path = workspace / "logs" / "worker.log"
    run_id = "2025-11-22T21-25-08Z"

    dry_run = "--apply" not in sys.argv

    fix_generations_csv(csv_path, log_path, run_id, dry_run=dry_run)

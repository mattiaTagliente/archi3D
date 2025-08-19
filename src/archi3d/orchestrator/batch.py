# archi3d/orchestrator/batch.py
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yaml
from filelock import FileLock

from archi3d import __version__
from archi3d.config.paths import PathResolver


@dataclass(frozen=True)
class ManifestRow:
    product_id: str
    algo: str
    image_set_csv: str
    img_suffixes: str
    n_images: int
    gt_fbx_relpath: str
    job_id: str
    reason: str  # empty if queued


# -------------------------------
# Image suffix extraction
# -------------------------------
_SUFFIX_RE = re.compile(r"_([A-Z])(?:\.[^.]+)$", re.IGNORECASE)


def _img_suffixes_from_list(image_files: List[str], max_len: int = 20) -> str:
    letters: List[str] = []
    for s in image_files:
        m = _SUFFIX_RE.search(Path(s).name)
        if m:
            letters.append(m.group(1).upper())
    out = "-".join(letters) if letters else ""
    return out[:max_len]


# -------------------------------
# Image selection policies
# -------------------------------

def _select_for_single(image_files: List[str]) -> Tuple[List[str], Optional[str]]:
    if not image_files:
        return [], "no_images"
    # Prefer *_A, else first
    a = [p for p in image_files if p.endswith("_A.jpg") or p.endswith("_A.jpeg") or p.endswith("_A.png")]
    if a:
        return [a[0]], None
    return [image_files[0]], None


def _select_first_k(image_files: List[str], k: int, min_required: Optional[int] = None) -> Tuple[List[str], Optional[str]]:
    if min_required is None:
        min_required = k
    if len(image_files) < min_required:
        return [], f"insufficient_images(min={min_required})"
    return image_files[:k], None


def _select_min_n_all(image_files: List[str], n_min: int) -> Tuple[List[str], Optional[str]]:
    if len(image_files) < n_min:
        return [], f"insufficient_images(min={n_min})"
    return list(image_files), None


def _select_min_max(image_files: List[str], n_min: int, n_max: int) -> Tuple[List[str], Optional[str]]:
    if len(image_files) < n_min:
        return [], f"insufficient_images(min={n_min})"
    # Deterministic: take first up to n_max
    return image_files[:n_max], None


# Map algorithms to policies
_POLICIES: Dict[str, Tuple[str, Dict]] = {
    # multi-image models
    "trellis_multi_stochastic": ("min_all", {"n_min": 2}),
    "trellis_multi_multidiffusion": ("min_all", {"n_min": 2}),
    "rodin_multi": ("min_all", {"n_min": 2}),
    "tripo3d_v2p5_multi": ("min_max", {"n_min": 2, "n_max": 4}),
    "hunyuan3d_v2_multi": ("first_k", {"k": 3, "min_required": 3}),
    # single-image models
    "trellis_single": ("single", {}),
    "tripoSR_single": ("single", {}),
    "tripo3d_v2p5_single": ("single", {}),
    "hunyuan3d_v2_single": ("single", {}),
    "hunyuan3d_v2p1_single": ("single", {}),
}


def _apply_policy(algo: str, image_files: List[str]) -> Tuple[List[str], Optional[str]]:
    mode, kwargs = _POLICIES.get(algo, ("", {}))
    if mode == "single":
        return _select_for_single(image_files)
    if mode == "first_k":
        return _select_first_k(image_files, **kwargs)
    if mode == "min_all":
        return _select_min_n_all(image_files, **kwargs)
    if mode == "min_max":
        return _select_min_max(image_files, **kwargs)
    return [], f"unknown_algo_policy:{algo}"


# -------------------------------
# Helpers
# -------------------------------

def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_items_csv(paths: PathResolver) -> pd.DataFrame:
    p = paths.items_csv
    if not p.exists():
        raise FileNotFoundError(
            f"items.csv not found at {p}.\n"
            "Run 'archi3d catalog build' first."
        )
    return pd.read_csv(p, dtype=str).fillna("")


def _existing_completed_for_run(paths: PathResolver, run_id: str) -> set[str]:
    p = paths.results_parquet
    if not p.exists():
        return set()
    df = pd.read_parquet(p)
    if df.empty:
        return set()
    subset = df[(df["run_id"] == run_id) & (df["status"] == "completed")]
    return set(subset["job_id"].astype(str).tolist())


def _already_queued(queue_dir: Path, job_id_prefix: str) -> bool:
    """Check for any token file that contains the unique job hash identifier."""
    # The pattern looks for any file ending in '*_h<job_id_prefix>.*.json'
    # to match the readable token name format.
    matches = list(queue_dir.glob(f"*_h{job_id_prefix}.*.json"))
    return len(matches) > 0


def _compose_job_id(algo: str, product_id: str, variant: str, image_csv: str) -> str:
    """Computes a deterministic job ID from its core components."""
    material = f"{algo}|{product_id}|{variant}|{image_csv}|{__version__}"
    return _sha1(material)


def _readable_token_name(product_id: str, algo: str, n_images: int, img_suffixes: str, run_id: str, job_id: str) -> str:
    job8 = job_id[:8]
    core = f"{product_id}_{algo}_N{n_images}"
    if img_suffixes:
        core += f"_{img_suffixes}"
    core += f"_{run_id}_h{job8}"
    return f"{core}.todo.json"


# -------------------------------
# Public API
# -------------------------------

def create_batch(
    run_id: str,
    algorithms: List[str],
    paths: PathResolver,
    only: Optional[str] = None,
) -> Tuple[Path, Dict]:
    """
    Create a run manifest and queue tokens under runs/<run_id>/queue/.
    Enforces per-algorithm image count constraints at batch time.
    Skips jobs already completed for the same (run_id, job_id).
    Returns path to manifest_inputs.csv and a summary dictionary.
    """
    paths.validate_expected_tree()

    # Freeze run config
    run_cfg_path = paths.run_config_path(run_id)
    run_cfg = {
        "run_id": run_id,
        "created_at": _now_iso(),
        "code_version": __version__,
        "algorithms": algorithms,
    }
    run_cfg_path.write_text(yaml.safe_dump(run_cfg, sort_keys=False), encoding="utf-8")

    items = _load_items_csv(paths)
    if only:
        # glob filter on product_id
        items = items[items["product_id"].apply(lambda s: fnmatch.fnmatchcase(s, only))].reset_index(drop=True)

    queue_dir = paths.queue_dir(run_id)
    _ = paths.outputs_dir(run_id)  # ensure exists

    completed_job_ids = _existing_completed_for_run(paths, run_id)

    manifest_rows: List[ManifestRow] = []

    for _, row in items.iterrows():
        product_id = row["product_id"].strip()
        variant = row["variant"].strip()
        gt_rel = row["gt_fbx_relpath"].strip()
        image_files = [p for p in row["image_files"].split(";") if p.strip()]

        for algo in algorithms:
            selected, reason = _apply_policy(algo, image_files)
            img_suffixes = _img_suffixes_from_list(selected)

            image_csv = ",".join(selected)
            job_id = _compose_job_id(algo, product_id, variant, image_csv)
            job_id8 = job_id[:8]

            # Skip reasons precedence
            if reason:
                manifest_rows.append(
                    ManifestRow(product_id, algo, image_csv, img_suffixes, len(selected), gt_rel, job_id, reason)
                )
                continue

            if job_id in completed_job_ids:
                manifest_rows.append(
                    ManifestRow(product_id, algo, image_csv, img_suffixes, len(selected), gt_rel, job_id, "already_completed")
                )
                continue

            if _already_queued(queue_dir, job_id8):
                manifest_rows.append(
                    ManifestRow(product_id, algo, image_csv, img_suffixes, len(selected), gt_rel, job_id, "already_queued")
                )
                continue

            # Create queue token
            token = {
                "job_id": job_id,
                "run_id": run_id,
                "product_id": product_id,
                "variant": variant,
                "algo": algo,
                "image_files": selected,  # relpaths under 'dataset/...'
                "gt_fbx_relpath": gt_rel,  # may be empty
                "queued_at": _now_iso(),
                "code_version": __version__,
            }
            token_name = _readable_token_name(product_id, algo, len(selected), img_suffixes, run_id, job_id)
            (queue_dir / token_name).write_text(json.dumps(token, indent=2), encoding="utf-8")

            manifest_rows.append(
                ManifestRow(product_id, algo, image_csv, img_suffixes, len(selected), gt_rel, job_id, "")
            )

    # write manifest inputs CSV
    manifest_path = paths.manifest_inputs_csv(run_id)
    mdf = pd.DataFrame(
        [r.__dict__ for r in manifest_rows],
        columns=[
            "product_id", "algo", "image_set_csv", "img_suffixes",
            "n_images", "gt_fbx_relpath", "job_id", "reason",
        ],
    ).sort_values(["product_id", "algo"], kind="stable")

    # Also create summary before writing files
    summary = {
        "run_id": run_id,
        "created_at": _now_iso(),
        "code_version": __version__,
        "user_filter": only or "all",
        "algorithms": algorithms,
        "counts": {
            "manifest_rows": int(mdf.shape[0]),
            "enqueued": int((mdf["reason"] == "").sum()),
            "skipped": int((mdf["reason"] != "").sum()),
        },
        # Get counts for each skip reason, excluding empty reasons
        "skip_reasons": mdf[mdf["reason"] != ""]["reason"].value_counts().to_dict(),
    }

    # Use a lock to prevent concurrent writes from multiple users
    lock_path = paths.manifest_lock_path(run_id)
    with FileLock(str(lock_path)):
        # Write the main manifest, overwriting to reflect the latest state
        mdf.to_csv(manifest_path, index=False, encoding="utf-8")

        # Append the summary to a historical log file
        log_path = manifest_path.with_name("batch_creation_log.yaml")
        with log_path.open("a", encoding="utf-8") as f:
            # Separate entries with '---' for valid YAML stream
            f.write("---\n")
            yaml.safe_dump(summary, f, sort_keys=False)

    return manifest_path, summary
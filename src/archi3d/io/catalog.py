# archi3d/io/catalog.py
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd


_SUFFIX_RE = re.compile(r"_(?P<tag>[A-Z])(?:\.[^.]+)$", re.IGNORECASE)
_IMG_EXTS = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class CatalogStats:
    items_total: int
    items_with_gt: int
    items_with_img: int


def _split_folder_name(name: str) -> Tuple[str, str]:
    """
    Folder names may be like:
      - "353425"
      - "335888 - Curved backrest"
    Returns (product_id, variant)
    """
    parts = [p.strip() for p in name.split(" - ", 1)]
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _collect_images(images_dir: Path) -> Tuple[List[Path], List[str]]:
    """
    Return (sorted_image_paths, notes)
    Sorting rule:
      - files with trailing _A/_B/... first by letter A..Z
      - others then by name
    """
    notes: List[str] = []
    if not images_dir.exists():
        notes.append("no_images_dir")
        return [], notes

    imgs = [p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in _IMG_EXTS]
    if not imgs:
        notes.append("no_images")
        return [], notes

    tagged = []
    untagged = []
    for p in imgs:
        m = _SUFFIX_RE.search(p.name)
        if m:
            tag = m.group("tag").upper()
            if len(tag) == 1 and "A" <= tag <= "Z":
                tagged.append((tag, p))
            else:
                untagged.append(p)
        else:
            untagged.append(p)

    tagged.sort(key=lambda t: t[0])  # A..Z
    untagged.sort(key=lambda p: p.name.lower())
    ordered = [p for _, p in tagged] + untagged
    return ordered, notes


def _select_gt(gt_dir: Path) -> Tuple[Path | None, List[str]]:
    """
    Pick a single FBX if present; prefer lexicographically first when multiple.
    """
    notes: List[str] = []
    if not gt_dir.exists():
        notes.append("no_gt_dir")
        return None, notes
    fbxs = sorted([p for p in gt_dir.iterdir() if p.is_file() and p.suffix.lower() == ".fbx"], key=lambda p: p.name.lower())
    if not fbxs:
        notes.append("no_gt")
        return None, notes
    if len(fbxs) > 1:
        notes.append("multiple_gt")
    return fbxs[0], notes


def build_items_csv(dataset_root: Path, out_csv: Path) -> CatalogStats:
    """
    Scan the dataset tree (immediate children folders are products) and write `items.csv`.

    Columns:
      product_id, product_name, variant, n_images,
      image_files (semicolon-separated relpaths under 'dataset/'),
      gt_fbx_relpath (under 'dataset/'), notes

    Relpaths are written with a 'dataset/' prefix so they are portable across machines
    (no absolute paths are ever embedded).
    """
    dataset_root = Path(dataset_root)
    if not dataset_root.exists():
        raise FileNotFoundError(f"Dataset root not found: {dataset_root}")

    rows: List[Dict[str, str]] = []
    issues: List[Dict[str, str]] = []

    # Prefix used to store portable relpaths (workspace-root independent)
    DATASET_PREFIX = Path("dataset")

    product_dirs = sorted([p for p in dataset_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower())

    with_gt = 0
    with_img = 0

    for prod_dir in product_dirs:
        product_id, variant = _split_folder_name(prod_dir.name)

        images_dir = prod_dir / "images"
        gt_dir = prod_dir / "gt"

        image_paths, img_notes = _collect_images(images_dir)
        gt_path, gt_notes = _select_gt(gt_dir)

        notes = list(dict.fromkeys(img_notes + gt_notes))  # unique, preserve order

        if gt_path is not None:
            with_gt += 1
            gt_rel = DATASET_PREFIX / prod_dir.name / "gt" / gt_path.name
        else:
            gt_rel = Path("")

        if image_paths:
            with_img += 1
        image_rels = [DATASET_PREFIX / prod_dir.name / "images" / p.name for p in image_paths]

        row = {
            "product_id": product_id,
            "product_name": "",  # not derivable from FS structure (left intentionally blank)
            "variant": variant,
            "n_images": str(len(image_paths)),
            "image_files": ";".join(str(p.as_posix()) for p in image_rels),
            "gt_fbx_relpath": gt_rel.as_posix(),
            "notes": ",".join(notes) if notes else "",
        }
        rows.append(row)

        if notes:
            issues.append({"product_id": product_id, "variant": variant, "notes": row["notes"]})

    df = pd.DataFrame(rows, columns=[
        "product_id",
        "product_name",
        "variant",
        "n_images",
        "image_files",
        "gt_fbx_relpath",
        "notes",
    ])

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False, encoding="utf-8")

    # Also write an issues list next to items.csv for quick triage
    issues_csv = out_csv.with_name("items_issues.csv")
    pd.DataFrame(issues).to_csv(issues_csv, index=False, encoding="utf-8")

    stats = CatalogStats(
        items_total=len(product_dirs),
        items_with_gt=with_gt,
        items_with_img=with_img,
    )
    return stats

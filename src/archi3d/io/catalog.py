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


def _load_enriched_data(dataset_root: Path) -> pd.DataFrame:
    """Loads and cleans the enriched data from a markdown-like table."""
    # The enriched file is expected to be in the workspace root, next to the dataset/ folder
    enriched_path = dataset_root.parent / "check_enriched.txt"
    if not enriched_path.exists():
        print(f"Warning: Enriched data file not found at {enriched_path}")
        return pd.DataFrame()

    with open(enriched_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Get column headers from the first line, stripping whitespace and slicing to ignore
    # the empty strings created by the leading/trailing pipes.
    header = [h.strip() for h in lines[0].split('|')[1:-1]]

    # Process the data lines, skipping the header (index 0) and the separator (index 1)
    data_rows = []
    for line in lines[2:]:
        if not line.strip():
            continue
        # Split the line and slice it to align with the header
        cells = [cell.strip() for cell in line.split('|')[1:-1]]
        if len(cells) == len(header):
            data_rows.append(cells)

    if not data_rows:
        return pd.DataFrame()

    df = pd.DataFrame(data_rows, columns=header)

    # Clean up column names to match the expected schema
    df = df.rename(columns={
        "Folder Name": "folder_name",
        "ProductID": "product_id",
        "Manufacturer": "manufacturer",
        "Name": "product_name",
        "Description": "description",
        "Views": "views",
        "Category level 1": "category_l1",
        "Category level 2": "category_l2",
        "Category level 3": "category_l3",
    })

    # Ensure product_id is a string for consistent merging
    if 'product_id' in df.columns:
        df['product_id'] = df['product_id'].astype(str)

    # Use the folder_name as the index for easy lookups
    if 'folder_name' in df.columns:
        df = df.set_index("folder_name")

    return df


def build_items_csv(dataset_root: Path, out_csv: Path) -> CatalogStats:
    """
    Scan the dataset tree and write `items.csv`, enriched with metadata.
    """
    dataset_root = Path(dataset_root)
    if not dataset_root.exists():
        raise FileNotFoundError(f"Dataset root not found: {dataset_root}")

    rows: List[Dict[str, str]] = []
    issues: List[Dict[str, str]] = []

    # Prefix used to store portable relpaths (workspace-root independent)
    DATASET_PREFIX = Path("dataset")
    
    # Load the enriched data
    enriched_df = _load_enriched_data(dataset_root)

    product_dirs = sorted([p for p in dataset_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower())

    with_gt = 0
    with_img = 0

    for prod_dir in product_dirs:
        product_id, variant = _split_folder_name(prod_dir.name)

        # ---- ENRICHMENT LOGIC ----
        enriched_data = {}
        if not enriched_df.empty and prod_dir.name in enriched_df.index:
            enriched_row = enriched_df.loc[prod_dir.name].to_dict()
            enriched_data = {
                "product_name": enriched_row.get("product_name", ""),
                "manufacturer": enriched_row.get("manufacturer", ""),
                "description": enriched_row.get("description", ""),
                "category_l1": enriched_row.get("category_l1", ""),
                "category_l2": enriched_row.get("category_l2", ""),
                "category_l3": enriched_row.get("category_l3", ""),
            }
        
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
            "product_name": enriched_data.get("product_name", ""), # Now populated
            "variant": variant,
            "n_images": str(len(image_paths)),
            "image_files": ";".join(str(p.as_posix()) for p in image_rels),
            "gt_fbx_relpath": gt_rel.as_posix(),
            "notes": ",".join(notes) if notes else "",
            # Add other enriched fields as columns
            "manufacturer": enriched_data.get("manufacturer", ""),
            "category_l1": enriched_data.get("category_l1", ""),
            "category_l2": enriched_data.get("category_l2", ""),
            "category_l3": enriched_data.get("category_l3", ""),
        }
        rows.append(row)

        if notes:
            issues.append({"product_id": product_id, "variant": variant, "notes": row["notes"]})

    df = pd.DataFrame(rows, columns=[
        "product_id", "product_name", "variant", "n_images",
        "image_files", "gt_fbx_relpath", "notes", "manufacturer",
        "category_l1", "category_l2", "category_l3"
    ])

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    # Also write an issues list next to items.csv for quick triage
    issues_csv = out_csv.with_name("items_issues.csv")
    pd.DataFrame(issues).to_csv(issues_csv, index=False, encoding="utf-8-sig")

    stats = CatalogStats(
        items_total=len(product_dirs),
        items_with_gt=with_gt,
        items_with_img=with_img,
    )
    return stats
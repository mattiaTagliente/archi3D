## Role

Your task is to implement **Phase 1 — Catalog Build (items.csv)**. Do **not** change behavior of other phases or commands beyond what is explicitly specified here.

## Objective of Phase 1

Implement a robust `archi3d catalog build` that:

1. **Scans the curated dataset folder** as the **primary source of truth** for item existence, images, and local GT assets.
2. **Enriches items** by joining with `products-with-3d.json` (metadata only).
3. **Writes** the canonical **`tables/items.csv`** (SSOT for parent items) and **`tables/items_issues.csv`**, and appends a structured summary to **`logs/catalog_build.log`**.
4. Uses Phase 0 primitives (PathResolver roots/getters + atomic I/O utilities) and stores **workspace-relative paths** only.

### Non-Goals (Out of Scope)

* No changes to `batch create`, `run worker`, `consolidate`, `compute fscore`, or `compute vfscore`.
* No changes to `tables/generations.csv`.
* No network or downloader logic; you operate only on the local curated dataset and local JSON.
* No additional dependencies beyond the repo’s existing ones.

---

## Repository Pointers (what you will modify)

* **CLI**: `src/archi3d/cli.py` — wire the command (args parsing & invocation).
* **Catalog builder**: create `src/archi3d/db/catalog.py` (new) **or** extend `src/archi3d/io/catalog.py` if already present and appropriate. Prefer a dedicated module under `db/` for SSOT responsibilities.
* **Config/paths**: use PathResolver getters from Phase 0 (no changes here).
* **I/O**: use Phase 0 helpers in `src/archi3d/utils/io.py` (`update_csv_atomic`, `append_log_record`, etc.). Do **not** duplicate atomic logic.

Keep changes **localized**, **backward-compatible**, and **idempotent** (safe to rerun).

---

## Functional Requirements

### A) CLI Behavior

Add/confirm a subcommand:

```
archi3d catalog build [--dataset <path>] [--products-json <path>]
```

* **Defaults** (if flags not provided):

  * `dataset` → `${workspace}/dataset`
  * `products-json` → `${workspace}/products-with-3d.json`
* **Auto-discovery** (only if defaults missing and flags not given):

  * Try finding `products-with-3d.json` one level up from workspace.
* **Validation**:

  * If `dataset` dir missing → fail with clear message.
  * If `products-json` missing → continue **without enrichment** and log a warning; still emit `items.csv` from pure scan.

### B) Dataset Scan Rules (primary SoT)

**Folder naming**
Each item is a directory named either:

* `ProductId` **or**
* `ProductId - variant`

Regex: `^(?P<pid>\d+)(?:\s*-\s*(?P<variant>.+))?$`
If `variant` is absent → set `"default"`.

**Images selection (max 6)**

* Allowed extensions (case-insensitive): `.jpg`, `.jpeg`, `.png`.
* Recognize suffix tags `_A .. _F` before the extension (e.g., `foo_A.jpg`) and **order**: tagged `A..F` first, then **untagged** images lexicographically.
* Select **up to 6** images in that order.
* **Issues**:

  * `<1` image → issue `no_images`.
  * `>6` images → issue `too_many_images` (keep first 6, record count).

**GT object selection**

* Prefer a **single** GT file by extension **order**: `.glb` then `.fbx`.
* If multiple candidates in the item folder: take lexicographically first by preferred extension; **issue** `multiple_gt_candidates`.
* If none found: **issue** `missing_gt`.

**Path normalization**

* All stored paths must be **relative to workspace** via `PathResolver.rel_to_workspace(...)`.
* Store image paths in fixed columns (see schema) even if fewer than 6; leave missing as empty strings.

### C) JSON Enrichment (secondary source)

If `products-with-3d.json` is available:

* Build a fast lookup map keyed by `product_id` (string).

* For each `product_id`, extract:

  | SSOT field        | JSON candidates (first non-empty wins)                                                                                                                                                                                                                  |
  | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `manufacturer`    | `Manufacturer.Name` || `Manufacturer` (string)                                                                                                                                                                                                          |
  | `product_name`    | `Name.Value.it` || `Name.Value.en` || `Name.it` || `Name.en` || `Name`                                                                                                                                                                                  |
  | `description`     | `ShortDescription.Value.it` || `ShortDescription.Value.en` || `ShortDescription.it` || `ShortDescription.en` || `Description`                                                                                                                           |
  | `category_l1..l3` | From `Categories` array: for each category, prefer `.Name.it` else `.Name.en`. Choose the **deepest path** available and take its first three levels as `l1/l2/l3`. If multiple categories tie, pick the first deterministically (stable sort by name). |

* **Issues** for missing enrichment fields (do **not** fail the build):

  * `missing_manufacturer`, `missing_product_name`, `missing_description`, `missing_categories`.

### D) CSV Schemas (outputs)

**1) `tables/items.csv`** — **SSOT (parents)**
Primary key: **(`product_id`, `variant`)**

Columns (in this order):

1. `product_id` (str)
2. `variant` (str, default `"default"`)
3. `manufacturer` (str)
4. `product_name` (str)
5. `category_l1` (str)
6. `category_l2` (str)
7. `category_l3` (str)
8. `description` (str)
9. `n_images` (int)
10. `image_1_path` … `image_6_path` (str, workspace-relative; empty if not used)
11. `gt_object_path` (str, workspace-relative; empty if missing)
12. `dataset_dir` (str, workspace-relative path of the item directory)
13. `build_time` (ISO8601 UTC)
14. `source_json_present` (`true|false`)

**2) `tables/items_issues.csv`** — per-issue rows
Columns:

* `product_id`, `variant`, `issue` (enum), `detail` (free text or structured hints)

**CSV writing**

* Use `update_csv_atomic(...)` from Phase 0 (UTF-8-SIG).
* `items.csv` is **replaced** by a fresh build (full set), not incremental; implement as: write to temp DF then `write_text_atomic` (for full rebuild), or simply write via pandas (atomic+lock). `items_issues.csv` is rebuilt accordingly.
* All logging via `append_log_record(...)`.

### E) Logging & Summary

Append to `logs/catalog_build.log` a structured summary:

```json
{
  "event": "catalog_build",
  "timestamp": "...",
  "dataset": "<rel_path>",
  "products_json": "<rel_path or null>",
  "items_total": <int>,
  "items_with_img": <int>,
  "items_with_gt": <int>,
  "no_images_count": <int>,
  "too_many_images_count": <int>,
  "missing_meta_counts": {
    "missing_manufacturer": <int>,
    "missing_product_name": <int>,
    "missing_description": <int>,
    "missing_categories": <int>
  },
  "multi_gt_candidates": <int>
}
```

Print a short human message to console with totals and the location of the two CSVs.

### F) Performance & Robustness

* Handle **thousands** of items efficiently: single pass scan; avoid loading the entire JSON into pandas; use a dict keyed by `product_id`.
* Image and GT extension checks must be **case-insensitive**; ignore hidden files.
* Windows & POSIX paths: always store normalized **POSIX-like** separators in CSV (optional but recommended).
* The operation is **idempotent**; re-running yields the same `items.csv` given the same inputs.

### G) Concurrency

* Build typically runs as a single process, but all writes must remain **atomic**:

  * Use Phase 0 `FileLock` + temp-rename.
  * No partial results left behind on exceptions.

---

## Acceptance Criteria (Definition of Done)

1. **CLI & discovery**

   * `archi3d catalog build` accepts optional `--dataset` and `--products-json`.
   * If `products-with-3d.json` is missing, the build still succeeds (with enrichment warnings).

2. **Outputs**

   * `tables/items.csv` is produced with **all columns** in the specified order.
   * `tables/items_issues.csv` lists every detected issue (possibly empty).
   * `logs/catalog_build.log` receives a JSON summary line with correct counters.

3. **Correctness**

   * Folder name parsing supports both `ProductId` and `ProductId - variant`.
   * Image selection respects `_A.._F` ordering, then untagged, capped at 6.
   * GT selection prefers `.glb`, fallback `.fbx`, with multi-candidate warning.
   * All stored paths are **workspace-relative**.

4. **Stability**

   * Atomic writes (no temp leftovers), UTF-8-SIG encoding.
   * Re-running with unchanged inputs reproduces byte-identical CSVs (modulo `build_time`).

---

## Minimal Tests

Create `tests/test_phase1_catalog_build.py` (or `scripts/dev/phase1_selftest.py` if pytest isn’t configured).

**Test 1 — Basic build (no JSON)**

* Temp workspace with `dataset/1001/` containing 2 images and 1 `.glb`.
* Run build without `products-with-3d.json`.
* Assert `items.csv` has one row; `n_images=2`; `gt_object_path` set; `source_json_present=false`.
* `items_issues.csv` empty; log summary counters correct.

**Test 2 — Image selection with tags & cap**

* `dataset/2002 - v1/` with files: `a_A.jpg, a_B.jpg, a_C.jpg, x.jpg, y.jpg, z.jpg, w.jpg`.
* Assert chosen 6 are `A..F` (up to C) then `x,y,z` (lexicographic), `w.jpg` excluded; issue `too_many_images`.

**Test 3 — GT preference & multiple candidates**

* `dataset/3003/` with `model.fbx` and `model.glb`.
* Assert `.glb` chosen; `multiple_gt_candidates` counted once.

**Test 4 — JSON enrichment**

* Provide a minimal `products-with-3d.json` for `3003` with localized names/descriptions and categories.
* Assert `manufacturer/product_name/description/category_l1..l3` populated from IT, fallback EN if IT missing.
* Missing fields produce `missing_*` issues.

**Test 5 — Workspace-relative & atomic**

* Ensure output paths do not start with drive letters or `/`.
* Simulate a concurrent read (open CSV while building) — build still succeeds; no `.tmp` remains.

---

## Deliverables

* Modified/new files:

  * `src/archi3d/cli.py` (wire command and args)
  * `src/archi3d/db/catalog.py` (or extend `src/archi3d/io/catalog.py`) — main implementation
* Tests or self-test script as specified
* CHANGELOG entry:

  * **feat(phase1):** implement catalog build from curated dataset + JSON enrichment; emit items.csv, items_issues.csv, and structured build log; atomic writes and workspace-relative paths.

---

## Implementation Notes

* Reuse Phase 0: `PathResolver.tables_root`, `.items_csv_path()`, `.items_issues_csv_path()`, `.catalog_build_log_path()`, and `ensure_mutable_tree()`.
* Use `update_csv_atomic` only if you ever choose to *incrementally* merge; for Phase 1 a **full rebuild** is simpler and acceptable. If you choose full rebuild, still use atomic temp-rename.
* For suffix tag detection use a compiled regex: `r"_(?P<tag>[A-F])(?:\.[^.]+)$"`, `re.IGNORECASE`.
* Normalize category derivation deterministically to avoid flapping between runs.
* Prefer `datetime.now(timezone.utc).isoformat()` for `build_time`.
* Keep console output concise; defer details to CSVs and the JSON summary log.

---

## 📝 IMPORTANT: Update Documentation

**After completing this phase, you MUST update the project documentation:**

1. Update `CLAUDE.md` with:
   - New functionality added in this phase
   - Usage examples and patterns
   - Any new constraints or design patterns
   - Update the "Implementation Status" section with phase completion details

2. Keep documentation comprehensive and consolidated (avoid creating many small files)

3. The user prefers documentation that retains all information in a few comprehensive files

**This is a critical step - do not consider the phase complete until documentation is updated!**

---
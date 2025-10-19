## Role

Your task is to implement **Phase 0 — Conventions & Layout (SSOT scaffolding)**. Do **not** change behavior of other phases or commands beyond what is explicitly specified here.

## Objective of Phase 0

Establish a **single, canonical workspace layout** and provide safe, reusable I/O primitives for **atomic CSV/log writes** that all later phases will use. Specifically:

1. Define/standardize the **workspace subtree** for mutable artifacts:

   * `tables/` — persistent Single Source of Truth (SSOT) CSVs
   * `runs/` — per-run manifests and intermediates
   * `reports/` — generated reports/exports
   * `logs/` — cumulative logs for steps (catalog, batch, worker, metrics)

2. Expose **stable path accessors** (in `PathResolver`) for the above, plus concrete file paths for upcoming CSVs/logs:

   * `tables/items.csv`, `tables/items_issues.csv`, `tables/generations.csv`
   * `logs/catalog_build.log`, `logs/batch_create.log`, `logs/worker.log`, `logs/metrics.log`

3. Provide **atomic, locked I/O utilities** to write/update CSVs and append logs safely:

   * Atomic write via temp file + rename
   * File locking to avoid concurrent corruption
   * An **upsert** helper for CSVs keyed by specified columns

### Non-Goals (Out of Scope)

* No scanning of datasets, no joining with JSON, no batch/worker logic, no metric computation.
* Do not add/modify CLI commands’ behavior (you may add private utilities used later).
* Do not introduce new external dependencies beyond those already in the repo.

---

## Repository Pointers (what you will modify)

* `src/archi3d/config/paths.py` — extend `PathResolver` with stable directories & file paths and an “ensure” method.
* `src/archi3d/utils/io.py` — add atomic CSV/log write utilities (if the file exists; otherwise create it under `utils/`).
* (Optional minimal stubs) `src/archi3d/db/` is **not** required for Phase 0. Do not add functional modules there yet.

Keep changes **localized** and backward-compatible.

---

## Functional Requirements

### A) Workspace Tree & Paths

1. **PathResolver extensions**

   * Add properties (or methods) for the canonical directories under the current workspace:

     * `tables_root: Path` → `${workspace}/tables`
     * `runs_root: Path` → `${workspace}/runs`
     * `reports_root: Path` → `${workspace}/reports`
     * `logs_root: Path` → `${workspace}/logs`
   * Add **file path getters**:

     * `items_csv_path()` → `${tables_root}/items.csv`
     * `items_issues_csv_path()` → `${tables_root}/items_issues.csv`
     * `generations_csv_path()` → `${tables_root}/generations.csv`
     * `catalog_build_log_path()` → `${logs_root}/catalog_build.log`
     * `batch_create_log_path()` → `${logs_root}/batch_create.log`
     * `worker_log_path()` → `${logs_root}/worker.log`
     * `metrics_log_path()` → `${logs_root}/metrics.log`
   * Ensure there is a utility:

     * `rel_to_workspace(path: Path) -> Path` (return a path **relative** to the workspace; keep or implement if missing).
   * Implement `ensure_mutable_tree()` to **create** (mkdir `parents=True, exist_ok=True`) the mutable subtrees: `tables/`, `runs/`, `reports/`, `logs/`.

     > **Do not** create or modify the dataset directory here. Validation of read-only parts (e.g., dataset) must remain as it is today.

2. **Validation behavior**

   * Keep any existing `validate_expected_tree()` behavior intact for read-only parts.
   * Do not enforce presence of CSV files yet; just ensure directories exist when `ensure_mutable_tree()` is called.

### B) Atomic I/O Utilities

Create or extend `src/archi3d/utils/io.py` with the following utilities. They must be **cross-platform** and **safe** under concurrent access:

1. **`write_text_atomic(path: Path, text: str) -> None`**

   * Write to `path` atomically:

     * Create `path.parent` if needed.
     * Write to `path.with_suffix(path.suffix + ".tmp")` (unique temp name is fine).
     * `flush` + `fsync` file descriptor.
     * `os.replace(tmp, path)` (atomic rename on POSIX/Windows).

2. **`append_log_record(path: Path, record: str | dict) -> None`**

   * If `record` is `dict`, serialize as one-line JSON.
   * Prefix each line with ISO8601 timestamp.
   * Use a `FileLock` (same directory) to **serialize appenders**.
   * Create parent directories if needed. Use UTF-8.

3. **`update_csv_atomic(path: Path, df_new: pd.DataFrame, key_cols: list[str]) -> tuple[int, int]`**

   * Purpose: **upsert** rows into a CSV table using `key_cols` as the unique key.
   * Behavior:

     * Create parent directories if needed.
     * If `path` does not exist → write `df_new` with header (`utf-8-sig`) and return `(inserted=len(df_new), updated=0)`.
     * Else read existing CSV (UTF-8, no index), **merge**:

       * Validate that all `key_cols` exist in both frames (raise `ValueError` if not).
       * Deduplicate `df_new` on `key_cols` keeping **last occurrence**.
       * Left join/update semantics: for each key in `df_new`, replace or insert the row into existing.
     * Write atomically via temp+rename (`utf-8-sig`), preserving column order:

       * Column order: union of existing columns followed by any **new** columns appended in order of appearance in `df_new`.
     * Return `(inserted_count, updated_count)` for telemetry.
   * Concurrency:

     * Use a `FileLock` on `path` (e.g., sibling `path.with_suffix(".lock")`).
     * Perform read-merge-write under the lock.

4. **Encoding & CSV dialect**

   * Always use `encoding="utf-8-sig"` for CSV to play well with Excel.
   * Use default pandas CSV dialect (comma-separated, quoted as needed).

### C) Coding Standards & Compatibility

* Python 3.11, type hints everywhere, clear docstrings.
* No new third-party dependencies (use `filelock`, `pandas`, etc., already present).
* Avoid breaking public APIs; your new functions are internal utilities to be used by later phases.
* All file paths returned by `PathResolver` **must be relative to the configured workspace** and created only when needed.

---

## Acceptance Criteria (Definition of Done)

1. **PathResolver**

   * Provides the four roots (`tables_root`, `runs_root`, `reports_root`, `logs_root`) and file path getters listed above.
   * `ensure_mutable_tree()` creates the four directories if missing.
   * `rel_to_workspace()` returns a correct relative path for any absolute path within workspace.

2. **I/O Utilities**

   * `write_text_atomic()` writes atomically (verified by checking that incomplete temp files are not left behind).
   * `append_log_record()` appends lines with ISO8601 timestamps; accepts both `str` and `dict`.
   * `update_csv_atomic()` correctly upserts:

     * Writing initial CSV,
     * Inserting new keys,
     * Updating existing keys,
     * Preserving existing columns, appending new columns,
     * Returning accurate `(inserted, updated)`.

3. **No behavioral regressions**

   * Existing imports/uses of `PathResolver` still work.
   * No CLI behavior changes in this phase.

---

## Minimal Tests (you may add under `tests/`)

Create `tests/test_phase0_paths_and_io.py` (or similar) with **self-contained** tests (no external network/files). Use a temporary directory as workspace.

* **Test 1 — ensure_mutable_tree**

  * Instantiate `PathResolver` with a temp workspace.
  * Call `ensure_mutable_tree()`.
  * Assert existence of `tables/`, `runs/`, `reports/`, `logs/`.

* **Test 2 — write_text_atomic**

  * Write text to a file twice; assert content equals last write; assert no `.tmp` files remain.

* **Test 3 — append_log_record**

  * Append a string, then a dict; read file; assert both lines exist and start with an ISO8601 timestamp.

* **Test 4 — update_csv_atomic (insert + update + new columns)**

  * Upsert initial DF with keys `["k1","k2"]`.
  * Upsert second DF that updates one row and inserts another, plus adds a new column.
  * Read back and assert row counts, updated values, and presence of new column at the tail of columns.

> If the repo does not currently run `pytest`, you may keep tests as a script under `scripts/dev/phase0_selftest.py` printing PASS/FAIL with exit codes.

---

## Deliverables

* Modified files:

  * `src/archi3d/config/paths.py` (add roots, getters, `ensure_mutable_tree`, keep existing APIs intact)
  * `src/archi3d/utils/io.py` (add `write_text_atomic`, `append_log_record`, `update_csv_atomic`)
* (Optional) `tests/test_phase0_paths_and_io.py` or `scripts/dev/phase0_selftest.py`
* A concise `CHANGELOG` entry or commit message:

  * **feat(phase0):** add SSOT workspace layout (tables/runs/reports/logs) and atomic CSV/log I/O utilities; extend PathResolver with stable paths and ensure function; no behavior change to CLI.

---

## Implementation Notes

* Use `from filelock import FileLock`.
* For timestamps, prefer `datetime.now(timezone.utc).isoformat()` or `%Y-%m-%dT%H:%M:%SZ` (UTC).
* For temp files, use `path.with_suffix(path.suffix + ".tmp")` and ensure cleanup on exceptions.
* For column union in upsert: keep original column order stable to minimize diffs in VCS.

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
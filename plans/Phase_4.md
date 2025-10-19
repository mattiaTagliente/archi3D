## Role

Implement **Phase 4 — Consolidate**, adding a robust `archi3d consolidate` command that reconciles a run’s artifacts and state markers with the SSOT **`tables/generations.csv`**, ensuring consistency, deduplication, and high-quality metadata. **Do not** change Phases 0–3 beyond what is explicitly required here.

**Context already implemented and available:**

* **Phase 0**: SSOT layout + `PathResolver` (roots/getters), `ensure_mutable_tree()`, and atomic CSV/log utilities (`update_csv_atomic`, `append_log_record`, etc.). 
* **Phase 1**: `archi3d catalog build` → `tables/items.csv` + `tables/items_issues.csv`. 
* **Phase 2**: `archi3d batch create` → initializes **`tables/generations.csv`** with `status=enqueued` and writes `runs/<run_id>/manifest.csv`. **`generations.csv` is the Single Source of Truth.** 
* **Phase 3**: `archi3d run worker` → executes jobs, writes artifacts under `runs/<run_id>/outputs/<job_id>/...`, maintains state markers in `runs/<run_id>/state/`, and **upserts** job results/status into **`tables/generations.csv`** (`running → completed|failed`). 

---

## Objective of Phase 4

Create a **reconciliation pass** that:

1. Reads **`runs/<run_id>/manifest.csv`**, **`runs/<run_id>/state/`**, **`runs/<run_id>/outputs/`**, and the SSOT **`tables/generations.csv`**.
2. **Validates & fixes** inconsistencies between CSV and on-disk markers/artifacts (e.g., completed jobs lacking `.completed` marker or missing `gen_object_path`, stale `running` jobs with no heartbeat).
3. **Deduplicates/merges** any duplicated `(run_id, job_id)` rows by keeping the most complete information.
4. **Upserts** corrected rows back into **`tables/generations.csv`** using Phase-0 atomic utilities.
5. Emits a **structured summary** into **`logs/metrics.log`** (event: `"consolidate"`) with counts and conflict details.

**Non-Goals:** No job execution, no (re)manifest build, no FScore/VFScore computation.

---

## Repository Pointers (where to work)

* **CLI:** `src/archi3d/cli.py` — wire `consolidate` subcommand and arguments.
* **Consolidation logic (new):** `src/archi3d/orchestrator/consolidate.py` — implement core reconcilation.
* **I/O & paths:** Reuse Phase-0 `PathResolver` getters (`runs_root`, `tables_root`, `metrics_log_path()`, etc.) and I/O helpers (`update_csv_atomic`, `append_log_record`). **Do not** re-implement atomic I/O. 

---

## Functional Requirements

### A) CLI Behavior

Add subcommand:

```
archi3d consolidate
  --run-id <string>           (required)
  [--dry-run]                 (default: false)
  [--strict]                  (default: false; if true, exit non-zero on any conflict)
  [--only-status <csv-list>]  (default: all; e.g., completed,failed,running,enqueued)
  [--fix-status]              (default: true; apply on-disk → CSV corrections)
  [--max-rows <int>]          (optional cap for safety; default: unlimited)
```

* Operates **only** on rows for `run_id`.
* `--dry-run` computes everything but **does not write** `generations.csv`; still logs a preview summary.
* `--only-status` restricts which jobs are considered (useful for partial consolidations).
* `--strict` makes any unresolved conflict a hard error.

### B) Inputs

* `tables/generations.csv` (SSOT).
* `runs/<run_id>/manifest.csv`.
* `runs/<run_id>/state/` markers:

  * `<job_id>.inprogress` (optional heartbeat info),
  * `<job_id>.completed`,
  * `<job_id>.failed`,
  * `<job_id>.error.txt` (free-text error detail).
* `runs/<run_id>/outputs/<job_id>/` artifacts:

  * `generated.glb` (required for completed),
  * `preview_*.png` (0..3),
  * optional `metadata.json`.

### C) Reconciliation Rules

Apply in this order per job (filtered by `--only-status`):

1. **Gather evidence**

   * CSV row (if present).
   * Presence of marker files and their timestamps.
   * Existence/size of `generated.glb` and previews.
   * Last heartbeat time from `.inprogress` (if you write it in Phase 3; if not present, skip heartbeat checks).

2. **Status truth table**

   * If `.completed` exists **and** `generated.glb` exists (non-empty) ⇒ desired status = `completed`.
   * Else if `.failed` exists ⇒ desired status = `failed`.
   * Else if `.inprogress` exists **and** heartbeat fresh (e.g., <10 min) ⇒ desired status = `running`.
   * Else if manifest contains job and **no** state markers/artifacts ⇒ desired status = keep CSV status (default `enqueued`).
   * If CSV says `completed` but file missing ⇒ **downgrade to `failed`** with `error_msg="missing generated.glb; downgraded by consolidate"` **only if** `--fix-status` is true.

3. **Timestamps & duration**

   * If `generation_start` or `generation_end` missing:

     * Start: take earliest of marker or outputs creation time (best effort).
     * End: take latest of marker or `generated.glb` mtime.
   * Recompute `generation_duration_s = max(end-start, 0)` if endpoints are both present.

4. **Outputs & paths**

   * Ensure `gen_object_path` points to `runs/<run_id>/outputs/<job_id>/generated.glb` (workspace-relative).
   * Normalize `preview_1..3_path` similarly when files exist.

5. **Worker/error fields**

   * If `error_msg` empty but `state/<job_id>.error.txt` exists, read the first ~2000 chars and set `error_msg="... (truncated; see error.txt)"`.

6. **Conflict resolution (duplicate keys)**

   * If multiple rows for the same `(run_id, job_id)` are found (should be rare), **merge**:

     * Prefer the row with “higher” status precedence: `completed > failed > running > enqueued`.
     * For each column, prefer **non-empty** over empty; for numerics, prefer non-NaN; for paths, prefer existing file paths.
     * Keep the **widest set of non-empty columns** (column-wise max information).
   * Report each merge in the summary as a conflict resolved.

7. **Schema evolution**

   * If new columns appear during consolidation (e.g., `algo_version` now known), they are appended at tail (Phase-0 upsert behavior). 

### D) Upsert Contract

* Write corrections via **`update_csv_atomic(path, df_merged, key_cols=["run_id","job_id"])`**. 
* Ensure **workspace-relative** paths using `PathResolver.rel_to_workspace(...)`.

### E) Logging & Summary

Append a JSON record to **`logs/metrics.log`** using `append_log_record(...)`:

```json
{
  "event": "consolidate",
  "timestamp": "...",
  "run_id": "...",
  "dry_run": false,
  "considered": 123,
  "upsert_inserted": 0,
  "upsert_updated": 120,
  "unchanged": 3,
  "conflicts_resolved": 2,
  "marker_mismatches_fixed": 5,
  "downgraded_missing_output": 1,
  "missing_outputs": 1,
  "invalid_previews": 0,
  "status_histogram_before": {"enqueued": 10, "running": 5, "completed": 100, "failed": 8},
  "status_histogram_after":  {"enqueued": 9,  "running": 2, "completed": 101, "failed": 11}
}
```

* In `--dry-run`, do not write CSV; still emit the **projected** histograms and counts with `"dry_run": true`.
* Keep console concise; the log line is the canonical audit record.

### F) Safety & Idempotency

* Running `consolidate` multiple times must be **idempotent**; after the first write, subsequent runs should yield `upsert_updated≈0` unless files/markers changed.
* All writes are atomic & locked; encoding is `utf-8-sig` for CSV. 
* Never delete markers/artifacts; only read and reconcile.

---

## Acceptance Criteria (Definition of Done)

1. **CLI**

   * `archi3d consolidate --run-id <id>` runs successfully, respecting `--dry-run`, `--strict`, `--only-status`, `--fix-status`.

2. **Reconciliation**

   * Correctly updates statuses from markers/artifacts (e.g., fixes stale/missing fields).
   * Fills in `gen_object_path` and preview paths for completed jobs when present on disk.
   * Recomputes `generation_duration_s` when start/end are fixable.

3. **SSOT Integrity**

   * `tables/generations.csv` contains **one** row per `(run_id,job_id)` after consolidation.
   * All paths are **workspace-relative** and refer to existing files (where applicable).

4. **Logging**

   * Appends a structured JSON summary (event `"consolidate"`) to `logs/metrics.log` with accurate counters.

5. **Idempotency**

   * Re-running without changes results in `upsert_updated=0` and unchanged histograms.

---

## Minimal Tests / Self-Tests

Create `tests/test_phase4_consolidate.py` (or `scripts/dev/phase4_selftest.py`):

**Test 1 — Happy path**

* Prepare a run with 2 completed jobs (markers + `generated.glb`) and 1 failed.
* Run `consolidate`.
* Assert: statuses unchanged, `gen_object_path` filled, `upsert_updated` minimal (0 or just path normalizations).

**Test 2 — Downgrade missing output**

* CSV says `completed` but `generated.glb` missing; markers absent.
* Run with `--fix-status`.
* Assert: status becomes `failed`, `error_msg` set, summary shows `downgraded_missing_output=1`.

**Test 3 — Merge duplicates**

* Duplicate `(run_id,job_id)` rows: one has `completed` with outputs; the other `running` with worker fields.
* Run `consolidate`.
* Assert: single merged row kept with union of non-empty fields; `conflicts_resolved=1`.

**Test 4 — Heartbeat stale → not “running”**

* Row says `running`, `.inprogress` exists but timestamp is >10 min old and no outputs.
* With default `--fix-status`, keep it `running` **only** if heartbeat fresh; otherwise leave as is (documented behavior) or lower to `enqueued` if you decide to implement that policy (pick one and test accordingly).

**Test 5 — Dry run**

* Run with `--dry-run`; assert: no CSV writes; log includes `"dry_run": true` and the projected histograms.

---

## Implementation Notes

* Use `PathResolver`:

  * `generations_csv_path()`, `runs_root`, `metrics_log_path()`, and helpers like `rel_to_workspace()`. 
* Use **Phase-0** atomic I/O: `update_csv_atomic` for SSOT upserts; `append_log_record` for summary logging. 
* Timestamps: `datetime.now(timezone.utc).isoformat()`.
* File times: `Path.stat().st_mtime` → convert to UTC ISO8601 when synthesizing start/end (best effort).
* Keep column order stable: existing columns first; any newly introduced columns appended at the tail (inherited behavior from `update_csv_atomic`). 

---

## Deliverables

* New: `src/archi3d/orchestrator/consolidate.py`
* Modified: `src/archi3d/cli.py` (register/wire `consolidate`)
* Optional: `tests/test_phase4_consolidate.py` or `scripts/dev/phase4_selftest.py`
* CHANGELOG: **feat(phase4):** add consolidate command to reconcile SSOT with run artifacts/markers; idempotent updates, atomic upserts, structured summary logging.

---

By completing Phase 4 as specified, the pipeline will maintain a **clean, reconciled SSOT** in `tables/generations.csv`, ready for subsequent metric enrichment (FScore/VFScore) without ambiguity or duplication.

---
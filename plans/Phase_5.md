## Role

Implement **Phase 5 — Compute FScore (Geometry Metrics)**. Your task is to add a robust `archi3d compute fscore` command that computes **geometry similarity metrics** (F-score, precision, recall, Chamfer-L2, distance statistics, and alignment transforms) for eligible jobs and **upserts** the results into the SSOT `tables/generations.csv`. **Do not** change phases 0–4 beyond what is explicitly required here. Phases 0–4 are considered done and available.

---

## Objective of Phase 5

1. For each **eligible job** (see selection rules below), run the FScore evaluator on the pair *(GT object, generated object)*.
2. Persist **per-job artifacts** under the run directory and **upsert** standardized metric columns into **`tables/generations.csv`** (the SSOT).
3. Append a **structured summary** to `logs/metrics.log`.

**Non-Goals:** No VFScore here (that will be Phase 6). No job execution or output generation (Phase 3). No consolidation logic (Phase 4). No changes to `tables/items.csv`.

---

## Repository Pointers (where to work)

* **CLI:** `src/archi3d/cli.py` — register and wire `compute fscore` subcommand and args.
* **FScore runner (new):** `src/archi3d/metrics/fscore.py` — core logic to select jobs, invoke evaluator, parse results, and upsert SSOT.
* **SSOT I/O utilities:** reuse Phase-0 `PathResolver` and atomic CSV/log helpers (`update_csv_atomic`, `append_log_record`, etc.). **Do not** reimplement I/O. 

You may create a tiny **adapter layer** in `metrics/fscore_adapter.py` to isolate the call to the external FScore implementation (import or CLI).

---

## Functional Requirements

### A) CLI Behavior

Add subcommand:

```
archi3d compute fscore
  --run-id <string>                 (required)
  [--jobs <glob-or-regex>]          (filter by job_id; contains/regex/glob acceptable)
  [--only-status <csv-list>]        (default: completed; e.g., completed,failed)
  [--with-gt-only]                  (default: true; require non-empty gt_object_path)
  [--redo]                          (default: false; recompute even if metrics already present)
  [--n-points <int>]                (default: 100000; Poisson disk samples per mesh)
  [--timeout-s <int>]               (optional per-job timeout; if exceeded → error)
  [--max-parallel <int>]            (default: 1)
  [--dry-run]                       (compute selection & logging only; no evaluator calls)
```

* By default, operate on **`status=completed`** rows of this `run_id` in `generations.csv`. (You may allow `failed` with `--only-status` to enable diagnostics, but these will likely skip due to missing outputs.)
* `--with-gt-only` requires `gt_object_path` to be present (recommended).
* `--redo` recomputes even when metric columns are already populated.

### B) Inputs

* **SSOT:** `tables/generations.csv` (Phase 2+). 
* **Run tree:** `runs/<run_id>/outputs/<job_id>/generated.glb` (or adapter-standardized name after Phase 3) and any job metadata you need. 
* **GT path** from the carry-over fields in `generations.csv` (originating from `items.csv`). 
* **Workspace & I/O:** `PathResolver`, atomic CSV/logging utilities from Phase 0. 

### C) Eligibility & Selection Rules

For a row to be processed:

1. `run_id` matches.
2. `status` ∈ `--only-status` (default `completed`).
3. `gen_object_path` exists and is non-empty on disk (relative to workspace).
4. `gt_object_path` is non-empty on disk **iff** `--with-gt-only` (default true).
5. If `--jobs` is given, `job_id` must match filter.
6. If **not** `--redo`, skip rows that already have `fscore_status="ok"` (or `fscore` non-null).

Emit a per-job **skip reason** (in memory + summary log).

### D) Output Directory Layout (per run, metrics)

Under `runs/<run_id>/metrics/fscore/<job_id>/` write:

* `result.json` — canonical machine-readable payload (see **E.2**).
* (Optional) `dist_stats.json`, `mesh_meta.json`, evaluator raw logs (if available).
* Do **not** overwrite previous files unless `--redo` is set.

### E) Evaluator Integration & Expected Payload

#### E.1 Adapter contract

Implement a small adapter so we can swap between importing a Python API or spawning a CLI:

```python
@dataclass
class FScoreRequest:
    gt_path: Path
    cand_path: Path
    n_points: int
    out_dir: Path
    timeout_s: int | None

@dataclass
class FScoreResponse:
    ok: bool
    payload: dict              # canonical result payload (E.2)
    tool_version: str | None
    config_hash: str | None
    runtime_s: float | None
    error: str | None
```

Resolution order:

1. Try **import path** (e.g., `from fscore.evaluator import evaluate_one` or equivalent).
2. Else, try **CLI** (e.g., `python -m fscore ...`) with arguments mapping to the same semantics.
   On failure, return `ok=False` with `error` message.

#### E.2 Canonical `payload` schema (JSON)

The adapter must normalize the evaluator output into this dict (and persist to `result.json`):

```json
{
  "fscore": <float>,              "precision": <float>,     "recall": <float>,
  "chamfer_l2": <float>,
  "n_points": <int>,
  "alignment": {
    "scale": <float>,
    "rotation_quat": {"w":<float>,"x":<float>,"y":<float>,"z":<float>},
    "translation": {"x":<float>,"y":<float>,"z":<float>}
  },
  "dist_stats": { "mean":<float>, "median":<float>, "p95":<float>, "p99":<float>, "max":<float> },
  "mesh_meta": {
    "gt_vertices": <int>, "gt_triangles": <int>,
    "pred_vertices": <int>, "pred_triangles": <int>
  }
}
```

Missing fields from the upstream tool should be filled as `null` in JSON and left blank in CSV columns.

### F) SSOT Upsert (columns to write/update)

Use **Phase-0** `update_csv_atomic(path, df_new, key_cols=["run_id","job_id"])`. All paths stored must be **workspace-relative**; numbers must be real numbers (NaN allowed only if unavoidable). 

**Columns (exact names) to update in `tables/generations.csv`:**

* `fscore` (float)
* `precision` (float)
* `recall` (float)
* `chamfer_l2` (float)
* `fscore_n_points` (int)
* `fscore_scale` (float)
* `fscore_rot_w` (float), `fscore_rot_x` (float), `fscore_rot_y` (float), `fscore_rot_z` (float)
* `fscore_tx` (float), `fscore_ty` (float), `fscore_tz` (float)
* `fscore_dist_mean` (float), `fscore_dist_median` (float), `fscore_dist_p95` (float), `fscore_dist_p99` (float), `fscore_dist_max` (float)
* `fscore_runtime_s` (float)
* `fscore_tool_version` (string)
* `fscore_config_hash` (string)
* `fscore_status` (enum: `ok|error|skipped`)
* `fscore_error` (string; single line, truncated to ~2k chars)

> **Idempotency:** If not `--redo`, do **not** overwrite rows with `fscore_status="ok"`.

### G) Error Handling & Status Rules

Per job:

* If inputs missing/invalid → set `fscore_status="error"`, `fscore_error` with concise reason; leave numeric fields empty.
* If adapter returns `ok=false` → `fscore_status="error"` and propagate `error`.
* On success → `fscore_status="ok"` and fill all available fields.
* Never change job `status` (that belongs to Phase 3/4).

### H) Concurrency & Timeouts

* Support `--max-parallel` via a thread pool (keep simple); guard CSV upserts with Phase-0 locks (already handled by `update_csv_atomic`). 
* If `--timeout-s` is set and evaluator exceeds it, treat as error (`fscore_status="error"`, `fscore_error="timeout"`).

### I) Logging & Summary

Append a structured JSON line to **`logs/metrics.log`** using `append_log_record(...)` (Phase-0):

```json
{
  "event": "compute_fscore",
  "timestamp": "...",
  "run_id": "...",
  "n_selected": <int>,
  "processed": <int>,
  "ok": <int>,
  "error": <int>,
  "skipped": <int>,
  "avg_runtime_s": <float>,
  "n_points": <int>,
  "redo": <bool>,
  "max_parallel": <int>,
  "dry_run": <bool>
}
```

Console output should remain succinct (counts and where results were written). 

### J) Safety, Paths, Encoding

* All CSV writes must be **atomic & locked**; encoding **UTF-8-SIG** (Phase-0 utilities). 
* Persist `result.json` under the run metrics dir; never store absolute paths inside CSV.

---

## Acceptance Criteria (Definition of Done)

1. **CLI**

   * `archi3d compute fscore --run-id <id>` runs and processes only eligible rows by default (`status=completed`, GT present).
   * `--dry-run` performs selection and logs a summary without writing CSV or per-job artifacts.
   * `--redo` recomputes and overwrites previous FScore results for selected jobs.

2. **SSOT Updates**

   * `tables/generations.csv` gains/updates the exact columns listed in **F**, using `update_csv_atomic(..., key_cols=["run_id","job_id"])`.
   * Re-runs without `--redo` do not change already `ok` rows (idempotent behavior).

3. **Artifacts**

   * Per-job `runs/<run_id>/metrics/fscore/<job_id>/result.json` exists for successfully computed jobs and matches the canonical payload schema (E.2).

4. **Logging**

   * `logs/metrics.log` has a JSON summary line with accurate counters.

5. **Robustness**

   * Missing/invalid inputs are reported as `fscore_status="error"` with a meaningful `fscore_error`.
   * Timeouts (if specified) are reported as errors.
   * Works on Windows/POSIX paths; paths stored in CSV are workspace-relative.

---

## Minimal Tests / Self-Tests

Place under `tests/test_phase5_compute_fscore.py` (or `scripts/dev/phase5_selftest.py`):

**Test 1 — Happy path**

* Prepare a workspace with a run having 1 `completed` job, valid `gt_object_path` and `gen_object_path`.
* Run with `--dry-run`: assert selection shows `n_selected=1`, `dry_run=true`, no files written.
* Run real: assert `result.json` exists, CSV upserted with `fscore_status="ok"` and numeric fields populated.

**Test 2 — Missing GT**

* Same job but blank GT path or missing file.
* Assert job counted under `error` with `fscore_status="error"` and reason.

**Test 3 — Idempotency**

* Run twice without `--redo`.
* Assert second run `skipped=1`, no CSV changes to FScore columns.

**Test 4 — Redo**

* Run with `--redo`.
* Assert FScore columns updated (you may tweak adapter to change a value to verify overwrite).

**Test 5 — Concurrency & Timeout**

* With `--max-parallel 2` on 3 synthetic jobs; ensure no race on CSV writes and summary counts add up.
* Simulate long evaluator and run with `--timeout-s 1` → `error` due to timeout.

---

## Deliverables

* **New:** `src/archi3d/metrics/fscore.py` (command implementation) and optionally `src/archi3d/metrics/fscore_adapter.py`.
* **Modified:** `src/archi3d/cli.py` (wire `compute fscore`).
* **Optional tests/self-tests:** `tests/test_phase5_compute_fscore.py` or `scripts/dev/phase5_selftest.py`.
* **CHANGELOG:**
  `feat(phase5): add compute fscore command with per-job artifacts, SSOT upserts, and structured metrics logging; idempotent with redo support.`

---

## Implementation Notes

* Use `PathResolver.generations_csv_path()`, `runs_root`, `metrics_log_path()`; create per-job output dir with `mkdir(parents=True, exist_ok=True)`. 
* Always convert absolute paths to **workspace-relative** before writing CSV (Phase-0 `rel_to_workspace(...)`). 
* Timestamps via `datetime.now(timezone.utc).isoformat()`.
* Keep column order stable; new columns appear at the tail (Phase-0 upsert behavior). 
* If the external FScore tool returns extra details, persist them in `result.json` but only promote the standardized subset into CSV (columns in **F**).
* Do not attempt consolidation fixes here; if outputs are inconsistent with markers/CSV, advise the user to run `archi3d consolidate` first. 

---

By completing Phase 5 as above, we extend the **single source of truth** (`tables/generations.csv`) with **geometry metrics** in a reproducible and idempotent way, leveraging the workspace layout, atomic I/O, and job lifecycle already established in Phases 0–4.

---
## 📝 IMPORTANT: Update Documentation

**After completing this phase, you MUST update the project documentation:**

1. Update `claude.md` (the agent's memory file) with:
   - New functionality added in this phase
   - Usage examples and patterns
   - Any new constraints or design patterns
   - Update the "Implementation Status" section with phase completion details

2. Update `readme.md` (the project's main documentation) with:
   - A summary of the new features.
   - Any changes to the project's usage or setup.

3. Keep documentation comprehensive and consolidated (avoid creating many small files)

4. The user prefers documentation that retains all information in a few comprehensive files

**This is a critical step - do not consider the phase complete until documentation is updated!**

---

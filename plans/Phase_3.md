## Role

Implement **Phase 3 — Run Worker**, wiring a robust `archi3d run worker` that executes the per-run manifest, **updates the SSOT `tables/generations.csv` in place** for each job, and emits structured logs & resumable artifacts. **Do not** modify behaviors of Phases 0–2 beyond what’s explicitly specified here.

Phases already in place (assumed done and available):

* **Phase 0**: SSOT scaffolding (workspace layout, `PathResolver` getters, atomic CSV/log I/O utilities, `ensure_mutable_tree()`, `update_csv_atomic(...)`, `append_log_record(...)`, etc.).
* **Phase 1**: `archi3d catalog build` producing `tables/items.csv` and `tables/items_issues.csv`.
* **Phase 2**: `archi3d batch create` producing/upserting `tables/generations.csv` with **status=`enqueued`** rows and writing `runs/<run_id>/manifest.csv`.

---

## Objective of Phase 3

1. Execute jobs listed in `runs/<run_id>/manifest.csv` (created in Phase 2).
2. For each job:

   * transition **status** from `enqueued → running → completed|failed`,
   * capture **timestamps**, **duration**, **worker environment** (host/user/GPU/env/commit),
   * materialize **output artifacts** (generated `.glb`, optional previews),
   * **upsert** the corresponding row in `tables/generations.csv` (SSOT) with the new fields.
3. Provide **resumability** (safe re-runs after interruption) and **idempotency** (no duplicate rows).
4. Append detailed, structured entries to `logs/worker.log` plus a final summary.

---

## Non-Goals (Out of Scope)

* No consolidate/metrics here (that’s Phase 4+).
* No FScore or VFScore computation.
* No modifications to `tables/items.csv` schema.
* No network/downloader logic.
* No changes to Phase 2’s manifest semantics (it remains derived from `generations.csv` and authoritative for execution order).

---

## Repository Pointers (where to work)

* **CLI**: `src/archi3d/cli.py` — wire `run worker` subcommand and args.
* **Worker**: `src/archi3d/orchestrator/worker.py` — main implementation for Phase 3.
* **DB utilities**: `src/archi3d/db/generations.py` — (if present from Phase 2) reuse helpers for upsert/normalization.
* **Adapters (generation backends)**: under `src/archi3d/adapters/` there are per-algo wrappers. In this phase, define a **minimal uniform adapter contract** (see below). Implement adapters only as thin shells if they already exist; otherwise stub them to a no-op or simulated output behind a `--dry-run`.

Use Phase-0 `PathResolver` getters (`tables_root`, `runs_root`, `logs_root`, file getters) and atomic I/O helpers. **Do not** reimplement atomic I/O.

---

## Functional Requirements

### A) CLI Behavior

Add subcommand:

```
archi3d run worker
  --run-id <string>                  (required)
  [--jobs <glob-or-regex>]           (filter subset of job_id)
  [--only-status <csv-list>]         (default: enqueued; e.g., enqueued,running)
  [--max-parallel <int>]             (default: 1; >=1)
  [--adapter <name>]                 (limit to one algo/adapter; otherwise use the algo from manifest)
  [--dry-run]                        (no external execution, just simulate transitions/logs)
  [--fail-fast]                      (stop on first failure; default off)
```

* By default, process **only** jobs with `status=enqueued` for this `run_id`.
* `--only-status` allows resuming/rerunning stuck `running` jobs (see resumability).
* `--jobs` applies a **contains/regex/glob** filter on `job_id`.
* `--adapter` forces using a specific adapter implementation regardless of manifest `algo` (for debugging).
* `--max-parallel`: implement **N workers** via a simple process/thread pool (start with threads; leave a TODO for process-based).
* `--dry-run`: **do not** invoke real adapters; synthesize “success” with fake timings & a placeholder `gen_object_path` under the run outputs.
* Keep console concise; details go to `logs/worker.log`.

### B) Inputs

* `PathResolver.ensure_mutable_tree()` (Phase 0).
* Read `tables/generations.csv` (SSOT) and `runs/<run_id>/manifest.csv`.
* Validate `run_id` exists and manifest is non-empty; else fail clearly.

### C) Output Directory Layout (per run)

Under `runs/<run_id>/` create:

* `outputs/` — per job artifacts

  * `outputs/<job_id>/generated.glb` (or adapter-specific name; standardize to `generated.glb` in CSV)
  * `outputs/<job_id>/preview_1.png` … `preview_3.png` (optional)
  * `outputs/<job_id>/metadata.json` (adapter raw metadata if provided)
* `state/` — worker state & locks

  * `state/<job_id>.inprogress`
  * `state/<job_id>.completed`
  * `state/<job_id>.failed`
  * `state/<job_id>.error.txt` (error details on failure)

These marker files enable **resumability** and are used alongside SSOT updates.

### D) Job Lifecycle & State Machine

Per job:

1. **Pick**: eligible if `(status in allowed)` and **no** `.inprogress|.completed|.failed` marker present, **or** resumable case:

   * If `status=running` but `.inprogress` exists without recent heartbeats and `--only-status` includes `running`, allow **resume** (see concurrency section).
2. **Mark running**:

   * Create `state/<job_id>.inprogress` (write PID/host/timestamp).
   * Upsert `tables/generations.csv`:

     * `status="running"`
     * `generation_start=<ISO8601 UTC>`
     * `worker_host`, `worker_user`, `worker_gpu` (best effort), `worker_env`, `worker_commit` (if available)
3. **Execute adapter** (unless `--dry-run`; see Adapter Contract).
4. **On success**:

   * Write artifacts in `outputs/<job_id>/…`
   * Upsert `tables/generations.csv` with:

     * `status="completed"`
     * `generation_end`, `generation_duration_s`
     * `gen_object_path="runs/<run_id>/outputs/<job_id>/generated.glb"` (workspace-relative)
     * `preview_1_path…preview_3_path` if created
     * `algo_version` (from adapter), plus any **cost** fields if returned (`unit_price_usd`, `estimated_cost_usd`, `price_source`)
   * Rename marker to `state/<job_id>.completed`
5. **On failure (catch all exceptions)**:

   * Write `state/<job_id>.error.txt` with traceback/summary
   * Upsert `tables/generations.csv`:

     * `status="failed"`
     * `generation_end`, `generation_duration_s`
     * `error_msg` (truncated; keep full text in `error.txt`)
   * Rename marker to `state/<job_id>.failed`
   * If `--fail-fast`, stop the run with a non-zero exit.

> **Idempotency**: If a job is already `completed` and has a `.completed` marker, **skip** (count as already done). If mismatched (e.g., CSV says completed but marker missing), log a warning and treat as completed unless `--only-status` includes `running` and user is explicitly re-running.

### E) Adapter Contract (minimal, stable)

Define a **lightweight interface** (Python protocol or simple function) that all generators implement:

```python
@dataclass
class GenerationRequest:
    job_id: str
    product_id: str
    variant: str
    algo: str
    used_images: list[Path]          # workspace-absolute or resolved paths
    out_dir: Path                    # runs/<run_id>/outputs/<job_id>
    workspace: Path                  # workspace root
    extra: dict                      # future extension

@dataclass
class GenerationResult:
    success: bool
    generated_glb: Path | None
    previews: list[Path]             # 0..3
    algo_version: str | None
    unit_price_usd: float | None
    estimated_cost_usd: float | None
    price_source: str | None
    raw_metadata: dict | None        # optional dump-through

def run_generation(req: GenerationRequest) -> GenerationResult:
    ...
```

* The worker constructs `GenerationRequest` from the manifest row.
* Adapter must **not** write outside `out_dir`.
* On any failure, raise an exception or return `success=False` (the worker uniformly handles it).
* For `--dry-run`, bypass adapter and synthesize a minimal `generated.glb` placeholder (e.g., copy a tiny canned GLB shipped with repo) and a couple of dummy previews.

### F) Fields to **Upsert** into `tables/generations.csv` (Phase 3)

Use Phase-0 `update_csv_atomic(path, df_new, key_cols=["run_id","job_id"])`.

**Key columns (already present from Phase 2):**
`run_id`, `job_id`

**Columns to write/update in Phase 3 (exact names):**

* **Execution meta**

  * `status` ∈ {`running`,`completed`,`failed`}
  * `generation_start` (ISO8601 UTC)
  * `generation_end` (ISO8601 UTC)
  * `generation_duration_s` (float seconds, `(end - start).total_seconds()`)

* **Worker identity**

  * `worker_host` (str)
  * `worker_user` (str)
  * `worker_gpu` (str; best effort like “RTX 4090” or empty)
  * `worker_env` (str; e.g., conda/env name or py version)
  * `worker_commit` (str; optional SCM commit of orchestrator/adapters)

* **Outputs**

  * `gen_object_path` (str, **workspace-relative** → `runs/<run_id>/outputs/<job_id>/generated.glb`)
  * `preview_1_path`, `preview_2_path`, `preview_3_path` (str, rel; empty if absent)
  * `algo_version` (str)

* **Costs (optional)**

  * `unit_price_usd` (float)
  * `estimated_cost_usd` (float)
  * `price_source` (str; e.g., `static_table|api_quote|unknown`) By default, try to read the unit price associated to each algo from the file adapters.yaml in the src/archi3/config directory.

* **Errors**

  * `error_msg` (str; single line, truncated to e.g. 2k chars; full text in `state/<job_id>.error.txt`)

**Do not** touch parent carry-over fields or Phase-2 fields outside of the ones listed above unless filling previously empty `algo_version`.

All paths **must be workspace-relative** using `PathResolver.rel_to_workspace(...)`. Use UTF-8-SIG CSV encoding and atomic+locked writes.

### G) Concurrency, Heartbeats & Resumability

* Use a **thread pool** size `--max-parallel`.
* For each running job, write a **heartbeat** (touch/update) inside `state/<job_id>.inprogress` every ~10–30s with PID & timestamp.
* On start, if a job has `status=running` but the `.inprogress` file timestamp is **stale** (e.g., older than 10 minutes) and `--only-status` includes `running`, **resume** it (overwrite the inprogress marker).
* Ensure **single-runner safety**: use a per-job **`FileLock`** on `state/<job_id>.lock` during state transitions and upserts to avoid double execution if two workers accidentally target the same run.

### H) Logging & Summary

Append structured JSON lines to `logs/worker.log`:

* `worker_started` (run_id, filters, max_parallel, dry_run)
* Per job: `job_started`, `job_completed` (with duration, output paths), `job_failed` (with error summary)
* `worker_summary` with counts:

  ```json
  {
    "event": "worker_summary",
    "timestamp": "...",
    "run_id": "...",
    "processed": <int>,
    "completed": <int>,
    "failed": <int>,
    "skipped": <int>,
    "avg_duration_s": <float>,
    "max_parallel": <int>,
    "dry_run": <bool>
  }
  ```

Keep console output short: show a progress line and final counts.

### I) Validation & Safety

* Before execution, verify that used images from manifest exist (workspace-absolute). If any missing → **fail job** with a clear `error_msg`.
* Ensure `out_dir` exists (`mkdir(parents=True, exist_ok=True)`).
* On success, **assert** that `generated_glb` exists and is non-empty; otherwise treat as failure.
* Wrap adapter execution with a **timeout hook** (optional param in code; default no timeout—leave TODO).

### J) Determinism & Idempotency

* Re-running `run worker` with the same `run_id` does **not** create duplicate rows; it only updates the same `(run_id,job_id)` rows.
* Jobs already marked `completed` and having a `.completed` marker are **skipped** by default.
* If user selects `--only-status running` and a job is “running” but safe to resume, it can be re-entered and ended as completed/failed.

---

## Acceptance Criteria (Definition of Done)

1. **CLI**

   * `archi3d run worker --run-id <id>` processes only `status=enqueued` jobs by default.
   * `--dry-run` synthesizes outputs without calling adapters and updates CSV/logs accordingly.
   * `--max-parallel` limits concurrency.
   * Filters (`--jobs`, `--only-status`) work as specified.

2. **SSOT Updates**

   * `tables/generations.csv` rows for processed jobs have updated fields listed in **F** with correct values and relative paths.
   * No duplicate `(run_id,job_id)` rows; atomic upserts with UTF-8-SIG.

3. **Artifacts & State**

   * Per-job outputs under `runs/<run_id>/outputs/<job_id>/...` (or simulated in dry-run).
   * Proper state markers in `runs/<run_id>/state/`.
   * Resumability: interrupt mid-run, relaunch with `--only-status running`, jobs are correctly resumed or skipped depending on markers and heartbeats.

4. **Logging**

   * `logs/worker.log` contains structured lines for start, per-job events, and final summary with accurate counters.

5. **Safety & Robustness**

   * All writes atomic & locked; no `.tmp` leftovers.
   * Missing inputs produce `failed` jobs with `error_msg`.
   * Paths in CSV are workspace-relative.
   * Works cross-platform (Windows/POSIX).

---

## Minimal Tests / Self-Tests

Create `tests/test_phase3_run_worker.py` (or `scripts/dev/phase3_selftest.py`):

**Test 1 — Dry-run success**

* Workspace with a valid `tables/items.csv` and a simple `generations.csv` + `runs/<run_id>/manifest.csv` (1 job, 2 images).
* Run: `archi3d run worker --run-id <id> --dry-run`.
* Assert: `status=completed`, `gen_object_path` under `runs/.../generated.glb` exists, duration > 0, logs contain summary.

**Test 2 — Real run with failure**

* Make adapter stub raise an exception.
* Assert: `status=failed`, `error_msg` filled, `state/<job_id>.failed` exists, summary counts match.

**Test 3 — Resumability**

* Start `run worker` (dry-run but sleep inside adapter), kill midway, then re-run with `--only-status running`.
* Assert: remaining jobs complete; no duplicates; final counts correct.

**Test 4 — Concurrency**

* `--max-parallel 3` with 5 jobs (dry-run).
* Assert: all complete; average duration < serial time; no lock contention errors.

**Test 5 — Path relativity & idempotency**

* Verify all CSV paths are workspace-relative.
* Re-run the same command; CSV row count unchanged; only `generation_start/end` update when re-executed explicitly.

---

## Deliverables

* Modified:

  * `src/archi3d/cli.py` (register and wire `run worker`)
  * `src/archi3d/orchestrator/worker.py` (Phase 3 core)
* (Optional)

  * `src/archi3d/adapters/base.py` (contract types), and minimal adapter wrappers if missing
  * tests or `scripts/dev/phase3_selftest.py`
* CHANGELOG entry:
  **feat(phase3):** implement run worker with resumable job execution, SSOT generations.csv updates, atomic logging, and per-run artifacts.

---

## Implementation Notes

* Timestamps: `datetime.now(timezone.utc).isoformat()`.
* Duration: compute in seconds with high-resolution timer.
* Host/user: `socket.gethostname()`, `getpass.getuser()`.
* GPU (best effort): inspect `torch.cuda.get_device_name(0)` or `nvidia-smi` parse if available; else empty string (do **not** add new deps).
* Environment string: f`python {sys.version.split()[0]}` + env name if detectable.
* Commit: try `os.environ.get("ARCHI3D_COMMIT")` or leave empty.
* When truncating `error_msg`, keep first 2000 chars and note “(truncated; see error.txt)”.
* All upserts via Phase-0 `update_csv_atomic(...)` with key `["run_id","job_id"]`.
* Always convert adapter output paths to **workspace-relative** before upsert.

---

By implementing Phase 3 per this spec, the project will have a **resilient, resumable execution layer** that keeps **`tables/generations.csv`** as the living SSOT for every generation, paving the way for Phase 4 (consolidate) and Phase 5–6 (FScore/VFScore) to enrich the same records in place.

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

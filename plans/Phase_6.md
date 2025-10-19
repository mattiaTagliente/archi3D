## Role

Implement **Phase 6 — Compute VFScore (Visual Fidelity Metrics)**. Add a robust `archi3d compute vfscore` command that renders and scores **visual fidelity** of generated models against their **reference photos** (the item’s dataset photos), and **upserts** standardized VFScore columns into the SSOT **`tables/generations.csv`**. **Do not** change behavior of Phases 0–5 beyond what is explicitly required here. Phases 0–5 are considered done and available.

---

## Objectives of Phase 6

1. For each **eligible job** (see selection rules), run the **VFScore** pipeline:

   * render the **generated .glb** under a **fixed, standardized Cycles** setup, and
   * have an LLM-vision scorer compare the renders to the item’s reference photos (appearance only, **ignore geometry**).
2. Persist **per-job artifacts** under the run directory and **upsert** standardized VFScore columns into **`tables/generations.csv`** (the SSOT).
3. Append a **structured summary** to `logs/metrics.log` (event: `"compute_vfscore"`).
   (Reuse SSOT, atomic I/O, and path rules from Phase 0; extend `generations.csv` as in Phase 5.)

**Non-Goals:** No FScore here (already Phase 5). No worker execution (Phase 3). No consolidation (Phase 4). No changes to `tables/items.csv`.

---

## Repository Pointers (where to work)

* **CLI:** `src/archi3d/cli.py` — register and wire `compute vfscore` subcommand + args.
* **VFScore runner (new):** `src/archi3d/metrics/vfscore.py` — select jobs, invoke adapter, collect artifacts, upsert SSOT.
* **Adapter (new):** `src/archi3d/metrics/vfscore_adapter.py` — **thin isolation** over the external VFScore implementation (import or CLI).
* **Reuse Phase-0 utilities:** `PathResolver`, `update_csv_atomic`, `append_log_record`, etc. **Do not** reimplement atomic I/O. 

---

## Functional Requirements

### A) CLI Behavior

Add subcommand:

```
archi3d compute vfscore
  --run-id <string>                    (required)
  [--jobs <glob-or-regex>]             (filter by job_id; contains/regex/glob acceptable)
  [--only-status <csv-list>]           (default: completed)
  [--use-images-from <source>]         (default: used; enum: used|source)
  [--repeats <int>]                    (default: 3; LLM scoring repeats)
  [--redo]                             (default: false; recompute even if already present)
  [--max-parallel <int>]               (default: 1)
  [--timeout-s <int>]                  (optional per-job timeout)
  [--dry-run]                          (selection & logging only; skip adapter)
```

Defaults mirror Phase-5 style and selection model. `--use-images-from` chooses which photos to use as **references**: the **used** image set (Phase 2) or the full **source** set from `items.csv` carried into `generations.csv`.

### B) Inputs

* **SSOT:** `tables/generations.csv` (must exist from Phase 2+). 
* **Run tree:** `runs/<run_id>/outputs/<job_id>/generated.glb` (from Phase 3). 
* **Reference photos** from `generations.csv`: columns `used_image_*` (default) or `source_image_*` per `--use-images-from`. 
* **Workspace/I/O:** `PathResolver`, atomic CSV/logging (Phase 0). 

### C) Eligibility & Selection Rules

A row is **eligible** if:

1. `run_id` matches.
2. `status` ∈ `--only-status` (default = `completed`). 
3. `gen_object_path` exists and is non-empty on disk (workspace-relative).
4. At least **1 reference image** exists on disk from the chosen source set.
5. If `--jobs` provided, `job_id` matches.
6. If not `--redo`, skip rows that already have `vf_status="ok"` (or non-null `vfscore_overall`).

Record per-job **skip reasons** for the summary.

### D) Output Directory Layout (per run, metrics)

Under `runs/<run_id>/metrics/vfscore/<job_id>/` write:

* `result.json` — canonical payload (see **E.2**).
* `renders/` — the standardized renders used for LLM scoring (PNG).
* `rationales/` — text files (one per repeat) with LLM explanations.
* `config.json` — effective VFScore config snapshot (model name, rubric weights, camera/HDRI parameters).
* Adapter raw logs as appropriate.

Do **not** overwrite previous files unless `--redo`.

### E) VFScore Adapter Integration

#### E.1 Adapter Contract

```python
@dataclass
class VFScoreRequest:
    cand_glb: Path                 # generated object path
    ref_images: list[Path]         # reference photo paths
    out_dir: Path                  # job metrics dir
    repeats: int                   # LLM scoring repeats
    timeout_s: int | None          # overall per-job timeout
    workspace: Path                # workspace root

@dataclass
class VFScoreResponse:
    ok: bool
    payload: dict                  # canonical result payload (E.2)
    tool_version: str | None
    config_hash: str | None
    render_runtime_s: float | None
    scoring_runtime_s: float | None
    error: str | None
```

Resolution order:

1. Try **Python import** of the VFScore package (preferred).
2. Else, spawn the **CLI** (e.g., `python -m vfscore ...`) mapping to the same semantics.
   On failure, return `ok=False` with `error`.

#### E.2 Canonical `payload` schema (JSON)

Adapter must normalize to:

```json
{
  "vfscore_overall_median": <int>,          // 0–100
  "vf_subscores_median": {
    "finish": <int>,
    "texture_identity": <int>,
    "texture_scale_placement": <int>
  },
  "repeats_n": <int>,
  "scores_all": [<int> ...],                // per-repeat overall
  "subscores_all": [ { "finish":<int>, "texture_identity":<int>, "texture_scale_placement":<int> } ... ],
  "iqr": <float>,
  "std": <float>,
  "llm_model": "<string>",
  "rubric_weights": { "finish":<float>, "texture_identity":<float>, "texture_scale_placement":<float> },
  "render_settings": { "engine":"cycles", "hdri":"<rel or alias>", "camera":"<preset>", "seed": <int> }
}
```

Missing fields from the upstream tool should be `null` in JSON and left blank in CSV.

### F) SSOT Upsert (columns to write/update)

Use Phase-0 `update_csv_atomic(path, df_new, key_cols=["run_id","job_id"])`. **All paths must be workspace-relative**. 

**Columns to add/update in `tables/generations.csv`:**

* **Core scores**

  * `vfscore_overall` (int)
  * `vf_finish` (int)
  * `vf_texture_identity` (int)
  * `vf_texture_scale_placement` (int)
  * `vf_repeats_n` (int)
  * `vf_iqr` (float)
  * `vf_std` (float)

* **Provenance & perf**

  * `vf_llm_model` (string)
  * `vf_rubric_json` (string; JSON-compacted rubric weights)
  * `vf_render_runtime_s` (float)
  * `vf_scoring_runtime_s` (float)
  * `vf_config_hash` (string)
  * `vf_rationales_dir` (string; rel path under metrics dir)

* **Status**

  * `vf_status` (enum: `ok|error|skipped`)
  * `vf_error` (string; single line, truncated ~2000 chars)

> **Idempotency:** If not `--redo`, do **not** overwrite rows with `vf_status="ok"`. (Mirror Phase-5 semantics.) 

### G) Error Handling & Timeouts

Per job:

* Missing/invalid inputs → `vf_status="error"`, `vf_error="<reason>"`; leave numeric fields empty.
* Adapter `ok=false` → `vf_status="error"`, propagate `error`.
* On success → `vf_status="ok"` and fill all available VF fields.
* Respect `--timeout-s` as an overall cap for the adapter; on exceed → error `"timeout"`.

### H) Concurrency

* Support `--max-parallel` via thread pool (as in Phase 5); guard upserts with Phase-0 locks.

### I) Logging & Summary

Append a JSON line to **`logs/metrics.log`** (Phase-0 helper) with:

```json
{
  "event": "compute_vfscore",
  "timestamp": "...",
  "run_id": "...",
  "n_selected": <int>,
  "processed": <int>,
  "ok": <int>,
  "error": <int>,
  "skipped": <int>,
  "avg_render_runtime_s": <float>,
  "avg_scoring_runtime_s": <float>,
  "repeats": <int>,
  "use_images_from": "used|source",
  "redo": <bool>,
  "max_parallel": <int>,
  "dry_run": <bool>
}
```

Console output should remain succinct (counts & locations). (Same style as Phase 5 summary.) 

### J) Safety, Paths, Encoding

* All CSV writes are **atomic & locked**; encoding **UTF-8-SIG**. Store **workspace-relative** paths only. (Phase-0 rules.) 
* Do not alter job `status` (that belongs to Phases 3/4). 

---

## Acceptance Criteria (Definition of Done)

1. **CLI**

   * `archi3d compute vfscore --run-id <id>` processes only eligible rows by default (`status=completed`, with generated object and ≥1 reference photo).
   * `--dry-run` performs selection and logs a summary without writing artifacts/CSV.
   * `--redo` recomputes and overwrites previous VFScore results for selected jobs.

2. **SSOT Updates**

   * `tables/generations.csv` gains/updates the exact VF columns listed in **F**, via `update_csv_atomic(..., ["run_id","job_id"])`.
   * Re-runs without `--redo` do not change already `ok` VF rows (idempotent).

3. **Artifacts**

   * Per-job `runs/<run_id>/metrics/vfscore/<job_id>/result.json` exists on success and matches the canonical payload schema (E.2).
   * `renders/` and `rationales/` folders populated as appropriate.

4. **Logging**

   * `logs/metrics.log` has a structured summary line with accurate counters and averages.

5. **Robustness**

   * Missing/invalid inputs reported with `vf_status="error"` and meaningful `vf_error`.
   * Timeouts are reported as errors.
   * Works on Windows/POSIX; all stored paths are workspace-relative.

---

## Minimal Tests / Self-Tests

Place in `tests/test_phase6_compute_vfscore.py` or `scripts/dev/phase6_selftest.py`:

**Test 1 — Happy path**

* Workspace with a run having 1 `completed` job, valid `gen_object_path`, and at least 1 reference image in `used_image_*`.
* Run with `--dry-run` → assert selection, `dry_run=true`, no files written.
* Run real → assert `result.json` exists; CSV upserted with `vf_status="ok"`, `vfscore_overall` ∈ [0,100]; `vf_repeats_n` matches.

**Test 2 — Missing reference images**

* Remove/blank reference image paths.
* Assert job counted as `error` (`vf_status="error"`, reason indicates missing refs).

**Test 3 — Idempotency**

* Run twice without `--redo` → second run `skipped=1`, VF columns unchanged.

**Test 4 — Redo**

* Run with `--redo` → VF columns updated (you may tweak adapter to change a value).

**Test 5 — Concurrency & Timeout**

* `--max-parallel 2` on 3 synthetic jobs; ensure no race on CSV writes (counts OK).
* Simulate long adapter; run with `--timeout-s 1` → count as `error` with `vf_error="timeout"`.

---

## Deliverables

* **New:**

  * `src/archi3d/metrics/vfscore.py` (command implementation)
  * `src/archi3d/metrics/vfscore_adapter.py` (adapter shim)
* **Modified:**

  * `src/archi3d/cli.py` (wire `compute vfscore`)
* **Optional tests/self-tests:**

  * `tests/test_phase6_compute_vfscore.py` or `scripts/dev/phase6_selftest.py`
* **CHANGELOG:**
  `feat(phase6): add compute vfscore command with standardized visual-fidelity scoring, per-job artifacts, SSOT upserts, and structured metrics logging; idempotent with redo support.`

---

## Implementation Notes

* **Reusability/consistency:** Mirror Phase-5 structure for selection, concurrency, upsert, and logging to minimize new concepts. 
* **Paths:** Convert absolute paths to workspace-relative via `PathResolver.rel_to_workspace(...)` before writing CSV. 
* **Renders:** Use a fixed camera/HDRI and **Cycles** rendering for consistency; persist actual settings in `config.json` and record a short hash in `vf_config_hash`.
* **JSON in CSV:** Store `rubric_weights` compacted as JSON string in `vf_rubric_json`; keep scalar KPIs as first-class columns for BI (overall and per-dimension subscores).
* **Do not** attempt consolidation fixes here; if outputs/markers disagree, instruct users to run `archi3d consolidate` first. 

---

By completing **Phase 6** per this spec, the pipeline will enrich the **single source of truth** (`tables/generations.csv`) with **visual fidelity metrics**, in a reproducible and idempotent manner, leveraging the workspace layout, atomic I/O, and lifecycle already established in Phases 0–5.

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

## Role

Implement **Phase 8 — Monorepo Integration & Plug-in Adapters**. The goal is to unify the three projects (archi3D, FScore, VFScore) under a **single `archi3d` monorepo** while **preserving the Single Source of Truth (SSOT)** and existing CLIs, and formalizing **adapter boundaries** so geometry and visual metrics can be provided by **importable packages or external CLIs** (closed-source binaries allowed). Do **not** alter data schemas, thresholds, or command semantics established in earlier phases. The SSOT remains `tables/items.csv` and `tables/generations.csv`.

---

## Objectives

1. **Monorepo layout & packaging:** consolidate code into a single Python package `archi3d` with optional extras `[fscore]` and `[vfscore]` for import-based integrations. Keep **FScore/VFScore code out of core** (they may be closed-source or installed separately).
2. **Stable adapter contracts:** define explicit **generator** and **metric** adapter interfaces and a **discovery layer** (import first, fallback to CLI) for FScore/VFScore—**no changes to CSV schemas** or Phase-5/6 output columns.
3. **Config unification:** extend existing config to select adapter backends without changing prior defaults. Reuse **PathResolver** roots, SSOT files, and **atomic I/O** utilities from Phase 0. 
4. **Back-compat & idempotency:** keep all Phase-2/3/4/5/6/7 commands and behaviors **unchanged** from a caller’s perspective (selection rules, statuses, quality gate, reports).
5. **Graceful degradation:** when optional tools aren’t installed, metrics commands should **explain how to enable** (install extra or set CLI path) and **exit cleanly**.

---

## Non-Goals (Out of Scope)

* No rework of Phase-1 catalog logic or Phase-2 registry content.
* No changes to Phase-3 worker lifecycle/state markers. 
* No schema changes to `generations.csv` or `items.csv`; no threshold changes; no new report types.

---

## Repository Pointers (where to work)

* **New/modified package structure (top-level):**

  * `src/archi3d/` (existing)

    * `adapters/`

      * `base.py` (generator interface)  **(new)**
    * `metrics/`

      * `fscore_adapter.py` (now uses discovery) **(modify)**
      * `vfscore_adapter.py` (now uses discovery) **(modify)**
      * `discovery.py` **(new)**
    * `plugins/`

      * `metrics.py` (protocols & entrypoints) **(new)**
    * `config/` (reuse Phase-0 schema/paths) **(no breaking changes)** 
    * `cli.py` (wire detection errors nicely; no new flags required) **(modify minimally)**
* **`pyproject.toml`**: add optional extras `[fscore]`, `[vfscore]` and entry points.

---

## Functional Requirements

### A) Adapter Protocols (stable contracts)

1. **Generation adapters** (Phase-3 already uses them; formalize in `adapters/base.py`): keep the `GenerationRequest/GenerationResult` API used by worker; no behavior change. 

2. **Metric adapters** (in `plugins/metrics.py`): define `MetricAdapter` protocols for **FScore** and **VFScore** with these methods:

```python
class FScoreAdapter(Protocol):
    def evaluate(self, req: FScoreRequest) -> FScoreResponse: ...

class VFScoreAdapter(Protocol):
    def evaluate(self, req: VFScoreRequest) -> VFScoreResponse: ...
```

The request/response types **must** remain those specified in Phase-5 and Phase-6 respectively (including canonical JSON payload fields). Do **not** rename fields.

### B) Adapter Discovery (import-first, CLI-fallback)

Implement `metrics/discovery.py` with:

* **Import path search** (default):

  * FScore: try `import fscore` (or configured import name) then a callable `evaluate_one(...)` or equivalent implemented by `fscore_adapter.py`.
  * VFScore: try `import vfscore` likewise (or configured import name).
* **CLI fallback**: if import fails, use configured CLI commands (e.g., `python -m fscore ...`, `python -m vfscore ...`) with the **same canonical payload** normalized by the adapter.
* **Configuration sources (precedence)**:

  1. CLI flags (none added in this phase; keep hidden env overrides only).
  2. **Env vars**: `ARCHI3D_FSCORE_IMPL={import|cli}`, `ARCHI3D_VFSCORE_IMPL={import|cli}`, `ARCHI3D_FSCORE_CLI`, `ARCHI3D_VFSCORE_CLI`.
  3. `global.yaml` optional keys (read-only here) to prefer import/cli names. (Do not change existing defaults; absence means “auto”.) 
* **Error policy**: if neither import nor CLI is available, **raise a friendly, actionable error** from the Phase-5/6 commands explaining how to enable (install `archi3d[fscore]` or set `ARCHI3D_FSCORE_CLI` etc.).

### C) Packaging & Optional Extras

* In `pyproject.toml`:

  * Define extras:

    * `[project.optional-dependencies]`

      * `fscore = ["fscore>=0"]` (placeholder; may be private wheel)
      * `vfscore = ["vfscore>=0"]`
  * Register **entry points** (namespace `archi3d.metrics_adapters`) so third parties can ship plug-ins:

    * `fscore = "thirdparty_pkg:MyFScoreAdapter"`
    * `vfscore = "thirdparty_pkg:MyVFScoreAdapter"`
* In `metrics/discovery.py`, try entry points if direct import fails (this allows vendor plug-ins).

### D) CLI Wiring (no new flags)

* `archi3d compute fscore ...` and `archi3d compute vfscore ...` **must behave identically**; only the adapter resolution changes internally. All SSOT upserts & logging unchanged.
* When unavailable:

  * exit with code ≠0,
  * message: “FScore adapter not found. Install `archi3d[fscore]` or set `ARCHI3D_FSCORE_CLI=/path/to/cmd`.” (analogous for VFScore).

### E) Config & Paths

* Reuse **PathResolver** and Phase-0 atomic I/O; no new globals. All persisted paths must remain **workspace-relative**. 

### F) Documentation Stubs

* Add `docs/INTEGRATION.md`: how to enable FScore/VFScore via extras or CLI env vars; how to author a custom plugin via entry points (short).
* Add `CHANGELOG` entry for Phase 8.

---

## Acceptance Criteria (Definition of Done)

1. **Monorepo & packaging**

   * `pip install .` installs `archi3d` core; `pip install .[fscore]` or `.[vfscore]` enables import-based adapters.
   * Entry-point discovery works (you can register a dummy adapter in tests and it is used).

2. **Adapter discovery**

   * With `fscore` package present: Phase-5 uses **import** path; without it but with `ARCHI3D_FSCORE_CLI`, uses **CLI**; without both, **fails politely**.
   * Same for VFScore (Phase-6). Commands’ flags/columns/logging unchanged.

3. **Back-compat**

   * Phases 2–7 produce identical CSV schemas/columns; reports & gates unchanged and still derive solely from SSOT.

4. **No regressions to Phase-0**

   * Atomic CSV/log helpers and `PathResolver` are reused; no duplicated I/O. 

5. **Cross-platform**

   * Works on Windows/POSIX. CLI fallback uses `os.execvp`/`subprocess` robustly and captures JSON.

---

## Minimal Tests / Self-Tests

Create `tests/test_phase8_integration.py` (or `scripts/dev/phase8_selftest.py`):

**Test 1 — Import path wins**

* Install a dummy `fscore` module in the test venv that returns fixed metrics.
* Run `archi3d compute fscore --run-id <id> --dry-run` (selection only).
  Swap to real run on a tiny sample; assert adapter was `import` (marker in result.json), SSOT columns match Phase-5.

**Test 2 — CLI fallback**

* Uninstall/disable `fscore`; set `ARCHI3D_FSCORE_CLI="python -m fscore"`.
* Assert command runs and populates Phase-5 columns; discovery reports `mode="cli"`.

**Test 3 — Missing adapter → friendly error**

* Remove both import and CLI; assert exit code ≠0 and help text suggests extras/env.

**Test 4 — VFScore symmetry**

* Repeat Tests 1–3 for `compute vfscore` with repeats=1 and dry-run; assert Phase-6 columns are unchanged and idempotent.

**Test 5 — Entry-point plugin**

* Package a toy adapter exposing `archi3d.metrics_adapters` entry point.
* Assert discovery finds and uses it when no import/CLI present.

---

## Deliverables

* **New:**

  * `src/archi3d/metrics/discovery.py`
  * `src/archi3d/plugins/metrics.py` (protocols & entry-point loader)
  * `docs/INTEGRATION.md`
* **Modified:**

  * `src/archi3d/metrics/fscore_adapter.py` (switch to discovery, same payload) 
  * `src/archi3d/metrics/vfscore_adapter.py` (switch to discovery, same payload) 
  * `src/archi3d/cli.py` (surface friendly “adapter missing” errors; no flag changes)
  * `pyproject.toml` (extras + entry points)
* **Optional:** `tests/test_phase8_integration.py` or `scripts/dev/phase8_selftest.py`
* **CHANGELOG:**
  `feat(phase8): monorepo adapters via import/CLI discovery; optional extras for FScore/VFScore; no changes to SSOT or CLI semantics.`

---

## Implementation Notes

* Keep **canonical payloads** exactly as in Phase-5/6 `result.json` and CSV upserts—don’t rename fields.
* When wrapping CLIs, normalize all outputs into the Phase-5/6 canonical schema before upsert.
* Use `PathResolver.rel_to_workspace(...)` for any path you store; all SSOT writes via `update_csv_atomic`. 
* Do not touch report logic; it must continue to read only from `tables/generations.csv`. 

---

By completing **Phase 8** as specified, you will have a **single, clean monorepo** with **extensible, swappable adapters** for geometry and visual metrics, preserving all **SSOT and CLI guarantees** from Phases 0–7 while enabling controlled delivery models (importable packages, private wheels, or CLI binaries).

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

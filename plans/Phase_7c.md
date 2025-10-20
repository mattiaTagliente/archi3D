## Goal

Implement an **importable module + CLI** in the **VFScore** repo that:

1. Exposes a **stable Python API** matching archi3D’s VFScore **adapter contract** (request/response types).
2. Exposes a **CLI** (`python -m vfscore …` / `vfscore …`) that produces the **canonical JSON payload** on stdout and writes per-job artifacts in a provided `out_dir`.
3. Optionally registers a **plugin entry point** so Phase 8 can auto-discover VFScore as an importable **MetricAdapter**, with no changes to the JSON wire schema or archi3D. 

This enables archi3D’s `archi3d compute vfscore` to call VFScore **by import** (preferred) or **via CLI fallback**, and then upsert exactly the VF columns expected by Phase 6 into `tables/generations.csv`. 

---

## Must-Match Contracts (authoritative)

* **Canonical per-job JSON payload** (the *only* schema archi3D reads from VFScore). Your API and CLI must produce exactly this structure (fill missing items with `null`). 

```json
{
  "vfscore_overall_median": <int>,                           // 0–100
  "vf_subscores_median": {
    "finish": <int>,
    "texture_identity": <int>,
    "texture_scale_placement": <int>
  },
  "repeats_n": <int>,
  "scores_all": [<int>, ...],                                // per-repeat overall
  "subscores_all": [
    { "finish":<int>, "texture_identity":<int>, "texture_scale_placement":<int> },
    ...
  ],
  "iqr": <float>,
  "std": <float>,
  "llm_model": "<string>",
  "rubric_weights": { "finish":<float>, "texture_identity":<float>, "texture_scale_placement":<float> },
  "render_settings": { "engine":"cycles", "hdri":"<string>", "camera":"<string>", "seed": <int> }
}
```

* **Phase-6 columns** that archi3D will write into SSOT (your results must map 1:1 from the payload): `vfscore_overall`, `vf_finish`, `vf_texture_identity`, `vf_texture_scale_placement`, `vf_repeats_n`, `vf_iqr`, `vf_std`, `vf_llm_model`, `vf_rubric_json`, `vf_render_runtime_s`, `vf_scoring_runtime_s`, `vf_config_hash`, `vf_rationales_dir`, `vf_status`, `vf_error`. (archi3D does the CSV upsert; VFScore just returns/prints JSON and writes artifacts.) 

* **Adapter discovery (Phase 8)**: archi3D will try **import first**, then **CLI fallback**. Provide both. Also provide an optional **entry point** so discovery can load a concrete `VFScoreAdapter` implementation by name later. 

---

## Deliverables (VFScore repository)

### 1) Package layout (under `src/vfscore/`)

* `__init__.py` — exports `__version__` and API shortcuts.
* `api.py` — public Python API (import-first path).
* `adapter.py` — concrete adapter class that implements `evaluate(req) -> VFScoreResponse`.
* `cli.py` — console interface (`vfscore` / `python -m vfscore`).
* `types.py` — dataclasses / typed dicts for request/response and small helpers.
* `render.py` — standardized renders (Cycles-like contract; can internally select backend).
* `score.py` — LLM-vision scoring (repeat loop + rubric application).
* `aggregate.py` — median / IQR / std aggregator + JSON assembler.
* `io.py` — artifact writing (`result.json`, `config.json`, `renders/`, `rationales/`), safe path ops, config hashing.
* `config.py` — defaults (model name, rubric weights, render setup) and env overrides.

> Keep all writes **inside** the provided `out_dir`. Never write elsewhere. Paths in the JSON can be relative to `out_dir`, but **do not depend on the archi3D workspace** here (archi3D ensures relativity on its side). Phase-0 rules in archi3D enforce workspace-relative SSOT; you just stick to deterministic local artifacts. 

### 2) Python API (import-first)

**Module:** `vfscore.api`

```python
## types.py
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any

@dataclass
class VFScoreRequest:
    cand_glb: Path                 # generated object path
    ref_images: List[Path]         # at least 1 existing file
    out_dir: Path                  # job metrics dir to create/populate
    repeats: int                   # >=1
    timeout_s: Optional[int]       # per-job cap, or None
    workspace: Optional[Path]      # opaque passthrough; may be None

@dataclass
class VFScoreResponse:
    ok: bool
    payload: Dict[str, Any]        # exactly the canonical JSON object above
    tool_version: Optional[str]
    config_hash: Optional[str]
    render_runtime_s: Optional[float]
    scoring_runtime_s: Optional[float]
    error: Optional[str]
```

**Function:** `vfscore.api.evaluate_one(req: VFScoreRequest) -> VFScoreResponse`

Behavioral requirements:

* Validate inputs (existence, repeats ≥1). On validation failure: return `ok=False` with `error`.
* Create `out_dir/` and subfolders:

  * `renders/` — the standardized renders used for scoring.
  * `rationales/` — one text file per repeat with the LLM rationale (or stub text).
* Produce `config.json` (effective params) and `result.json` (canonical payload).
* Compute and return:

  * `payload` as specified (median/iqr/std for overall and subscores),
  * `tool_version` (module `__version__`),
  * `config_hash` (stable hash of `config.json` canonicalized),
  * `render_runtime_s`, `scoring_runtime_s`.
* Do **not** raise on normal errors; report via `ok=False`, `error="<reason>"`. Timeouts must be caught and converted to `ok=False` with `error="timeout"`.

All field names and semantics must match Phase-6; **do not rename**. 

### 3) Adapter class (for Phase 8 readiness)

**Module:** `vfscore.adapter`

Provide:

```python
class VFScoreAdapterImpl:
    """Concrete adapter compatible with archi3D's VFScoreAdapter protocol."""
    def evaluate(self, req) -> "VFScoreResponse":
        # Thin wrapper that calls api.evaluate_one(req)
        ...
```

* This class will later be discoverable via entry points. Keep the method name/signature aligned with the protocol in archi3D Phase 8 (`evaluate(self, req) -> VFScoreResponse`). 

### 4) CLI (stdout JSON + artifacts)

**Entry:** `vfscore` and `python -m vfscore`

Command:

```
vfscore evaluate
  --cand-glb <path>
  --ref-images <path>[,<path>...]
  --out <dir>
  [--repeats <int>=3]
  [--timeout-s <int>]
  [--workspace <path>]
```

Rules:

* On success: print **only** the canonical JSON payload to **stdout** (no extra logs) and exit `0`. Also write `result.json` to `--out`.
* On error: print a minimal JSON `{"ok": false, "error": "<reason>"}` to stdout and exit non-zero.
* All artifacts live under `--out` with the same layout as API.

This matches the **CLI fallback** archi3D will use if import fails. 

### 5) Rendering & Scoring skeletons

* **`render.py`** should provide a deterministic, standardized setup (Cycles-like): camera preset, HDRI identifier/alias, fixed seed. Persist these into `render_settings` in the payload and into `config.json`. (If you don’t ship Blender integration here, implement a pluggable backend and a **stub** backend that generates placeholder renders deterministically.)
* **`score.py`** should implement:

  * a loop of `repeats` scoring passes,
  * per-repeat **overall** and **per-dimension** subscores `{finish, texture_identity, texture_scale_placement}` in **0–100**,
  * rationale text per repeat saved under `rationales/NN.txt`,
  * `llm_model` and `rubric_weights` in the payload. (If no LLM is available, include a deterministic stub scorer; keep the same fields with plausible values. The archi3D side treats VFScore as optional, and reports will still render properly. )
* **`aggregate.py`** computes median of overall and each subscore; also `iqr` and `std`; assemble the canonical JSON object exactly as specified. 

### 6) I/O & Safety

* Write `result.json`, `config.json`, `renders/`, `rationales/` **only inside** `out_dir`. Do **not** touch parent directories.
* JSON must be UTF-8, minified, keys exactly as in the schema.
* Provide stable **`config_hash`** = SHA1 of canonicalized `config.json` (sorted keys).

### 7) Packaging

* **`pyproject.toml`**:

  * `project.scripts`: `vfscore = vfscore.cli:main`
  * `project.entry-points."archi3d.metrics_adapters"` *(optional but recommended now)*:

    * `vfscore = vfscore.adapter:VFScoreAdapterImpl`
* Export `__version__` in `vfscore/__init__.py`.

This mirrors the adapter discovery model archi3D will use in Phase 8 (import path, then entry points, then CLI). 

### 8) Tests / Self-checks (inside VFScore)

Add a minimal self-test script (e.g., `scripts/selftest_vfscore.py`) that:

1. Creates a temp `out_dir`, copies a tiny `.glb` (or generates one) and 1–2 reference images.
2. Calls **API** → asserts `ok`, `result.json` exists, payload contains required keys, and numbers are in valid ranges.
3. Calls **CLI** with the same inputs → asserts stdout is parseable canonical JSON and equals the file payload.
4. Forces a timeout → asserts non-zero exit and `{"ok": false, "error": "timeout"}`.
5. Asserts `config_hash` changes when you tweak a knob (e.g., repeats or rubric weight).

---

## Acceptance Criteria (for VFScore repo)

* **API**: `vfscore.api.evaluate_one(VFScoreRequest)` returns `VFScoreResponse` with fields populated; does not raise on normal failures.
* **CLI**: `vfscore evaluate …` prints canonical JSON to stdout; writes `result.json` under `--out`; proper exit codes.
* **Schema**: `result.json` strictly matches Phase-6 canonical payload; field names/types/structure are exact. 
* **Artifacts**: `renders/`, `rationales/`, `config.json` written under `out_dir/`; `config_hash` stable.
* **Entry point**: optional `archi3d.metrics_adapters` entry registered; `VFScoreAdapterImpl.evaluate()` defers to API.
* **Determinism**: Stub modes are deterministic (fixed seed) to enable CI.
* **No archi3D changes required**: archi3D Phase-6 runner can import `vfscore.api` or shell out to the CLI unchanged; Phase-7 reports will consume the VF columns written by archi3D from your payload; Phase-8 will discover this adapter automatically via import/entry point/CLI.

---

## Notes & Constraints

* **Do not invent new fields**; extras may live in separate files, but the **canonical** JSON returned/printed must remain as above so archi3D can map to SSOT columns verbatim. 
* Keep console **quiet**; the CLI must emit only JSON on stdout (log to stderr if needed).
* Paths provided to the API/CLI can be absolute or relative; you must only write under `out_dir`. Archi3D will later normalize to **workspace-relative** when it upserts to CSV (Phase-0 rule). 

---

By implementing the above in VFScore, you will provide a **Phase-7c-ready** module that archi3D can call **today** via CLI and **tomorrow** via import/adapter discovery, without any schema or CLI changes on the archi3D side, fully aligned with Phases **6–8**.

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

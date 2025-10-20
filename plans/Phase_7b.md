## Role

You are the agent in charge of updating the **FScore** repository to provide **an importable Python module** and **a CLI** that expose **a single canonical API/payload** for the geometric metric. Your code must be consumable by `archi3d compute fscore` (Phase 5) **before** the Phase 8 monorepo integration, adhering **exactly** to the schemas, contracts, and path policies established on the archi3D side.

* The archi3D command (Phase 5) selects jobs and updates the SSOT `tables/generations.csv`; **FScore must never write to the SSOT CSVs**. It must only **calculate** and **write a `result.json`** per job, plus any accessory files.
* The Phase 8 integration discovers an FScore adapter **first by import** (`import fscore`) and **as a fallback by CLI** (`python -m fscore`). Adapt the package for **both** modes.

---

## Objectives

1.  Provide an importable **Python package** `fscore` with a stable function **`evaluate_one(...)`** that returns a **canonical** payload (see schema below). It must work on Windows/POSIX.
2.  Provide a **CLI** (`python -m fscore …`) that accepts the same parameters, performs the evaluation, and writes `result.json` to the job's output folder.
3.  Guarantee **semantic parity** between import and CLI: **same result**, same `result.json`, same fields required by archi3D Phase 5.
4.  Expose **version** and **config hash** in the result; calculate the required **metrics** (F-score, precision, recall, Chamfer-L2, distance stats) and, if available, **alignment transformations** and **mesh meta**.
5.  Manage job **timeouts**, clear errors, idempotency of output files.

---

## Integration Contract (derived from archi3D Phase 5 & 8)

### 1) Python-side API (import)

Implement in the `fscore` package:

```python
## src/fscore/api.py (or evaluator.py)
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

@dataclass
class FScoreRequest:
    gt_path: Path             # path to GT (.glb/.fbx)
    cand_path: Path           # path to generated (.glb)
    n_points: int             # default 100_000
    out_dir: Path             # directory for job artifacts
    timeout_s: Optional[int]  # total timeout (can be None)

@dataclass
class FScoreResponse:
    ok: bool
    payload: Dict[str, Any]   # see canonical schema below
    tool_version: Optional[str]
    config_hash: Optional[str]
    runtime_s: Optional[float]
    error: Optional[str]

def evaluate_one(req: FScoreRequest) -> FScoreResponse:
    """Performs the evaluation and returns FScoreResponse.
    ALWAYS writes out_dir/'result.json' with the canonical `payload`."""
    ...
```

* The **name** `evaluate_one` and the **signature** consistent with Phase 5 are **mandatory**; the archi3D adapter will look for this callable (or equivalent) via import.
* The `payload` is the **canonical JSON** (following paragraph).

### 2) CLI (fallback)

Provide `python -m fscore` with:

```
python -m fscore \
  --gt <path_to_gt> \
  --cand <path_to_cand> \
  --out <out_dir> \
  [--n-points 100000] \
  [--timeout-s <sec>]
```

* The CLI must write `result.json` in `<out_dir>` with **the same payload** as the API.
* Exit code: `0` on `ok=True`, otherwise `1`.
* Messages to stderr must be **short and clear** (one line with the reason).
* This CLI will be invoked by archi3D as a fallback (Phase 8).

---

## **Canonical** `result.json` Schema (mandatory)

The `payload` must **match exactly** this schema (Phase 5 — E.2):

```json
{
  "fscore": <float>,
  "precision": <float>,
  "recall": <float>,
  "chamfer_l2": <float>,
  "n_points": <int>,
  "alignment": {
    "scale": <float>,
    "rotation_quat": {"w":<float>,"x":<float>,"y":<float>,"z":<float>},
    "translation": {"x":<float>,"y":<float>,"z":<float>}
  },
  "dist_stats": {
    "mean": <float>, "median": <float>,
    "p95": <float>, "p99": <float>, "max": <float>
  },
  "mesh_meta": {
    "gt_vertices": <int>, "gt_triangles": <int>,
    "pred_vertices": <int>, "pred_triangles": <int>
  }
}
```

* If you cannot calculate some sub-fields, set `null` in the JSON and do **not** write absolute paths.
* This schema is what archi3D will map into CSV columns **with the exact names** listed in Phase 5 (F): `fscore`, `precision`, `recall`, `chamfer_l2`, `fscore_n_points`, `fscore_scale`, `fscore_rot_w/x/y/z`, `fscore_tx/ty/tz`, `fscore_dist_mean/…`, `fscore_runtime_s`, `fscore_tool_version`, `fscore_config_hash`, `fscore_status`, `fscore_error`.

---

## Expected Evaluation Behavior

* **Point Sampling** (cand & GT) with Poisson disk/Uniform (consistent with the current implementation), `n_points` default **100,000**.
* Calculation of **precision**, **recall**, **F-score** with a distance threshold as per your existing metric; **Chamfer-L2**; distance statistics (**mean/median/p95/p99/max**).
* **Alignment**: if the pipeline performs ICP/scale fit/rigid align, report `scale`, `rotation_quat`, `translation`. If not available, leave `null`.
* **Mesh meta**: vertices/triangles for GT and pred; if not available, `null`.
* Total **Runtime** (sec) in the `runtime_s` response field.
* `tool_version`: `__version__` of the package; `config_hash`: deterministic hash (sha1) of the material options (e.g., serialized `{"n_points":..., "align":..., "threshold":..., "seed":...}`).

---

## archi3D Compatibility (Phase 5 & 8)

* **Phase 5 (archi3d compute fscore)** invokes the adapter: if import works, it will call `evaluate_one(FScoreRequest)`; if not, it will use the CLI and read `result.json`. In both cases, it **must find the same schema** to upsert into `generations.csv`.
* **Phase 8** introduces a **discovery layer** that first looks for the import (`import fscore`) and then the CLI command (configurable via env). Your package must make **both paths functional** without additional special flags.

---

## Paths & Artifacts

For **each job**:

```
<out_dir>/
  result.json          # canonical payload (mandatory)
  dist_stats.json      # optional (redundant, if you want to export more details)
  mesh_meta.json       # optional
  logs.txt             # optional, for debug
```

* **Do not** write to the SSOT CSVs; do not create folders outside of `<out_dir>`. The SSOT upsert is archi3D's responsibility.

---

## Timeout & Error Policy

* If `timeout_s` is set, interrupt with `ok=False`, `error="timeout"`, no partial `result.json` (or write it with a `null` payload, but it's **better not to write it** in case of timeout—archi3D handles the error).
* I/O errors, mesh parsing, unsupported formats → `ok=False` with a concise `error` (<= 2000 characters), exit code `1` from CLI.
* In case of `ok=True`, `result.json` **must** exist.

---

## Surface API (CLI)

```
usage: python -m fscore --gt GT_PATH --cand CAND_PATH --out OUT_DIR
                         [--n-points 100000] [--timeout-s SECONDS]
                         [--seed INT] [--align {auto,none}] [--threshold FLOAT]

## Behavior:
## - Creates OUT_DIR if missing
## - Performs evaluation
## - Writes OUT_DIR/result.json (canonical payload)
## - Prints a summary to stdout "FSCORE=<..> PREC=<..> RECALL=<..> CHAMFER_L2=<..>"
## - Exit 0 on success, 1 on error
```

> Note: the additional flags (`--seed`, `--align`, `--threshold`) are optional; **if present** they must be included in the `config_hash`.

---

## FScore Repository Structure (new/updated)

```
src/fscore/__init__.py        # exposes __version__, evaluate_one
src/fscore/api.py             # FScoreRequest/FScoreResponse + evaluate_one(req)
src/fscore/cli.py             # argparse parser and main()
src/fscore/__main__.py        # from .cli import main; main()
src/fscore/metrics.py         # metric implementations (precision/recall/fscore, chamfer, stats)
src/fscore/sampling.py        # point sampling from meshes
src/fscore/align.py           # ICP / best-fit scale + rigid (if available)
src/fscore/io.py              # mesh loader (GLB/FBX -> trimesh/open3d), safe normals, unit handling
src/fscore/utils.py           # timer, config_hash, safe json dump
pyproject.toml                # entry-points, metadata
tests/test_cli.py             # CLI smoke test
tests/test_api.py             # API smoke test
```

* Maintain compatibility with any existing code (if there are files with different names, you can **adapt**, but the public export must respect this contract: import `fscore` with `evaluate_one`, CLI execution via `python -m fscore`).

---

## Acceptance Criteria (DoD)

**Functional:**

1.  **Import path**: `from fscore.api import FScoreRequest, evaluate_one` works; calling `evaluate_one` returns `FScoreResponse(ok=True, payload=…, tool_version, config_hash, runtime_s, error=None)` and `result.json` is written.
2.  **CLI**: `python -m fscore --gt ... --cand ... --out ...` produces the **same** `result.json` and returns exit 0.
3.  **Canonical Payload**: `result.json` respects **exactly** the schema required by archi3D Phase 5 (keys and nesting).
4.  **Metrics**: `fscore`, `precision`, `recall`, `chamfer_l2`, `n_points`, `dist_stats{…}` are populated; if available, `alignment{…}` and `mesh_meta{…}`. Missing fields are `null`.
5.  **Timeout**: with `--timeout-s` exceeded → `ok=False`, `error="timeout"`, exit 1, no valid `result.json`.

**Phase 5/8 Compatibility:**
6.  The API/CLI is **binary** identical on Windows/POSIX; no absolute paths in the payload; no writing to SSOT CSVs.
7.  Import-first/CLI-fallback: the package is importable; the CLI is available with `python -m fscore`. (Phase 8 discovery).

**Quality:**
8.  **Determinism**: with the same inputs and seed, same numbers.
9.  **Versioning**: `__version__` is valued; `tool_version` is present in the response.
10. **Config hash**: stable hash of the JSON of the actual material options.

---

## Minimum Tests (in the FScore repo)

* **API Happy Path Test**: on two small synthetic meshes (or mini .glb included in tests), `evaluate_one` returns ok and a valid `result.json`.
* **CLI Happy Path Test**: same thing via CLI.
* **Missing GT/Cand Test**: non-existent files → exit 1, `ok=False`, semantic `error`.
* **Timeout Test**: simulate a slow calculation and verify `timeout`.
* **Determinism Test**: fixed seed → same results.

---

## Implementation Notes

* **Sampling/alignment**: reuse what you already have (Open3D/trimesh/…); if alignment is not available, leave `null` in `alignment`.
* **Chamfer-L2**: make sure the definition is the same as used by your current FScore (L2, not L1).
* **Performance**: `n_points` default 100k (configurable); avoid excessive memory loads; use float32/float64 where sensible.
* **Serialization**: write `result.json` with UTF-8, `ensure_ascii=False`, numbers in decimal, `indent=2` (optional).
* **Format robustness**: support `.glb` and `.fbx`; if necessary, use an internal converter (as you already do) to standardize to triangles.
* **Logging**: optional `logs.txt` in `out_dir`; not called by archi3D but useful for debugging.
* **No side effects**: do not create/alter paths outside of `out_dir`.

---

## archi3D References to Respect

* **Phase 5 — Compute FScore**: adapter contract, canonical payload, SSOT columns to populate, and idempotency/redo rules (on the archi3D side).
* **Phase 6 — Compute VFScore**: *conceptual symmetry* (you don't need to implement VFScore, but note the same pattern).
* **Phase 7 — Reports**: reports read from the SSOT; do not introduce alternative formats.
* **Phase 8 — Adapters & Discovery**: import-first, CLI fallback, no schema changes.
* **Phase 0 — Atomicity/Path policy (SSOT)**: archi3D uses **relative** paths in CSVs; FScore must **not** write to the CSVs.

---

## Deliverable

1.  Installable `fscore` package (PEP 621) with:

    * `evaluate_one(FScoreRequest) -> FScoreResponse`
    * `python -m fscore` (CLI)
    * `__version__`
2.  Metric implementation + **canonical** `result.json` file per job.
3.  Minimum tests passing (API/CLI/timeout/errors).
4.  Synthetic README with usage examples (API and CLI).

With these changes, FScore will be **plug-and-play** for archi3D **Phase 5** and ready for **Phase 8** discovery (import/CLI), without touching the SSOT schemas or the semantics of the archi3D commands.

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

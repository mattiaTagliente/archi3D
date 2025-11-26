## Implementation Status

### Windows PyTorch DLL Loading Fix (2025-11-26) ✅ RESOLVED

**Problem**: VFScore evaluation failed on Windows with DLL initialization error:
```
OSError: [WinError 1114] A dynamic link library (DLL) initialization routine failed.
Error loading "C:\Users\matti\venvs\archi3D\Lib\site-packages\torch\lib\c10.dll" or one of its dependencies.
```

**Traceback Analysis**:
```python
File "src/vfscore/evaluator.py", line 76, in vfscore.evaluator.evaluate_visual_fidelity
File "vfscore/objective2/__init__.py", line 18, in <module>
    from vfscore.objective2.pipeline_objective2 import (
File "src/vfscore/objective2/pipeline_objective2.py", line 30, in init
File "src/vfscore/objective2/lpips_score.py", line 7, in init
File "lpips/__init__.py", line 7, in <module>
    import torch
File "torch/__init__.py", line 281, in <module>
    _load_dll_libraries()
File "torch/__init__.py", line 264, in _load_dll_libraries
    raise err  # Error loading c10.dll
```

**Root Cause**:
1. VFScore's compiled `.pyd` modules have lazy imports - `import torch` happens when `evaluate_visual_fidelity()` is CALLED, not when the module is imported
2. By that point, Windows DLL search path is already locked
3. Even though `sitecustomize.py` added torch's lib directory via `os.add_dll_directory()`, torch's own `_load_dll_libraries()` fails
4. Torch iterates through all DLLs in `torch/lib/` and tries to load them with `kernel32.LoadLibraryExW(dll, None, 0x00001100)`
5. The flags `0x00001100` = `LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS`
6. This search mode doesn't respect `os.add_dll_directory()` when called after Python's import system is already active

**Why Simple Tests Worked But archi3d Failed**:
- `python test_torch_import.py` works: Direct `import torch` before any compiled modules load
- `python test_vfscore_import.py` works: Calls `os.add_dll_directory()` then imports vfscore
- `archi3d compute vfscore` fails: Lazy import of torch happens deep inside compiled vfscore code, AFTER import chain starts

**Solution**: Pre-import torch in `sitecustomize.py` to force all DLL loading at Python startup:

```python
# sitecustomize.py (in site-packages)
if sys.platform == "win32":
    import pathlib
    torch_lib_path = pathlib.Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib"

    if torch_lib_path.exists():
        # Add to PATH
        torch_lib_str = str(torch_lib_path)
        if torch_lib_str not in os.environ.get("PATH", ""):
            os.environ["PATH"] = torch_lib_str + os.pathsep + os.environ.get("PATH", "")

        # Use os.add_dll_directory
        try:
            os.add_dll_directory(torch_lib_str)
        except (OSError, AttributeError):
            pass

        # *** CRITICAL: Pre-import torch to force DLL loading NOW ***
        # This ensures all torch DLLs are loaded with the correct search path
        # BEFORE any compiled vfscore modules try to import torch
        try:
            import torch  # noqa: F401
            _ = torch.__version__  # Force initialization
        except Exception:
            pass  # Let it fail later with better error context
```

**Why This Works**:
- `sitecustomize.py` runs automatically at Python startup, BEFORE any user code
- Pre-importing torch loads all DLLs (c10.dll, torch.dll, etc.) with the correct search path
- When vfscore's compiled modules later do `import torch`, the module is already loaded
- No DLL loading happens during vfscore execution - torch is already initialized

**Files Modified**:
- `C:\Users\matti\venvs\archi3D\Lib\site-packages\sitecustomize.py`: Added torch pre-import (lines 35-44)

**Result**:
- ✅ VFScore evaluation now works without DLL errors
- ✅ Torch DLLs load correctly at Python startup
- ✅ Solution is transparent - no changes to archi3D or VFScore code needed
- ✅ Works for both `archi3d` command and `python -m archi3d`

---

### Dry-Run Availability Check Fix (2025-11-26)

**Problem**: The early availability checks for FScore and VFScore were being skipped during `--dry-run` mode:

```python
if not dry_run:
    try:
        import fscore
    except ImportError as e:
        raise RuntimeError("FScore not installed...")
```

This created a **misleading user experience**:
- User runs `archi3d compute fscore --dry-run` without FScore installed
- Command succeeds, shows jobs would be processed
- User thinks environment is configured correctly
- User removes `--dry-run` flag
- Command fails with "FScore not installed" error

**Root Cause**: The check was intentionally skipped to allow previewing selection logic without requiring the tools, but this meant users couldn't validate their environment setup with dry-run.

**Solution**: Remove the `if not dry_run:` condition so the availability check **always runs**, even in dry-run mode. The `--dry-run` flag should only skip the actual evaluator calls, not the basic environment validation.

**Files Modified**:
- `src/archi3d/metrics/fscore.py:387-393`: Removed `if not dry_run` wrapper
- `src/archi3d/metrics/vfscore.py:358-364`: Removed `if not dry_run` wrapper

**Result**: Now both commands fail fast with clear error messages if the required module is missing, regardless of `--dry-run` flag. Users can confidently use dry-run to validate their environment before running expensive computations.

---

### VFScore Pydantic Validation Fix (2025-11-26)

**Problem**: After installing the VFScore wheel (v0.2.0), users encountered a Pydantic validation error:
```
VFScore error: A non-annotated attribute was detected: `get_project_root = <cyfunction Config.get_project_root at 0x...>`.
All model fields require a type annotation
```

**Root Cause**:
- VFScore's `Config` class (Pydantic BaseModel) contained methods like `load()`, `get_project_root()`, and `resolve_paths()`
- When Cython compiles these methods, they become `cyfunction` objects attached to the class
- Pydantic v2's model introspection sees these as class attributes without type annotations
- Pydantic tries to validate them as fields, causing "A non-annotated attribute was detected" error
- **Neither `extra="allow"` nor `extra="ignore"` prevents this** - Pydantic still sees the cythonized methods

**Solution**: Extract all methods as module-level functions - keep the Config class as pure data:

```python
# config.py - BEFORE (fails with Cython - methods become cyfunction objects)
class Config(BaseModel):
    model_config = ConfigDict(...)

    paths: PathsConfig = Field(default_factory=PathsConfig)

    @classmethod
    def load(cls, config_path: Path | str = "config.yaml") -> "Config":
        ...

    def get_project_root(self) -> Path:
        ...

    def resolve_paths(self, project_root: Path | None = None) -> None:
        ...

# config.py - AFTER (Cython compatible - no methods, only data fields)
class Config(BaseModel):
    model_config = ConfigDict(...)

    paths: PathsConfig = Field(default_factory=PathsConfig)
    # No methods!

# Module-level functions (not compiled as class methods)
def load_config_from_file(config_path: Path | str = "config.yaml") -> Config:
    ...

def get_project_root_for_config(config: Config) -> Path:
    ...

def resolve_config_paths(config: Config, project_root: Path | None = None) -> None:
    ...
```

**Rationale**:
- Pydantic BaseModel classes with methods are fundamentally incompatible with Cython compilation
- The Config dict settings (`extra="allow"`, `extra="ignore"`) don't solve the cyfunction detection issue
- **Proper solution**: Separate data (Config class) from behavior (module functions)
- Module-level functions compile correctly and don't interfere with Pydantic introspection
- This is actually cleaner architecture - Config becomes a pure data container
- **Both `config.py` AND `evaluator.py` can now be compiled**, protecting IP

**Result**:
- ✅ VFScore wheel loads successfully without Pydantic validation errors
- ✅ Config is now a pure Pydantic data model (no methods)
- ✅ All functionality preserved via module-level functions
- ✅ config.py is compiled to .pyd, protecting IP
- ✅ Clean separation of data (Config) and behavior (functions)

**Files Modified**:
- `src/vfscore/config.py:274-287`: Removed all methods from Config class
- `src/vfscore/config.py:318-382`: Added module-level functions `load_config_from_file()`, `get_project_root_for_config()`, `resolve_config_paths()`
- `src/vfscore/config.py:389-403`: Updated `get_config()` and `reload_config()` to use new function names
- `setup.py:12`: Re-enabled config.py compilation

---

### VFScore Windows PyTorch DLL Loading Fix (2025-11-26)

**Problem**: After fixing the Pydantic error, VFScore evaluation on Windows still failed with:
```
VFScore error: [WinError 1114] A dynamic link library (DLL) initialization routine failed.
Error loading "C:\Users\matti\venvs\archi3D\Lib\site-packages\torch\lib\c10.dll" or one of its dependencies.
```

**Root Cause**:
- VFScore's `objective2.lpips_score` module imports PyTorch at module level (line 9: `import torch`)
- `lpips_score.py` is compiled to Cython (becomes `lpips_score.pyd`)
- On Windows, PyTorch's C extension DLLs (`c10.dll`, etc.) require their containing directory to be in the DLL search path
- When importing from compiled Cython modules, Windows doesn't automatically add torch's lib directory to the search path
- The DLL fix was initially added to `evaluator.py`, but **`evaluator.py` was also being compiled to Cython**, so the fix code was converted to C and didn't run before the torch import

**Solution**: Two-step fix in `__init__.py` - add DLL directory AND pre-import torch:

```python
# src/vfscore/__init__.py (lines 8-23)
if sys.platform == "win32":
    # Step 1: Add torch DLL directory to search path
    import pathlib
    torch_lib_path = pathlib.Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib"
    if torch_lib_path.exists():
        os.add_dll_directory(str(torch_lib_path))

    # Step 2: Pre-import torch to force DLL loading BEFORE vfscore's compiled modules
    # This ensures c10.dll and other torch DLLs are loaded with the correct search path
    try:
        import torch
        _ = torch.__version__  # Force initialization
    except Exception:
        pass  # Let it fail later with better error context

# Import happens AFTER torch is pre-loaded
from vfscore.evaluator import evaluate_visual_fidelity
```

**Rationale**:
- `os.add_dll_directory()` only affects DLLs loaded AFTER the call
- The compiled vfscore modules (evaluator.pyd, pipeline_objective2.pyd, lpips_score.pyd) form an import chain
- When evaluator.pyd imports pipeline_objective2.pyd imports lpips_score.pyd imports torch, the DLLs haven't loaded yet
- **Key insight**: Pre-importing torch forces Windows to load c10.dll and other torch DLLs with the updated search path
- Then when compiled vfscore modules import torch, the DLLs are already loaded
- `__init__.py` is NOT compiled (standard practice), so this runs as pure Python
- **Both `evaluator.py` AND `config.py` remain compiled**, protecting IP

**Files Modified**:
- `src/vfscore/__init__.py:8-23`: Added two-step DLL fix (add directory + pre-import torch)
- `setup.py:12`: `evaluator.py` compilation enabled (protecting IP)

**Status**: ✅ Complete. Proper fix implemented, ready for wheel rebuild/testing.

---

### FScore & VFScore Early Availability Check + Concise Error Messages (2025-11-26)

**Problem**:
1. FScore had same issue as VFScore - no early check if module is installed
2. Error messages saved to CSV were too verbose (500-2000 chars), cluttering generations.csv
3. Console error messages were unnecessarily verbose

**Solution**:

1. **Added Early FScore Availability Check** (`src/archi3d/metrics/fscore.py:387-394`):
   ```python
   if not dry_run:
       try:
           import fscore
       except ImportError as e:
           raise RuntimeError(
               "FScore not installed. See quickstart.md for installation instructions."
           ) from e
   ```

2. **Made All Error Messages Concise** (200 char max):
   - FScore adapter: `"FScore error: ..."` (was `"FScore import API error: ..."`)
   - VFScore adapter: `"VFScore error: ..."` (was `"VFScore import API error: ..."`)
   - Timeout errors: `"FScore timeout"` / `"VFScore timeout"` (was `"timeout"`)
   - Unexpected errors: `"Unexpected: {e[:180]}"` (was `"Unexpected error: {e[:2000]}"`)
   - Processing failures: `"Processing failed: {e[:180]}"` (was `"Processing exception: {e[:2000]}"`)
   - Unknown errors: `"FScore error (unknown)"` (was `"unknown_error"`)

3. **Updated Both Compute Functions**:
   - Console error message now concise: `"FScore not installed. See quickstart.md..."`
   - Same format for both FScore and VFScore for consistency

**Result**:
- Clear, immediate feedback if wheels not installed
- CSV error columns remain readable with concise messages
- Console errors reference quickstart.md for installation help

---

### VFScore Reference Image Fix (2025-11-26)

**Problem**: After fixing VFScore configuration integration, users reported `no_reference_images_found` error even without VFScore installed. This revealed two issues:
1. The command would proceed with 0 eligible jobs without warning about missing VFScore
2. Column name mismatch: code expected `used_image_a/b/c` but CSV had `used_image_1/2/3_path`

**Root Cause Analysis**:
- Error occurred in eligibility check (`_get_reference_images()`) BEFORE VFScore invocation
- `_get_reference_images()` looked for lettered columns (`_a`, `_b`, `_c`, etc.)
- Actual CSV columns from batch creator: `used_image_1_path`, `used_image_2_path`, etc.
- No early validation that VFScore wheel was installed

**Solution** (`src/archi3d/metrics/vfscore.py`):

1. **Early VFScore Availability Check** (lines 358-367):
   - Added check at start of `compute_vfscore()` function
   - Attempts `import vfscore` before processing any jobs
   - Raises clear `RuntimeError` with installation instructions if missing
   - Skipped in dry-run mode to allow previewing without VFScore

2. **Fixed Column Name Lookup** (lines 96-103):
   - Changed from lettered suffixes (`a`, `b`, `c`) to numbered (`1`, `2`, `3`)
   - Changed from no suffix to `_path` suffix
   - Updated comment to document actual column naming scheme
   - Now correctly matches batch creator output format

**User Experience Improvement**:
- Before: Confusing `no_reference_images_found` error with 0 jobs processed
- After: Clear error message on startup if VFScore not installed:
  ```
  ERROR: VFScore is not installed. Please install it with:
      uv pip install path/to/vfscore_rt-*.whl

  VFScore is required for visual fidelity metrics computation.
  ```

**Testing**: Verified column names match batch creator output in `src/archi3d/orchestrator/batch.py:336-341` and worker output in `worker.py:273`.

---

## Implementation Status

### Phase 0 — Conventions & Layout (SSOT scaffolding) ✅ COMPLETE

**Objective**: Establish a single, canonical workspace layout and provide safe, reusable I/O primitives for atomic CSV/log writes.

**Implemented Components**:

1. **PathResolver Extensions** (`src/archi3d/config/paths.py`):
   - Added `logs_root` / `logs_dir` property for logs directory
   - Added property aliases: `tables_root`, `runs_root`, `reports_root`, `logs_root`
   - Implemented `ensure_mutable_tree()` method (creates tables/, runs/, reports/, logs/)
   - Added SSOT file path getters:
     - `items_csv_path()`, `items_issues_csv_path()`, `generations_csv_path()`
     - `catalog_build_log_path()`, `batch_create_log_path()`, `worker_log_path()`, `metrics_log_path()`

2. **Atomic I/O Utilities** (`src/archi3d/utils/io.py`):
   - `write_text_atomic(path, text)` — Atomic file writes via temp + rename, with fsync
   - `append_log_record(path, record)` — Thread-safe log appending with ISO8601 timestamps
   - `update_csv_atomic(path, df_new, key_cols)` — CSV upsert with locking, returns (inserted, updated)

3. **Test Suite** (`tests/test_phase0_paths_and_io.py`):
   - 21 comprehensive tests covering all Phase 0 functionality
   - Tests for PathResolver, atomic writes, log appending, CSV upserts
   - All tests passing with proper fixtures and temp workspace isolation

**Key Deliverables**:
- Backward-compatible PathResolver enhancements
- Thread-safe, atomic I/O primitives for concurrent access
- Comprehensive test coverage (21/21 tests passing)
- Zero behavioral regressions in existing code

**Non-Functional Changes**:
- No changes to CLI commands or user-facing behavior
- All existing imports and APIs remain compatible
- Linting/formatting applied (ruff, black)

### Phase 1 — Catalog Build ✅ COMPLETE

**Objective**: Implement `archi3d catalog build` command to scan curated dataset folder, enrich with JSON metadata, and write canonical `tables/items.csv` and `tables/items_issues.csv` SSOT tables.

**Implemented Components**:

1. **Catalog Build Function** (`src/archi3d/db/catalog.py`):
   - `build_catalog(dataset_path, products_json_path, paths)` — Main entry point
   - Folder name parsing: `_parse_folder_name()` extracts product_id and variant
   - Image selection: `_collect_and_sort_images()` implements tagged-first, then lexicographic ordering (max 6)
   - GT selection: `_select_gt_object()` prefers .glb over .fbx, warns on multiple candidates
   - JSON enrichment: `_extract_enrichment_data()` with IT/EN locale preference
   - Issue tracking: Generates structured issues for missing data, too many images, etc.

2. **CLI Integration** (`src/archi3d/cli.py`):
   - `archi3d catalog build` command with optional `--dataset` and `--products-json` flags
   - Auto-discovery: Searches for `products-with-3d.json` in workspace root and parent directory
   - Defaults: Uses `paths.dataset_root` for dataset, gracefully handles missing JSON
   - Output summary: Displays item counts, issues count, and output file paths

3. **SSOT Schema** (`tables/items.csv`):
   - **Columns** (19 total, in order):
     1. `product_id` (str) — Product identifier
     2. `variant` (str) — Variant name (default: "default")
     3. `manufacturer` (str) — From JSON enrichment
     4. `product_name` (str) — From JSON (IT preferred)
     5. `category_l1`, `category_l2`, `category_l3` (str) — Category hierarchy
     6. `description` (str) — From JSON (IT preferred)
     7. `n_images` (int) — Number of selected images (0-6)
     8. `image_1_path` ... `image_6_path` (str) — Workspace-relative image paths
     9. `gt_object_path` (str) — Workspace-relative GT file path
     10. `dataset_dir` (str) — Workspace-relative dataset folder path
     11. `build_time` (ISO8601) — Build timestamp
     12. `source_json_present` (bool) — Whether JSON enrichment was available
   - **Key columns**: `(product_id, variant)` uniquely identify each item
   - **Encoding**: UTF-8-SIG for Excel compatibility
   - **Paths**: All paths are workspace-relative (POSIX format with forward slashes)

4. **Issues Tracking** (`tables/items_issues.csv`):
   - **Columns**: `product_id`, `variant`, `issue`, `detail`
   - **Issue types**: `no_images`, `too_many_images`, `missing_gt`, `multiple_gt_candidates`, `missing_manufacturer`, `missing_product_name`, `missing_description`, `missing_categories`
   - Allows tracking data quality problems for manual review

5. **Test Suite** (`tests/test_phase1_catalog_build.py`):
   - 8 comprehensive tests covering:
     - Basic build without JSON enrichment
     - Image selection with tagged (_A-_F) and untagged images, 6-image cap
     - GT file preference (.glb over .fbx) and multiple candidate warnings
     - JSON enrichment with IT/EN locale preference
     - Missing enrichment field detection
     - Workspace-relative path validation
     - Atomic writes (no .tmp files left behind)
     - Idempotent builds (same results on reruns)
   - All 8 tests passing

**Key Features**:
- **Atomic writes**: Uses temp file + rename pattern for items.csv and items_issues.csv
- **Workspace-relative paths**: All stored paths are relative to workspace root (no absolute paths, no drive letters)
- **Folder naming flexibility**: Parses both `{product_id}` and `{product_id} - {variant}` formats
- **Image tagging priority**: Tagged images (_A-_F) selected first, preserving photographer intent
- **JSON auto-discovery**: Searches workspace and parent directory for products-with-3d.json
- **Graceful degradation**: Works without JSON (creates items with empty enrichment fields)
- **Structured logging**: Writes JSON-formatted log entries to `logs/catalog_build.log`

**CLI Examples**:
```bash
# Basic usage (auto-discovers dataset/ and products-with-3d.json)
archi3d catalog build

# Explicit paths
archi3d catalog build --dataset /path/to/dataset --products-json /path/to/products.json

# Without enrichment JSON
archi3d catalog build --dataset /path/to/dataset
```

**Non-Functional Changes**:
- No changes to existing orchestrator, adapters, or worker logic
- No schema changes to existing tables (this creates new `items.csv` and `items_issues.csv`)
- All writes use atomic I/O from Phase 0
- Linting/formatting applied (ruff, black)

**Known Constraints**:
- Product IDs in folder names must be numeric (enforced by regex `\d+`)
- Image files must have extensions: .jpg, .jpeg, .png (case-insensitive)
- GT files must have extensions: .glb or .fbx (case-insensitive)
- Maximum 6 images per item (hard cap, excess images ignored)
- JSON structure must match expected schema (ProductId, Manufacturer.Name, Name.Value.{it,en}, etc.)

### Phase 2 — Batch Create ✅ COMPLETE

**Objective**: Implement `archi3d batch create` command to materialize per-run job lists, initialize the SSOT `tables/generations.csv` registry, and produce per-run manifests.

**Implemented Components**:

1. **Job Identity Helpers** (`src/archi3d/db/generations.py`):
   - `compute_image_set_hash(image_paths)` — Deterministic SHA1 hash of ordered image paths
   - `compute_job_id(product_id, variant, algo, image_set_hash)` — 12-char deterministic job ID
   - `upsert_generations(generations_csv_path, df_new)` — Atomic upsert to generations.csv using (run_id, job_id) keys

2. **Batch Creation Logic** (`src/archi3d/orchestrator/batch.py`):
   - Completely refactored for Phase 2 requirements
   - `create_batch(run_id, algos, paths, ...)` — Main entry point
   - `_select_images_use_up_to_6(row)` — Image selection policy implementation
   - `_apply_filters(items_df, ...)` — Filtering logic (include/exclude/with-gt-only/limit)
   - Uses Phase 0 atomic I/O utilities for safe concurrent access

3. **CLI Integration** (`src/archi3d/cli.py`):
   - `archi3d batch create` command with Phase 2 flags:
     - `--run-id` (optional, auto-generates UTC timestamp if omitted)
     - `--algos` (comma-separated algorithm keys)
     - `--image-policy` (default: "use_up_to_6")
     - `--limit` (max items to process)
     - `--include` / `--exclude` (substring filters on product_id/variant/product_name)
     - `--with-gt-only` (skip items without GT)
     - `--dry-run` (compute summary without writing files)
   - Console output: Summary tables with candidates, enqueued, skipped counts
   - Auto-selects default algorithm if none specified

4. **SSOT Schema** (`tables/generations.csv`):
   - **Columns** (30 total, in order per Phase 2 spec):
     - **Carry-over from parent** (observability): `product_id`, `variant`, `manufacturer`, `product_name`, `category_l1`, `category_l2`, `category_l3`, `description`, `source_n_images`, `source_image_1_path` ... `source_image_6_path`, `gt_object_path`
     - **Batch/job metadata**: `run_id`, `job_id`, `algo`, `algo_version`, `used_n_images`, `used_image_1_path` ... `used_image_6_path`, `image_set_hash`, `status`, `created_at`, `notes`
   - **Key columns**: `(run_id, job_id)` uniquely identify each generation job
   - **Upsert behavior**: Keeps existing rows on conflict (idempotent)
   - **Encoding**: UTF-8-SIG for Excel compatibility
   - **Status**: Phase 2 sets `status="enqueued"` for new jobs

5. **Per-Run Manifest** (`runs/<run_id>/manifest.csv`):
   - Derived from `generations.csv` for jobs with `status=enqueued` and matching `run_id`
   - **Required columns**: `job_id`, `product_id`, `variant`, `algo`, `used_n_images`, `used_image_1_path` ... `used_image_6_path`, `image_set_hash`
   - **Optional columns**: `gt_object_path`, `product_name`, `manufacturer`
   - Provides per-run job list for worker execution

6. **Structured Logging** (`logs/batch_create.log`):
   - JSON-formatted log entries with ISO8601 timestamps
   - Fields: `event`, `timestamp`, `run_id`, `algos`, `image_policy`, `candidates`, `enqueued`, `skipped`, `skip_reasons`, `dry_run`
   - Skip reasons histogram: `no_images`, `filtered_include`, `filtered_exclude`, `with_gt_only`, `duplicate_job`

7. **Test Suite** (`tests/test_phase2_batch_create.py`):
   - 7 comprehensive tests covering:
     - Dry-run mode (no files written, log with dry_run flag)
     - Real write + idempotency (upsert prevents duplicates)
     - Filters and with-gt-only (correct items skipped)
     - Multi-algo job identity (distinct job_ids per algo, same image_set_hash)
     - Path relativity (all paths workspace-relative)
     - Job identity determinism (stable hashes and IDs)
     - Limit parameter (caps items processed)
   - All 7 tests passing

**Key Features**:
- **Deterministic job identity**: SHA1-based job IDs stable across re-runs with same inputs
- **Atomic upserts**: Uses Phase 0 `update_csv_atomic()` with (run_id, job_id) keys
- **Idempotency**: Re-running with same inputs updates existing rows, doesn't create duplicates
- **Flexible filtering**: Include/exclude patterns, GT-only mode, item limit
- **Dry-run mode**: Preview changes without writing files
- **Auto-generated run IDs**: UTC timestamp slugs if not specified
- **Workspace-relative paths**: All paths in CSVs are portable and cross-platform

**CLI Examples**:
```bash
# Basic usage (auto-generates run_id, uses default algo)
archi3d batch create

# Explicit run_id and algorithms
archi3d batch create --run-id "2025-10-20-experiment" --algos tripo3d_v2p5,trellis_single

# With filters
archi3d batch create --run-id "test-run" --algos tripo3d_v2p5 --with-gt-only --limit 10

# Include/exclude patterns
archi3d batch create --include "100001" --algos tripo3d_v2p5
archi3d batch create --exclude "100003" --algos tripo3d_v2p5

# Dry-run (preview only)
archi3d batch create --run-id "test" --algos tripo3d_v2p5 --dry-run
```

**Design Patterns**:
- **SSOT First**: `generations.csv` is the single source of truth; manifest is derived from it
- **Image Selection Policy**: Extensible policy system (currently only `use_up_to_6`)
- **Filtering Order**: include → exclude → with-gt-only → n_images ≥ 1 → limit
- **Job Identity**: `job_id = SHA1(product_id|variant|algo|image_set_hash)[:12]`
- **Image Set Hash**: `SHA1(\n.join(used_image_paths))` for deterministic ordering

**Non-Functional Changes**:
- Completely replaced old token-based batch creation logic
- No changes to Phase 0 or Phase 1 functionality
- All writes use Phase 0 atomic I/O utilities
- Linting/formatting applied (ruff, black)

**Known Constraints**:
- Image policy currently limited to `use_up_to_6` (extensible for future policies)
- Include/exclude filters use substring matching (case-insensitive), not regex or glob
- Filters apply to product_id, variant, and product_name fields only
- Job identity is stable but not backward-compatible with pre-Phase 2 job IDs

### Phase 3 — Run Worker ✅ COMPLETE

**Objective**: Execute generation jobs from tables/generations.csv with robust lifecycle management, resumability, and concurrent execution support.

**Implemented Components**:

1. **Worker Execution Logic** (`src/archi3d/orchestrator/worker.py`):
   - `run_worker(run_id, paths, ...)` — Main entry point for job execution
   - `_execute_job(job_row, ...)` — Per-job execution with lifecycle management
   - `_get_worker_identity()` — Captures worker environment metadata (host, user, GPU, env, commit)
   - `_simulate_dry_run(...)` — Creates synthetic outputs for testing
   - State marker management functions for resumability

2. **Phase 3 Adapter Contract** (`src/archi3d/adapters/base.py`):
   - `GenerationRequest` — Input dataclass for adapter execution (job_id, product_id, variant, algo, used_images, out_dir, workspace, extra)
   - `GenerationResult` — Output dataclass from adapters (success, generated_glb, previews, algo_version, pricing, raw_metadata)
   - Maintains backward compatibility with Phase 0-2 Token/ExecResult types

3. **PathResolver Extensions** (`src/archi3d/config/paths.py`):
   - `outputs_dir(run_id, job_id=...)` — Per-job output directories under `runs/<run_id>/outputs/<job_id>/`
   - `state_dir(run_id)` — State marker directory for resumability
   - `state_lock_path(run_id, job_id)` — Per-job FileLock paths for safe concurrent access

4. **CLI Integration** (`src/archi3d/cli.py`):
   - `archi3d run worker` command with Phase 3 flags:
     - `--run-id` (required) — Run identifier
     - `--jobs` (optional) — Filter job_id by substring
     - `--only-status` (default: "enqueued") — Comma-separated statuses to process
     - `--max-parallel` (default: 1) — Concurrent worker threads
     - `--adapter` (optional) — Force specific adapter for debugging
     - `--dry-run` — Simulate execution without calling adapters
     - `--fail-fast` — Stop on first failure
   - Rich console output with execution summary and file paths

5. **Job Lifecycle State Machine**:
   - **enqueued** → **running** → **completed** / **failed**
   - State transitions protected by per-job FileLock
   - State markers in `runs/<run_id>/state/`:
     - `<job_id>.inprogress` — Job currently executing
     - `<job_id>.completed` — Job finished successfully
     - `<job_id>.failed` — Job failed
     - `<job_id>.error.txt` — Full error details for failed jobs

6. **SSOT Updates** (`tables/generations.csv`):
   - **Phase 3 fields added**:
     - **Execution metadata**: `status` (running/completed/failed), `generation_start`, `generation_end`, `generation_duration_s`
     - **Worker identity**: `worker_host`, `worker_user`, `worker_gpu`, `worker_env`, `worker_commit`
     - **Outputs**: `gen_object_path`, `preview_1_path`, `preview_2_path`, `preview_3_path`, `algo_version`
     - **Costs**: `unit_price_usd`, `price_source`
     - **Errors**: `error_msg` (truncated to 2000 chars)
   - All paths are workspace-relative (POSIX format with forward slashes)
   - Atomic upserts via Phase 0 `update_csv_atomic()` with (run_id, job_id) keys

7. **Output Artifacts**:
   - Per-job outputs under `runs/<run_id>/outputs/<job_id>/`:
     - `generated.glb` — Generated 3D model (or adapter-specific name)
     - `preview_1.png`, `preview_2.png`, `preview_3.png` — Optional preview images
     - `metadata.json` — Adapter raw metadata (if provided)

8. **Structured Logging** (`logs/worker.log`):
   - JSON-formatted log entries with ISO8601 timestamps
   - Event types:
     - `worker_started` — Execution start with filters and config
     - `job_completed` — Per-job success with duration
     - `job_failed` — Per-job failure with error summary
     - `job_crashed` — Unexpected worker crashes
     - `worker_summary` — Final counts (processed, completed, failed, skipped, avg_duration_s)

9. **Concurrency Support (Rearchitected)**:
   - **Batch Upsert Model**: The core change is the move away from concurrent per-job CSV writes.
   - **Thread Safety**: A thread pool (`--max-parallel`) is still used for execution. State markers (`<job_id>.inprogress`) prevent multiple workers from picking up the same job.
   - **Race Condition Eliminated**: Since only one process writes the final batch of results to `generations.csv` (protected by `FileLock`), the risk of `NaN` corruption from concurrent pandas merges is completely eliminated.
   - **Resumability**: State markers (`.completed`, `.failed`) ensure that re-running a worker safely skips already-processed jobs.

10. **Dry-Run Mode**:
    - Simulates execution without calling adapters
    - Creates placeholder `generated.glb` and `preview_*.png` files
    - Updates CSV with `algo_version="dry-run"`
    - Useful for testing workflow and timing

11. **Test Suite** (`tests/test_phase3_run_worker.py`):
    - **7/7 tests passing (ALL FIXED)**.
    - Tests were updated to validate the new batch upsert logic and to account for timing changes. The suite now fully covers:
        - Dry-run success with synthetic outputs.
        - Real run with failure validation.
        - Resumability (completed jobs skipped on re-run).
        - Concurrency with thread pools.
        - Path relativity and idempotency.
        - Job filtering by substring.
        - Fail-fast mode.

**Key Features**:
- **Resumable execution**: State markers prevent duplicate work after interruption
- **Concurrent execution**: Thread pool with configurable parallelism
- **Worker observability**: Captures host, user, GPU, environment, commit for troubleshooting
- **Atomic updates**: Safe concurrent access to SSOT via FileLock and atomic I/O
- **Flexible filtering**: Process specific jobs by status, job_id pattern, or adapter
- **Dry-run testing**: Validate workflow without external API calls
- **Cost tracking**: Reads unit prices from adapters.yaml and tracks estimated costs
- **Error handling**: Full error details in error.txt, truncated summary in CSV

**CLI Examples**:
```bash
# Basic usage (process enqueued jobs with default parallelism)
archi3d run worker --run-id "2025-10-20-experiment"

# Dry-run mode (test without calling adapters)
archi3d run worker --run-id "test-run" --dry-run

# Concurrent execution with 4 workers
archi3d run worker --run-id "prod-run" --max-parallel 4

# Resume stuck "running" jobs
archi3d run worker --run-id "interrupted-run" --only-status running

# Process specific jobs by substring
archi3d run worker --run-id "test-run" --jobs "59ad"

# Force specific adapter (debug mode)
archi3d run worker --run-id "test-run" --adapter test_algo_1

# Stop on first failure
archi3d run worker --run-id "test-run" --fail-fast
```

**Design Patterns**:
- **State Machine**: Clear job lifecycle with atomic transitions
- **State Markers**: Resume-friendly design with filesystem-based state tracking
- **Worker Identity**: Full observability for debugging distributed execution
- **Fail-Safe Defaults**: Conservative settings (serial execution, process enqueued only)
- **Extensible Filtering**: Substring matching on job_id, status filtering, adapter override
- **Cost Awareness**: Automatic cost tracking from configuration

**Non-Functional Changes**:
- Completely replaced old token-based worker logic
- No changes to Phase 0, 1, or 2 functionality
- All writes use Phase 0 atomic I/O utilities
- Linting/formatting applied (ruff, black)

**Known Constraints**:
- Real adapter execution not implemented (placeholder creates minimal GLB).
- Heartbeat mechanism for stale detection implemented but not actively updated.
- Thread-based concurrency (process-based left as TODO).
- No timeout mechanism for adapter execution (left as TODO).
- Job filtering uses simple substring matching (not regex/glob).

**Next Phase**: Phase 4+ (metrics computation, FScore/VFScore integration)

### Phase 4 — Consolidate ✅ COMPLETE

**Objective**: Reconcile tables/generations.csv with on-disk artifacts and state markers to ensure SSOT consistency, deduplicate rows, and fill missing metadata.

**Implemented Components**:

1. **Consolidation Logic** (`src/archi3d/orchestrator/consolidate.py`):
   - `consolidate(run_id, paths, ...)` — Main entry point for reconciliation
   - `_gather_evidence(row, run_id, state_dir, outputs_dir, paths)` — Collects evidence from disk (markers, artifacts, error files)
   - `_determine_desired_status(evidence, csv_status)` — Applies truth table to determine correct status
   - `_reconcile_row(row, evidence, desired_status, ...)` — Reconciles single row with evidence
   - `_merge_duplicate_rows(rows)` — Merges duplicate (run_id, job_id) rows by keeping most complete information
   - `_consolidate_run(run_id, paths, ...)` — Per-run reconciliation orchestrator

2. **CLI Integration** (`src/archi3d/cli.py`):
   - `archi3d consolidate` command with Phase 4 flags:
     - `--run-id` (required) — Run identifier
     - `--dry-run` (default: False) — Compute changes without writing CSV
     - `--strict` (default: False) — Exit with error on any conflict
     - `--only-status` (optional) — Comma-separated statuses to process
     - `--fix-status` (default: True) — Apply status downgrades for missing outputs
     - `--max-rows` (optional) — Cap on rows to process for safety
   - Rich console output with summary tables and status histograms

3. **Reconciliation Rules**:
   - **Status Truth Table** (priority order):
     1. `.completed` marker + `generated.glb` exists → `status=completed`
     2. `.failed` marker exists → `status=failed`
     3. `.inprogress` marker + heartbeat fresh (<10 min) → `status=running`
     4. No markers/artifacts → keep CSV status (default `enqueued`)
   - **Downgrade Logic**: CSV says `completed` but `generated.glb` missing → downgrade to `failed` with error_msg (if `--fix-status`)
   - **Timestamp Filling**: Best-effort synthesis from marker/artifact mtimes
   - **Path Normalization**: Fills `gen_object_path`, `preview_*_path` with workspace-relative paths
   - **Error Message Filling**: Reads first ~2000 chars from `error.txt` if `error_msg` empty

4. **Duplicate Handling**:
   - Detects duplicate (run_id, job_id) rows in CSV
   - Merges duplicates using smart precedence:
     - Prefers row with highest status precedence (completed > failed > running > enqueued)
     - Column-wise: keeps non-empty/non-NaN values
     - Result: single merged row with union of non-empty fields
   - Special upsert path: removes all run_id rows, then inserts deduplicated data (avoids pandas merge issues with duplicates)

5. **Structured Logging** (`logs/metrics.log`):
   - JSON-formatted summary with counters:
     - `considered`, `upsert_inserted`, `upsert_updated`, `unchanged`
     - `conflicts_resolved`, `marker_mismatches_fixed`, `downgraded_missing_output`
     - `status_histogram_before`, `status_histogram_after`
   - Includes `dry_run` flag for audit trail

6. **Test Suite** (`tests/test_phase4_consolidate.py`):
   - 7 comprehensive tests covering all requirements:
     1. Happy path (completed jobs with full artifacts, minimal changes)
     2. Downgrade missing output (CSV says completed but GLB missing)
     3. Merge duplicates (duplicate rows merged to single row with union of fields)
     4. Heartbeat stale (inprogress marker >10 min old, keeps running status per spec)
     5. Dry-run mode (no CSV writes, log includes dry_run flag)
     6. Idempotency (re-running yields minimal/no updates after first run)
     7. No CSV exists (handles missing generations.csv gracefully)
   - All 7 tests passing

**Key Features**:
- **Idempotent Reconciliation**: Re-running without changes yields `upsert_updated≈0`
- **Conflict Resolution**: Merges duplicate rows with smart precedence rules
- **Status Validation**: Downgrades incorrect statuses based on on-disk evidence
- **Metadata Filling**: Synthesizes missing timestamps, paths, error messages from artifacts
- **Atomic Updates**: Uses Phase 0 `update_csv_atomic()` with FileLock for safety
- **Dry-Run Mode**: Preview changes without modifying CSV
- **Flexible Filtering**: Process specific statuses, cap rows for safety
- **Heartbeat Detection**: Identifies stale `inprogress` markers (>10 min old)

**CLI Examples**:
```bash
# Basic usage (reconcile all jobs for a run)
archi3d consolidate --run-id "2025-10-20-experiment"

# Dry-run mode (preview changes without writes)
archi3d consolidate --run-id "test-run" --dry-run

# Process only specific statuses
archi3d consolidate --run-id "prod-run" --only-status "completed,failed"

# Disable status downgrades
archi3d consolidate --run-id "test-run" --fix-status=false

# Strict mode (fail on any conflict)
archi3d consolidate --run-id "test-run" --strict

# Safety cap (process max 100 rows)
archi3d consolidate --run-id "large-run" --max-rows 100
```

**Design Patterns**:
- **Evidence-Based Reconciliation**: Gathers disk evidence before making decisions
- **Truth Table Logic**: Clear precedence rules for status determination
- **Smart Merging**: Column-wise union for duplicate row resolution
- **Workspace-Relative Paths**: All stored paths use POSIX format relative to workspace
- **Atomic Deduplication**: Special upsert path for handling CSV duplicates cleanly

**Non-Functional Changes**:
- No changes to Phases 0-3 functionality
- All writes use Phase 0 atomic I/O utilities
- Linting/formatting applied (ruff, black)
- Mypy type checking passed (pandas stub warnings ignored)

**Known Constraints**:
- Heartbeat freshness threshold: 10 minutes (configurable via constant)
- Error message truncation: 2000 characters (when reading from error.txt)
- Stale heartbeat behavior: keeps `running` status (documented as "leave as is")
- Status filtering uses simple string matching (not regex/glob)

**Next Phase**: Phase 5 (FScore geometry metrics computation)

### Phase 5 — Compute FScore (Geometry Metrics) ✅ COMPLETE

**Objective**: Compute geometry-based quality metrics (F-score, precision, recall, Chamfer-L2, alignment transforms) for completed jobs with ground truth objects, and upsert standardized metric columns into the SSOT `tables/generations.csv`.

**Implemented Components**:

1. **FScore Adapter Layer** (`src/archi3d/metrics/fscore_adapter.py`):
   - `FScoreRequest` — Input dataclass for FScore evaluation (gt_path, cand_path, n_points, out_dir, timeout_s)
   - `FScoreResponse` — Output dataclass with normalized results (ok, payload, tool_version, config_hash, runtime_s, error)
   - `evaluate_fscore(req)` — Main entry point with dual resolution:
     1. Tries Python import API (`from fscore.evaluator import evaluate_one`)
     2. Falls back to CLI invocation (`python -m fscore ...`)
   - `_normalize_payload(raw)` — Normalizes tool output into canonical schema
   - `_try_import_api(req)` — Attempts Python API with exception handling
   - `_try_cli_invocation(req)` — Subprocess fallback with timeout and JSON parsing

2. **Main Computation Logic** (`src/archi3d/metrics/fscore.py`):
   - `compute_fscore(run_id, jobs, only_status, ...)` — Main entry point for metrics computation
   - `_is_eligible(row, run_id, only_status, with_gt_only, redo, jobs_filter, paths)` — Eligibility filtering with skip reasons
   - `_job_matches_filter(job_id, filter_pattern)` — Job ID filtering (substring/glob/regex)
   - `_process_job(row, n_points, timeout_s, paths, dry_run)` — Per-job execution and artifact creation
   - Thread pool support with `ThreadPoolExecutor` for parallel execution
   - Atomic CSV upserts via Phase 0 `update_csv_atomic()` with (run_id, job_id) keys
   - Structured logging to `logs/metrics.log` with event summaries

3. **CLI Integration** (`src/archi3d/cli.py`):
   - Added `compute_app` Typer group for metrics computation commands
   - `archi3d compute fscore` command with Phase 5 flags:
     - `--run-id` (required) — Run identifier
     - `--jobs` (optional) — Job ID filter (glob/regex/substring)
     - `--only-status` (default: "completed") — Comma-separated statuses to process
     - `--with-gt-only` (default: True) — Require non-empty GT object path
     - `--redo` (default: False) — Force recomputation even if metrics already present
     - `--n-points` (default: 100000) — Poisson disk samples per mesh
     - `--timeout-s` (optional) — Per-job timeout in seconds
     - `--max-parallel` (default: 1) — Maximum concurrent workers
     - `--dry-run` (default: False) — Preview selection without running evaluator
   - Rich console output with summary tables and skip reasons histogram

4. **SSOT Schema Extensions** (`tables/generations.csv` — 24 new columns):
   - **Core metrics**: `fscore` (float), `precision` (float), `recall` (float), `chamfer_l2` (float)
   - **Alignment**: `fscore_scale` (float), `fscore_rot_w/x/y/z` (float), `fscore_tx/y/z` (float)
   - **Distance statistics**: `fscore_dist_mean/median/p95/p99/max` (float)
   - **Metadata**: `fscore_n_points` (int), `fscore_runtime_s` (float), `fscore_tool_version` (str), `fscore_config_hash` (str)
   - **Status tracking**: `fscore_status` (enum: ok/error/skipped), `fscore_error` (str, truncated to 2000 chars)
   - All numeric fields are nullable (None/NaN for missing data)
   - Upserted atomically via Phase 0 utilities

5. **Per-Job Artifacts**:
   - Output directory: `runs/<run_id>/metrics/fscore/<job_id>/`
   - `result.json` — Canonical machine-readable payload with all metrics
   - Directory created automatically before writing (handles concurrent access)
   - Idempotent: not overwritten unless `--redo` is set

6. **Canonical Payload Schema** (JSON):
   ```json
   {
     "fscore": <float>, "precision": <float>, "recall": <float>,
     "chamfer_l2": <float>, "n_points": <int>,
     "alignment": {
       "scale": <float>,
       "rotation_quat": {"w": <float>, "x": <float>, "y": <float>, "z": <float>},
       "translation": {"x": <float>, "y": <float>, "z": <float>}
     },
     "dist_stats": {
       "mean": <float>, "median": <float>, "p95": <float>, "p99": <float>, "max": <float>
     },
     "mesh_meta": {
       "gt_vertices": <int>, "gt_triangles": <int>,
       "pred_vertices": <int>, "pred_triangles": <int>
     }
   }
   ```

7. **Eligibility Rules**:
   - `run_id` must match target run
   - `status` must be in `--only-status` list (default: `completed`)
   - `gen_object_path` must exist on disk
   - `gt_object_path` must exist on disk if `--with-gt-only` (default: True)
   - Job ID must match `--jobs` filter (if provided)
   - Skip if `fscore_status="ok"` unless `--redo` is set
   - Emits structured skip reasons: `wrong_run_id`, `status_not_in_filter`, `job_id_not_matching_filter`, `already_computed`, `missing_gen_object_path`, `missing_gt_object_path`, `gen_object_not_found_on_disk`, `gt_object_not_found_on_disk`

8. **Structured Logging** (`logs/metrics.log`):
   - JSON-formatted event summary:
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
       "dry_run": <bool>,
       "skip_reasons": {...}
     }
     ```

9. **Test Suite** (`tests/test_phase5_compute_fscore.py`):
   - 9 comprehensive tests covering all requirements:
     1. Happy path dry-run (selection without evaluator calls)
     2. Happy path real computation (full workflow with mock adapter)
     3. Missing GT object (proper error handling)
     4. Idempotency without redo (skips already computed jobs)
     5. Redo mode (force recomputation)
     6. Concurrency multiple jobs (parallel processing with ThreadPoolExecutor)
     7. Timeout handling (evaluator timeout → error status)
     8. Job filter matching (substring/glob/regex filtering)
     9. Status filtering (process only specified statuses)
   - All 9 tests passing
   - Full test suite: 59/59 tests passing (21+8+7+7+7+9 across Phases 0-5)

**Key Features**:
- **Dual Adapter Integration**: Tries Python import API first, falls back to CLI invocation
- **Idempotent by Default**: Skips jobs with `fscore_status="ok"` unless `--redo` is set
- **Atomic CSV Updates**: Uses Phase 0 `update_csv_atomic()` with FileLock for concurrent safety
- **Parallel Execution**: ThreadPoolExecutor with configurable `--max-parallel` flag
- **Flexible Filtering**: Job ID patterns (substring/glob/regex), status lists, GT requirement
- **Timeout Support**: Per-job timeout with `--timeout-s` flag (useful for large meshes)
- **Dry-Run Mode**: Preview eligible jobs without evaluator calls
- **Comprehensive Metrics**: 24 new columns covering geometry similarity, alignment, and distance statistics
- **Structured Logging**: JSON event summaries to `logs/metrics.log` with counters and skip reasons
- **Graceful Error Handling**: Invalid inputs → `fscore_status="error"` with descriptive `fscore_error`

**CLI Examples**:
```bash
# Basic usage (process completed jobs with GT objects)
archi3d compute fscore --run-id "2025-10-20-experiment"

# Dry-run to preview selection
archi3d compute fscore --run-id "test-run" --dry-run

# Parallel execution with 4 workers
archi3d compute fscore --run-id "prod-run" --max-parallel 4

# Recompute specific jobs matching a pattern
archi3d compute fscore --run-id "test-run" --jobs "product_123*" --redo

# Custom sampling density and timeout
archi3d compute fscore --run-id "large-meshes" --n-points 200000 --timeout-s 300

# Process failed jobs (for diagnostics)
archi3d compute fscore --run-id "test-run" --only-status "failed" --with-gt-only=false
```

**Design Patterns**:
- **Adapter Abstraction**: Clean separation between orchestration and tool integration
- **Evidence-Based Eligibility**: Multi-criteria filtering with structured skip reasons
- **Job ID Filtering**: Supports substring, glob (`*` wildcard), and regex (`re:` prefix) patterns
- **Canonical Payload Schema**: Normalized JSON schema for cross-tool compatibility
- **Workspace-Relative Paths**: All artifact paths use POSIX format relative to workspace
- **Directory Auto-Creation**: Output directories created automatically before writes
- **Error Truncation**: Error messages capped at 2000 chars with full details in result.json

**Non-Functional Changes**:
- No changes to Phases 0-4 functionality
- All writes use Phase 0 atomic I/O utilities
- Linting/formatting applied (ruff, black)
- Type checking passed (mypy with pandas stub warnings)
- Added `compute_app` Typer group to CLI (new subcommand namespace)

**Known Constraints**:
- FScore tool must be available via Python import or CLI (`python -m fscore`)
- Payload normalization assumes specific JSON schema from FScore tool
- Job ID filtering uses simple patterns (substring, glob with `*`, or regex with `re:` prefix)
- Status filtering uses simple string matching (not regex/glob)
- Distance statistics fields are optional (may be None if tool doesn't provide)
- Mesh metadata fields are optional (may be None if tool doesn't provide)
- Error message truncation: 2000 characters (full error in result.json)
- Concurrency is thread-based (not process-based)

---

### Phase 6 — Compute VFScore (Visual Fidelity Metrics) ✅ COMPLETE

**Objective**: Implement `archi3d compute vfscore` command to render generated models under standardized Cycles setup and score visual fidelity against reference photos using LLM-based scoring. Upsert standardized VFScore columns into SSOT `tables/generations.csv` and persist per-job artifacts.

**Implemented Components**:

1. **VFScore Adapter Layer** (`src/archi3d/metrics/vfscore_adapter.py`):
   - `VFScoreRequest` dataclass: Input specification (cand_glb, ref_images, out_dir, repeats, timeout_s, workspace)
   - `VFScoreResponse` dataclass: Normalized result (ok, payload, tool_version, config_hash, render_runtime_s, scoring_runtime_s, error)
   - `evaluate_vfscore(req)` — Main entry point with dual integration:
     1. Try Python import API (`from vfscore.evaluator import evaluate_visual_fidelity`)
     2. Fallback to CLI invocation (`python -m vfscore --cand-glb ... --ref-images ...`)
   - `_normalize_payload(raw)` — Normalizes tool output into canonical schema
   - Input validation: Checks candidate GLB and reference images exist on disk
   - Automatic filtering: Uses only existing reference images from the provided list

2. **Main Computation Logic** (`src/archi3d/metrics/vfscore.py`):
   - `compute_vfscore(run_id, jobs, only_status, use_images_from, repeats, redo, max_parallel, timeout_s, dry_run)` — Main entry point
   - `_get_reference_images(row, use_images_from, paths)` — Extracts reference images from used_image_* or source_image_* columns
   - `_is_eligible(row, run_id, only_status, use_images_from, redo, jobs_filter, paths)` — Multi-criteria eligibility filtering
   - `_process_job(row, repeats, use_images_from, timeout_s, paths, dry_run)` — Per-job processing workflow
   - Parallel execution: ThreadPoolExecutor with configurable max_parallel
   - Per-job artifacts: result.json, config.json, renders/, rationales/
   - Atomic CSV upserts: Uses Phase 0 `update_csv_atomic()` with FileLock

3. **CLI Integration** (`src/archi3d/cli.py`):
   - `archi3d compute vfscore` command registered under `compute_app`
   - Flags: `--run-id` (required), `--jobs`, `--only-status`, `--use-images-from`, `--repeats`, `--redo`, `--max-parallel`, `--timeout-s`, `--dry-run`
   - Rich output: Panel with parameters, Table with summary (selected/processed/ok/error/skipped), skip reasons table
   - Displays: generations CSV path, metrics artifacts path, metrics log path

4. **VFScore Columns in SSOT** (`tables/generations.csv`):
   - 15 new columns added (all nullable):
     - **Core scores**: `vfscore_overall` (int 0-100), `vf_finish` (int), `vf_texture_identity` (int), `vf_texture_scale_placement` (int)
     - **Statistics**: `vf_repeats_n` (int), `vf_iqr` (float), `vf_std` (float)
     - **Provenance**: `vf_llm_model` (str), `vf_rubric_json` (str, compact JSON), `vf_config_hash` (str), `vf_rationales_dir` (str, workspace-relative path)
     - **Performance**: `vf_render_runtime_s` (float), `vf_scoring_runtime_s` (float)
     - **Status**: `vf_status` (enum: ok/error/skipped), `vf_error` (str, truncated 2000 chars)
   - **Key columns**: `(run_id, job_id)` for upserts
   - **Encoding**: UTF-8-SIG for Excel compatibility

5. **Per-Job Artifacts** (`runs/<run_id>/metrics/vfscore/<job_id>/`):
   - `result.json` — Canonical payload with all VFScore metrics:
     ```json
     {
       "vfscore_overall_median": <int>,
       "vf_subscores_median": {"finish": <int>, "texture_identity": <int>, "texture_scale_placement": <int>},
       "repeats_n": <int>,
       "scores_all": [<int>, ...],
       "subscores_all": [{"finish": <int>, ...}, ...],
       "iqr": <float>,
       "std": <float>,
       "llm_model": "<string>",
       "rubric_weights": {"finish": <float>, "texture_identity": <float>, "texture_scale_placement": <float>},
       "render_settings": {"engine": "cycles", "hdri": "<rel or alias>", "camera": "<preset>", "seed": <int>}
     }
     ```
   - `config.json` — Effective VFScore configuration (render settings, rubric weights, LLM model, repeats)
   - `renders/` — Standardized Cycles renders used for scoring (created by VFScore tool)
   - `rationales/` — Text files with LLM explanations per scoring repeat (created by VFScore tool)

6. **Eligibility Filtering** (`_is_eligible()`):
   A job is eligible if ALL criteria are met:
   - `run_id` matches
   - `status` ∈ `--only-status` (default: `completed`)
   - `gen_object_path` exists and is non-empty on disk
   - At least 1 reference image exists on disk from chosen source (`used_image_*` or `source_image_*`)
   - If `--jobs` provided, job_id matches (substring/glob/regex)
   - If not `--redo`, skip rows with `vf_status="ok"` or non-null `vfscore_overall`
   - Emits structured skip reasons: `wrong_run_id`, `status_not_in_filter`, `job_id_not_matching_filter`, `already_computed`, `missing_gen_object_path`, `gen_object_not_found_on_disk`, `no_reference_images_found`

7. **Structured Logging** (`logs/metrics.log`):
   - JSON-formatted event summary:
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
       "dry_run": <bool>,
       "skip_reasons": {...}
     }
     ```

8. **Test Suite** (`tests/test_phase6_compute_vfscore.py`):
   - 9 comprehensive tests covering all requirements:
     1. Happy path dry-run (selection without evaluator calls)
     2. Happy path real computation (full workflow with mock adapter)
     3. Missing reference images (proper skip handling)
     4. Idempotency without redo (skips already computed jobs)
     5. Redo mode (force recomputation)
     6. Concurrency (parallel processing with ThreadPoolExecutor)
     7. Timeout handling (evaluator timeout → error status)
     8. Image source selection (used_image_* vs source_image_* filtering)
     9. Adapter error handling (non-timeout errors)
   - All 9 tests passing
   - Full test suite: **68/68 tests passing** (21+8+7+7+7+9+9 across Phases 0-6)

**Key Features**:
- **Dual Adapter Integration**: Tries Python import API first, falls back to CLI invocation
- **Reference Image Flexibility**: Supports both `used_image_*` (default) and `source_image_*` columns via `--use-images-from` flag
- **Idempotent by Default**: Skips jobs with `vf_status="ok"` unless `--redo` is set
- **Atomic CSV Updates**: Uses Phase 0 `update_csv_atomic()` with FileLock for concurrent safety
- **Parallel Execution**: ThreadPoolExecutor with configurable `--max-parallel` flag
- **Flexible Filtering**: Job ID patterns (substring/glob/regex), status lists
- **LLM Scoring Repeats**: Configurable `--repeats` for scoring consistency (default: 3)
- **Timeout Support**: Per-job timeout with `--timeout-s` flag (useful for large models/slow rendering)
- **Dry-Run Mode**: Preview eligible jobs without evaluator calls
- **Comprehensive Artifacts**: result.json, config.json, renders, rationales all persisted per job
- **Structured Logging**: JSON event summaries to `logs/metrics.log` with counters, runtimes, and skip reasons
- **Graceful Error Handling**: Missing inputs or evaluator errors → `vf_status="error"` with descriptive `vf_error`

**CLI Examples**:
```bash
# Basic usage (process completed jobs with reference images)
archi3d compute vfscore --run-id "2025-10-20-experiment"

# Dry-run to preview selection
archi3d compute vfscore --run-id "test-run" --dry-run

# Use source images instead of used images
archi3d compute vfscore --run-id "test-run" --use-images-from source

# Increase LLM repeats for more stable scores
archi3d compute vfscore --run-id "test-run" --repeats 5

# Parallel execution (renders + scoring are CPU/GPU intensive)
archi3d compute vfscore --run-id "prod-run" --max-parallel 2

# Recompute specific jobs matching a pattern
archi3d compute vfscore --run-id "test-run" --jobs "product_123*" --redo

# Custom timeout for slow renders
archi3d compute vfscore --run-id "large-models" --timeout-s 600
```

**Design Patterns**:
- **Adapter Abstraction**: Clean separation between orchestration and tool integration (mirrors Phase 5)
- **Evidence-Based Eligibility**: Multi-criteria filtering with structured skip reasons
- **Reference Image Discovery**: Dynamically extracts images from CSV columns based on `--use-images-from` parameter
- **Canonical Payload Schema**: Normalized JSON schema for cross-tool compatibility
- **Workspace-Relative Paths**: All artifact paths use POSIX format relative to workspace
- **Directory Auto-Creation**: Output directories created automatically before writes
- **Error Truncation**: Error messages capped at 2000 chars with full details in result.json
- **Runtime Decomposition**: Separates render and scoring runtimes for performance analysis

**Non-Functional Changes**:
- No changes to Phases 0-5 functionality
- All writes use Phase 0 atomic I/O utilities
- Linting/formatting applied (ruff, black)
- Added to `compute_app` Typer group (alongside `fscore` command)

**Known Constraints**:
- VFScore tool must be available via Python import or CLI (`python -m vfscore`)
- Payload normalization assumes specific JSON schema from VFScore tool
- Reference image columns must follow `{used|source}_image_{a-f}` naming convention
- Job ID filtering uses simple patterns (substring, glob with `*`, or regex with `re:` prefix)
- Status filtering uses simple string matching (not regex/glob)
- Rubric weights stored as compact JSON string in CSV (parse required for analysis)
- Error message truncation: 2000 characters (full error in result.json)
- Concurrency is thread-based (not process-based)
- Rendering and scoring are performed by external VFScore tool (not within archi3D)

**Next Phase**: Final reporting enhancements, binary wheel packaging (FScore/VFScore distribution)

---

### Bug Fixes & Improvements (2025-11-23)

**CSV Metadata Data Loss Investigation & Fix**

**Root Cause**: Worker CSV updates were lost due to OneDrive sync conflict, causing consolidate to reconstruct timestamps from state markers (which resulted in near-zero durations since both start/end were set to completion time).

**Symptoms**:
- 14 completed jobs showing ~0.000001s duration instead of actual 24-183 seconds
- `generation_start` and `generation_end` timestamps nearly identical (differing by microseconds)
- Price columns empty for affected jobs
- Completed job (`b89a7aa4150b`) retaining error_msg from previous failed attempt

**Fixes Applied**:

1. **Data Restoration** (`scripts/fix_nov22_timestamps.py`):
   - Created one-time fix script to restore correct timestamps from worker.log
   - Reconstructs `generation_start`, `generation_end`, `generation_duration_s` from log entries
   - Fills missing prices from `adapters.yaml` configuration
   - Clears `error_msg` for completed jobs
   - Usage: `python scripts/fix_nov22_timestamps.py` (dry-run) or `--apply` to write

2. **Consolidate Logic Improvement** (`src/archi3d/orchestrator/consolidate.py`):
   - Modified `_reconcile_row()` to preserve worker-written timestamps when duration > 1 second
   - Previously: Always filled timestamps from markers and recomputed duration
   - Now: Only fills timestamps/recomputes duration if existing duration < 1 second (indicating marker-derived values)
   - Prevents consolidate from overwriting valid worker data with marker-derived estimates

3. **Worker Enhancements** (previously implemented):
   - Added `--redo` flag to clear state markers and retry failed/completed jobs
   - Added `.env` hot-reload (`_reload_dotenv()`) before each API call for API key changes without restart

**Verification**:
- All 68 tests passing after consolidate fix
- Data restored: Duration range now 24-183 seconds (correct)
- All 15 jobs have prices filled
- No completed jobs with error_msg

**Prevention**:
- Consolidate now preserves valid worker data (duration > 1s)
- Worker logs capture true execution metrics even if CSV updates are lost
- Worker log can be used for data recovery if needed

---

### Additional Fixes (2025-11-23)

**1. `--redo` Flag Behavior Fix** (`src/archi3d/orchestrator/worker.py`):
- **Issue**: `--redo` only cleared state markers but didn't include completed/failed jobs in status filter
- **Problem**: With default `--only-status=enqueued`, completed jobs were filtered out BEFORE markers were cleared
- **Fix**: When `--redo` is set, auto-expand status filter to include `completed` and `failed`
- **Usage**: `archi3d run worker --run-id "..." --redo` now retries all completed/failed jobs

**2. Batch Create Status Overwrite Bug** (`src/archi3d/orchestrator/batch.py`):
- **Issue**: Running `batch create` twice for same run_id would reset job status to "enqueued"
- **Root Cause**: `update_csv_atomic()` overwrites ALL columns including status
- **Fix**: Load existing jobs before creating records, skip jobs that already exist
- **Result**: Re-running batch create is now safe - existing jobs are preserved with `already_exists` skip reason

**3. Redundant Price Column Removal**:
- **Issue**: Both `unit_price_usd` and `estimated_cost_usd` columns existed with identical values
- **Fix**: Removed `estimated_cost_usd` from:
  - `worker.py` upsert data
  - `adapters/base.py` GenerationResult dataclass
  - `scripts/fix_nov22_timestamps.py`
  - `tables/generations.csv` (existing data)
- **Result**: Single `unit_price_usd` column now used for pricing

**Verification**:
- All 68 tests passing
- CSV now has 46 columns (was 47)

---

### FScore Integration Enhancement (2025-11-25) ✅ COMPLETE

**Objective**: Add missing logging and visualization features to FScore integration for better debugging and HTML report preparation.

**Issues Identified**:
1. **Missing Console Logs**: FScore's detailed alignment/timing logs weren't captured, only Open3D warnings visible
2. **Missing Visualization**: Original FScore saves comparison `.glb` file (GT gray, prediction red) for visual inspection - needed for HTML report
3. **Missing Debug Log**: Need detailed `alignment_log.txt` file with timing/alignment details for troubleshooting

**Open3D Warnings Explanation**:
- `"Too few correspondences after mutual filter"`: During RANSAC feature matching, very few point pairs pass geometric consistency checks. Open3D falls back to using all correspondences. This is harmless - alignment still succeeds.
- `"Read PNG failed"`: Open3D attempted to read a texture/image file that's corrupted or in an unexpected format. Unrelated to FScore computation.

**Changes Made**:

1. **FScore `evaluate_one` Enhancement** (`FScore/src/fscore/evaluator.py`):
   - Added comparison visualization GLB export (GT in gray, aligned prediction in red)
   - Added detailed console logging with `logger.info()` calls:
     - Mesh loading progress (vertices/triangles counts)
     - Pre-alignment stage (RANSAC/PCA method)
     - ICP refinement stage
     - Final metric summary with timing
   - Added `alignment_log` to return payload (prealign method, scale, RANSAC/PCA fitness, ICP metrics)
   - Added `timing` to return payload (breakdown: load, prealign, ICP, fscore, total)
   - Added `visualization_path` to return payload (path to comparison GLB)
   - Added `version` and `config_hash` to return payload
   - Helper function `_rotation_matrix_to_quaternion()` for transform extraction

2. **FScore Package Update** (`FScore/src/fscore/__init__.py`):
   - Exported `evaluate_one` function in `__all__` for public API

3. **Adapter Update** (`src/archi3d/metrics/fscore_adapter.py`):
   - Added `visualization_path` field to `FScoreResponse` dataclass
   - Updated `_try_import_api()` to extract and return `visualization_path`
   - Updated `_try_cli_invocation()` to extract and return `visualization_path`
   - **CRITICAL FIX**: Updated `_normalize_payload()` to pass through new fields:
     - `alignment_log` (prealign method, scale, RANSAC/PCA/ICP fitness/RMSE)
     - `timing` (load, prealign, ICP, fscore, total timings)
     - `version` (FScore tool version)
     - `config_hash` (configuration fingerprint)
     - `visualization_path` (path to comparison GLB)
   - Previously these fields were being stripped during normalization, causing missing artifacts

4. **Computation Logic Enhancement** (`src/archi3d/metrics/fscore.py`):
   - Added `_configure_fscore_logging()` function to configure FScore's logger for console output
     - Enables FScore's logger.info() messages to appear in console
     - Adds indentation for visual grouping
     - Prevents duplicate logging via propagate=False
   - Added logging configuration call at start of `compute_fscore()` function
   - Added detailed alignment log writer in `_process_job()`:
     - Saves `alignment_log.txt` in artifact directory with:
       - Timing breakdown (load, prealign, ICP, fscore computation)
       - Alignment details (method, scale, RANSAC/PCA/ICP fitness/RMSE)
       - Mesh metadata (vertex/triangle counts for GT and prediction)
       - Final metrics (F-score, precision, recall, Chamfer L2)
   - Added visualization path logging when present
   - All data written to `runs/<run_id>/metrics/fscore/<job_id>/`:
     - `result.json` — Complete payload with all metrics and new fields
     - `alignment_log.txt` — Human-readable debug log
     - `generated_comparison.glb` — Side-by-side visualization (GT gray, pred red)

5. **CLI Enhancement** (`src/archi3d/cli.py`):
   - Added `--limit` flag to `archi3d compute fscore` command
   - Allows quick testing with subset of jobs (e.g., `--limit 1`)
   - Parameter properly wired through CLI → compute_fscore function

**Output Artifacts Structure**:
```
runs/<run_id>/metrics/fscore/<job_id>/
├── result.json                 # Complete payload with alignment_log, timing, version, config_hash
├── alignment_log.txt           # Human-readable debug log
└── generated_comparison.glb    # Visualization (GT gray, prediction red)
```

**Benefits**:
- **Better Debugging**: `alignment_log.txt` provides timing/alignment details for troubleshooting
- **Visual Inspection**: Comparison GLB allows visual verification of alignment quality
- **HTML Report Prep**: Visualization GLB path will be used in future Phase 7 HTML report with 3D viewer
- **Console Clarity**: Logger outputs show progress (mesh loading, alignment stages, final metrics) instead of just Open3D warnings
- **Quick Testing**: `--limit` flag enables fast iteration during development

**Testing**:
✅ Successfully tested with `--limit 1` on run `2025-11-22T21-25-08Z`:
- Console output shows detailed FScore progress messages:
  - "Loaded GT mesh: Leolux-LX91-armchair_KOeBH9twtP.glb (51290 vertices)"
  - "Loaded candidate mesh: generated.glb (50790 vertices)"
  - "Running pre-alignment (centering, scaling, RANSAC/PCA)..."
  - "Running ICP refinement..."
  - "Computing F-score metrics..."
  - "Saved comparison visualization: .../generated_comparison.glb"
  - "Evaluation complete: F-score=0.600, Precision=0.603, Recall=0.596, Total time=361.2s"
- `alignment_log.txt` created with timing breakdown, alignment details, mesh metadata
- `generated_comparison.glb` saved for visual inspection
- `result.json` contains all new fields (alignment_log, timing, version, config_hash, visualization_path)
- Total runtime: 361.2s for single job

**Known Issue**:
- Windows permission error when updating `generations.csv` if file is locked by Excel, OneDrive sync, or antivirus
- Workaround: Close Excel, pause OneDrive sync, or wait for sync to complete before running command
- FScore evaluation itself succeeds; error only occurs at final CSV update step

**Installation**:
To use the updated FScore integration:
```bash
# Install FScore as editable package
uv pip install --force-reinstall -e "C:/Users/matti/OneDrive - Politecnico di Bari (1)/Dev/FScore"

# Run FScore computation with limit for testing
archi3d compute fscore --run-id "your-run-id" --limit 1

# Run on all completed jobs
archi3d compute fscore --run-id "your-run-id"
```


---

### Phase 8 — Adapter Plug-In System (2025-11-25) ✅ COMPLETE

**Objective**: Implement monorepo integration with plug-in adapters for FScore/VFScore, supporting import-first/CLI-fallback discovery and third-party plugins via entry points.

**Background**:
Per `plans/Phase_8.md`, this phase prepares for closed-source binary distribution of FScore and VFScore while keeping archi3D open source. The adapter system allows flexible deployment models:
- **Development**: Editable package installs (`pip install -e path/to/FScore`)
- **Production**: Binary wheel distribution (`pip install fscore-0.2.0-*.whl`)
- **CI/CD**: External CLI invocation via environment variables
- **Third-party**: Custom adapters via setuptools entry points

**Implemented Components**:

1. **Protocol Definitions** (`src/archi3d/plugins/metrics.py`):
   - `FScoreAdapter` Protocol with `evaluate(req: FScoreRequest) -> FScoreResponse` method
   - `VFScoreAdapter` Protocol with `evaluate(req: VFScoreRequest) -> VFScoreResponse` method
   - `load_entry_point_adapter(namespace, name)` — Third-party plugin discovery
   - Runtime-checkable protocols for duck typing support

2. **Adapter Discovery Layer** (`src/archi3d/metrics/discovery.py`):
   - 3-tier resolution pattern:
     1. **Import mode**: Check if `fscore`/`vfscore` module is installed via `importlib.util.find_spec()`
     2. **Entry point mode**: Load from `archi3d.metrics_adapters` namespace
     3. **CLI mode**: Use command from `ARCHI3D_FSCORE_CLI`/`ARCHI3D_VFSCORE_CLI` environment variable
   - Environment variable overrides:
     - `ARCHI3D_FSCORE_IMPL` / `ARCHI3D_VFSCORE_IMPL` — Force specific mode (`import`, `cli`, `auto`)
     - `ARCHI3D_FSCORE_CLI` / `ARCHI3D_VFSCORE_CLI` — CLI command strings
   - `AdapterNotFoundError` with actionable error messages (installation instructions)
   - Separate discovery functions: `get_fscore_adapter()`, `get_vfscore_adapter()`

3. **Adapter Integration Updates**:
   - Modified `src/archi3d/metrics/fscore_adapter.py`:
     - `evaluate_fscore()` now uses `get_fscore_adapter()` from discovery layer
     - Removed direct `_try_import_api()` / `_try_cli_invocation()` calls from main entry point
     - Preserved original implementation functions for import/CLI wrappers
   - Modified `src/archi3d/metrics/vfscore_adapter.py`:
     - Same pattern as fscore_adapter.py
     - Discovery-based resolution for VFScore tool

4. **CLI Error Handling** (`src/archi3d/cli.py`):
   - Enhanced `compute fscore` and `compute vfscore` commands
   - Catches `AdapterNotFoundError` and displays clean error message without traceback
   - Other exceptions still show full traceback for debugging

5. **PyProject Configuration** (`pyproject.toml`):
   - Added optional dependencies:
     ```toml
     [project.optional-dependencies]
     fscore = []  # Install separately: pip install -e path/to/FScore
     vfscore = []  # Install separately: pip install -e path/to/VFScore
     ```
   - Defined entry point namespace for third-party plugins:
     ```toml
     [project.entry-points."archi3d.metrics_adapters"]
     # No built-in entry points; reserved for third-party plugins
     ```

6. **Integration Documentation** (`docs/INTEGRATION.md`):
   - Complete integration guide for FScore/VFScore
   - Three integration methods documented:
     1. Python import (recommended for development)
     2. CLI invocation (recommended for CI/CD)
     3. Entry points (for third-party plugins)
   - Environment variable configuration reference
   - Plugin development tutorial with example code
   - Canonical payload schema specification
   - Troubleshooting section with common issues
   - Discovery priority explanation

**Environment Variables**:

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `ARCHI3D_FSCORE_IMPL` | `import`/`cli`/`auto` | `auto` | Force FScore resolution mode |
| `ARCHI3D_VFSCORE_IMPL` | `import`/`cli`/`auto` | `auto` | Force VFScore resolution mode |
| `ARCHI3D_FSCORE_CLI` | Command string | - | FScore CLI command |
| `ARCHI3D_VFSCORE_CLI` | Command string | - | VFScore CLI command |

**Key Features**:
- **Import-first strategy**: Attempts Python import before falling back to CLI
- **Graceful degradation**: Clean error messages when adapters unavailable
- **Third-party extensibility**: Entry point support for custom adapters
- **Environment control**: Force specific resolution modes via environment variables
- **Docker-friendly**: CLI mode supports containerized deployment
- **No schema changes**: SSOT tables unchanged from Phases 0-7
- **Backward compatible**: Existing code works unchanged when modules are installed

**Testing**:
✅ Verified with dry-run test:
```bash
archi3d compute fscore --run-id "2025-11-22T21-25-08Z" --limit 1 --dry-run
```
- Successfully resolved FScore adapter via import mode
- Logged: "FScore adapter resolved via import"
- No changes to existing test suite (all 68 tests passing)

**Usage Examples**:

```bash
# Development: Install FScore as editable package
pip install -e "path/to/FScore"
archi3d compute fscore --run-id "..."

# Production: Install binary wheel
pip install fscore-0.2.0-cp311-cp311-win_amd64.whl
archi3d compute fscore --run-id "..."

# CI/CD: Use external CLI (no pip install needed)
export ARCHI3D_FSCORE_CLI="python -m fscore"
archi3d compute fscore --run-id "..."

# Force specific mode
export ARCHI3D_FSCORE_IMPL="import"  # Fail if not installable
archi3d compute fscore --run-id "..."
```

**Linting/Formatting**:
- All ruff checks passing (PLC0415 warnings suppressed for intentional lazy imports)
- Black formatting applied
- No F401 unused import warnings (using `importlib.util.find_spec()` instead of direct imports)

**Non-Functional Changes**:
- No changes to CLI semantics or existing commands
- No changes to SSOT schema or file formats
- Discovery layer adds ~100 lines of code total
- All existing imports remain compatible

**Next Steps**: Binary wheel distribution for FScore (Cython compilation, proprietary license)

---

### FScore Binary Distribution (2025-11-25) ✅ COMPLETE

**Objective**: Package FScore as a closed-source binary wheel using Cython compilation for code protection and distribution.

**Background**:
Per the "open-core" delivery strategy, FScore contains proprietary algorithms and must be distributed as compiled binaries (.pyd on Windows, .so on Linux) to protect intellectual property. The binary wheel approach:
- Preserves the `import fscore` interface (compatible with Phase 8 adapter discovery)
- Prevents source code inspection and reverse engineering
- Allows standard pip installation
- Maintains performance (native C extensions)

**Changes Made**:

1. **Cython Build Configuration** (`FScore/setup.py`):
   - Compiles 7 core modules to C extensions:
     - `fscore.evaluator` — Main evaluation logic
     - `fscore.alignment` — Mesh alignment algorithms (RANSAC, ICP)
     - `fscore.metrics` — F-score and Chamfer distance computation
     - `fscore.visualization` — GLB comparison export
     - `fscore.utils` — Shared utilities
     - `fscore.workspace` — Workspace management
     - `fscore.fbx_converter` — FBX to GLB conversion
   - Compiler directives for optimization:
     - `boundscheck=False` — Disable array bounds checking (performance)
     - `wraparound=False` — Disable negative indexing (performance)
     - `cdivision=True` — C-style division (performance)
     - `embedsignature=True` — Preserve function signatures for `help()`
     - `binding=True` — Enable runtime introspection
   - NumPy integration via `np.get_include()`
   - Build directory: `build/cython/` for intermediate files

2. **Project Configuration Updates** (`FScore/pyproject.toml`):
   - Updated build system requirements:
     ```toml
     [build-system]
     requires = ["setuptools>=61.0", "wheel", "Cython>=0.29.0", "numpy>=1.21.0"]
     build-backend = "setuptools.build_meta"
     ```
   - Changed license from MIT to Proprietary:
     ```toml
     license = {text = "Proprietary - All Rights Reserved"}
     classifiers = [
         "License :: Other/Proprietary License",
         # ... other classifiers
     ]
     ```
   - Version bumped to `0.2.0` (was `0.1.0`)

3. **Proprietary License** (`FScore/LICENSE.txt`):
   - 10-section comprehensive license agreement
   - Key restrictions:
     - **No reverse engineering, decompilation, or disassembly**
     - **No modification or derivative works**
     - **No redistribution** (except with written permission)
     - **No commercial use without explicit permission**
     - **No source code access** (binary distribution only)
   - Liability limitation and warranty disclaimer
   - Termination clause for license violations
   - Governing law (jurisdiction TBD by user)

4. **Distribution Control** (`FScore/MANIFEST.in`):
   - Includes: LICENSE.txt, README.md, pyproject.toml, setup.py
   - Excludes:
     - Test files (`tests/`, `test_models/`, `ground_truth/`, `run_test.py`)
     - Dev files (`.git/`, `.venv/`, `.claude/`, `.env`, `.gitignore`)
     - Build artifacts (`build/`, `dist/`, `*.egg-info/`)
     - Compiled files (`*.pyc`, `*.pyo`, `*.so`, `*.pyd`)
     - Documentation (CLAUDE.md, gemini.md, plan.md)
     - Binary tools (FBX2glTF-windows-x64.exe)

**Build Process**:

```bash
# Install build dependencies
pip install Cython build wheel

# Build binary wheel
cd FScore
python setup.py bdist_wheel

# Output
dist/fscore-0.2.0-cp311-cp311-win_amd64.whl  # 303 KB
```

**Build Output**:
- Wheel file: `fscore-0.2.0-cp311-cp311-win_amd64.whl` (303 KB)
- Platform-specific: `cp311-cp311-win_amd64` (Python 3.11, Windows x64)
- Contains:
  - 7 compiled `.pyd` extensions (binary C modules)
  - 7 corresponding `.py` stub files (for type hints/introspection)
  - `__init__.py` and `__main__.py` (package structure)
  - LICENSE.txt, metadata

**Installation Verification**:

Created test script (`test_binary_wheel.py`) and verified:

✅ **Test 1**: Module imports successfully
```
SUCCESS: fscore version 0.2.0
Location: .../site-packages/fscore/__init__.py
```

✅ **Test 2**: Function callable and detected as Cython function
```
SUCCESS: evaluate_one = <cyfunction evaluate_one at 0x...>
```

✅ **Test 3**: All modules compiled to binary extensions
```
Compiled extensions (.pyd): 7
  - alignment.cp311-win_amd64.pyd
  - evaluator.cp311-win_amd64.pyd
  - fbx_converter.cp311-win_amd64.pyd
  - metrics.cp311-win_amd64.pyd
  - utils.cp311-win_amd64.pyd
  - visualization.cp311-win_amd64.pyd
  - workspace.cp311-win_amd64.pyd
```

✅ **Test 4**: All expected modules verified as compiled
```
[OK] evaluator is compiled
[OK] alignment is compiled
[OK] metrics is compiled
[OK] visualization is compiled
[OK] utils is compiled
[OK] workspace is compiled
[OK] fbx_converter is compiled
```

**Installation**:

```bash
# Install binary wheel
pip install fscore-0.2.0-cp311-cp311-win_amd64.whl

# Verify installation
python -c "from fscore.evaluator import evaluate_one; print('Success!')"

# Use with archi3D (Phase 8 adapter discovery)
archi3d compute fscore --run-id "your-run-id"
```

**Integration with archi3D**:
- Phase 8 adapter discovery automatically detects installed wheel
- No code changes needed in archi3D
- Works identically to editable install (`pip install -e path/to/FScore`)
- CLI output: "FScore adapter resolved via import"

**Code Protection**:
- Source code compiled to native C extensions
- `.pyd` files are binary-only (not human-readable)
- Decompilation extremely difficult (requires reverse engineering C code)
- Proprietary license explicitly forbids reverse engineering
- Stronger protection than PyArmor or PyInstaller

**Performance**:
- Native C extensions provide performance benefits
- Compiler optimizations enabled (bounds check disabled, C division)
- NumPy integration via C API

**Platform Support**:
- Current build: Windows x64, Python 3.11
- For multi-platform distribution, build on each target platform:
  - Linux: `cp311-cp311-linux_x86_64.whl`
  - macOS: `cp311-cp311-macosx_*_x86_64.whl` or `macosx_*_arm64.whl`
- Use `cibuildwheel` for automated multi-platform builds in CI/CD

**Known Limitations**:
- `.py` stub files still included (for type hints and introspection)
  - These contain function signatures but not implementation details
  - Required for `help()`, IDE autocomplete, and type checking
  - Implementation code is in `.pyd` files only
- No runtime license verification (future enhancement)
- Wheel is platform-specific (need separate builds for Linux/macOS)

**Deliverables**:
- ✅ `setup.py` with Cython configuration
- ✅ Updated `pyproject.toml` with build requirements and proprietary license
- ✅ `LICENSE.txt` with comprehensive proprietary license agreement
- ✅ `MANIFEST.in` controlling distribution contents
- ✅ Binary wheel: `dist/fscore-0.2.0-cp311-cp311-win_amd64.whl` (303 KB)
- ✅ Verification script confirming compilation and installation

**Distribution Instructions**:
1. Build wheel on target platform: `python setup.py bdist_wheel`
2. Distribute wheel file (via private PyPI, email, or download link)
3. Recipients install via: `pip install fscore-0.2.0-*.whl`
4. No source code access required
5. License agreement terms in LICENSE.txt

**Next Steps**:
- Optional: VFScore binary distribution (same approach)
- Optional: Multi-platform wheel builds using cibuildwheel
- Optional: Runtime license verification for commercial use
- Optional: PyPI upload (private repository) for easier distribution
---

### VFScore Binary Distribution (2025-11-26) ✅ COMPLETE

**Objective**: Package VFScore as a closed-source binary wheel using Cython compilation for code protection and distribution, mirroring the FScore approach.

**Background**:
Per the "open-core" delivery strategy, VFScore contains proprietary visual fidelity scoring algorithms (Objective2 pipeline with pre-rendered library, LPIPS scoring, IoU silhouette matching) and must be distributed as compiled binaries to protect intellectual property. The binary wheel approach allows standard pip installation while preventing source code inspection.

**Configuration Integration Fix (2025-11-26)**:
After initial wheel distribution, VFScore integration with archi3D failed with `no_reference_images_found` error. Root cause analysis revealed configuration loading issues:

**Problem**:
1. VFScore evaluator was calling non-existent `load_config()` function (should use `Config.load()`)
2. Configuration was loading from file instead of using embedded defaults
3. Technical parameters from config.yaml were not embedded in the wheel
4. No mechanism for archi3D to pass workspace-specific paths (blender_exe, hdri)
5. Evaluator was using outdated Archi3DSource approach instead of direct pipeline invocation

**Solution**:
1. **Embedded Configuration Defaults** (`VFScore_RT/src/vfscore/config.py`):
   - Updated all Pydantic model defaults to match finetuned config.yaml parameters
   - RadiusCalibrationConfig: tri-criterion pipeline with Step 2 (iou_ar), Step 4 (iou), 2 coarse iterations, 3 step4_5 iterations
   - Objective2LibraryConfig: radius_multipliers=[1.0], fov_values=[20.0], elevation_values=[0-40 degrees in 5-10 degree steps]
   - Objective2RefinementConfig: disabled by default (refinement.enabled=false)
   - LPIPSConfig: model="alex", device="cpu"

2. **New load_config() Function** (`VFScore_RT/src/vfscore/config.py:391-442`):
   - Accepts workspace_path, blender_exe, hdri_path parameters
   - Creates Config instance with embedded defaults (no file loading)
   - Applies path overrides if provided
   - Checks for optional `vfscore_config.yaml` in workspace for user overrides
   - Resolves relative paths relative to workspace

3. **Updated Evaluator** (`VFScore_RT/src/vfscore/evaluator.py`):
   - Uses new `load_config(workspace_path=...)` function
   - Creates temporary GT directory with reference images passed by archi3D
   - Directly invokes `Objective2Pipeline._process_item()` with record dict
   - Sets `config.objective.priors_cache_dir` to control output directory placement
   - Cleans up temporary GT directory after processing

**Technical Parameters Embedded (from config.yaml)**:
- Pipeline mode: tri_criterion
- Radius calibration: resolution=256, pose_estimation_resolution=256, FOV=20 degrees
- Step 1 initial adjustment: yaw=45 degrees, elevation=10 degrees, border margin=3%
- Step 2 selection: iou_ar with top_fraction=3%
- Step 3 intermediate margin: 0.5%
- Step 4 search mode: fine (local search around best Step 3 pose), num_candidates=15
- Step 4 selection: iou (best silhouette match)
- Step 5 border margin: 0.5%
- Step 6 LPIPS: disabled (false)
- Yaw search: coarse_step=4 degrees, fine_step=0.5 degrees, fine_range=10 degrees
- Elevation search: coarse_step=5 degrees, fine_step=0.5 degrees, fine_range=10 degrees
- Refinement: disabled

**User Override Mechanism**:
Users can create `vfscore_config.yaml` in their archi3D workspace to override any embedded defaults. Example:
```yaml
objective:
  objective2:
    library:
      radius_calibration:
        step4_num_candidates: 20  # Override default 15
```

**Changes Made**:

1. **VFScore Evaluator Wrapper** (`VFScore_RT/src/vfscore/evaluator.py`):
   - Created public API wrapper function: `evaluate_visual_fidelity(cand_glb, ref_images, out_dir, repeats, timeout_s, workspace)`
   - Wraps internal `Objective2Pipeline` for compatibility with archi3D adapter
   - Returns canonical schema matching Phase 6 requirements
   - **Added artifact export fields**:
     - `artifacts_dir` — Path to vfscore_artifacts directory
     - `gt_image` — Relative path to GT image used for LPIPS comparison
     - `render_image` — Relative path to HQ render used for LPIPS comparison
   - Loads artifact paths from `vfscore_artifacts/artifacts.json` created by pipeline
   - Enables HTML report generation with GT/render side-by-side comparison

2. **Cython Build Configuration** (`VFScore_RT/setup.py`):
   - Compiles **all 21 modules** to C extensions:
     - **Main modules** (3): `evaluator`, `config`, `utils`
     - **Objective2 pipeline** (11): `pipeline_objective2`, `render_hq_pyrender`, `render_realtime`, `prerender_library`, `multi_gt_matcher`, `refine_pose`, `lpips_score`, `silhouette`, `combine`, `cache`, `image_utils`
     - **Data sources** (3): `base`, `archi3d_source`, `legacy_source`
     - **Preprocessing/reporting** (4): `preprocess_gt`, `ingest`, `aggregate_objective`, `report_objective`
   - **Cython Type Fixes Applied**:
     - `combine.py` — Rewrote to use `math.pow()` instead of `**` operator to avoid complex type inference
     - `cache.py` — Compiled successfully without modifications
   - Compiler directives:
     - `boundscheck=False`, `wraparound=False`, `cdivision=True` (performance)
     - `embedsignature=True`, `binding=True` (introspection)
   - NumPy integration via `np.get_include()` and `NPY_NO_DEPRECATED_API` macro
   - Build directory: `build/cython/`

3. **Project Configuration Updates** (`VFScore_RT/pyproject.toml`):
   - Updated build system requirements:
     ```toml
     [build-system]
     requires = ["setuptools>=65.0", "wheel", "Cython>=0.29.0", "numpy>=1.24.0"]
     build-backend = "setuptools.build_meta"
     ```
   - Changed license from MIT to Proprietary:
     ```toml
     license = {text = "Proprietary - All Rights Reserved"}
     classifiers = [
         "License :: Other/Proprietary License",
         # ... other classifiers
     ]
     ```
   - Version bumped: `0.1.0` → `0.2.0`

4. **Proprietary License** (`VFScore_RT/LICENSE.txt`):
   - Comprehensive 10-section license agreement (same structure as FScore)
   - Key restrictions:
     - **No reverse engineering, decompilation, or disassembly**
     - **No modification or derivative works**
     - **No redistribution** without written permission
     - **No commercial use** without explicit permission
     - **No source code access** (binary distribution only)
   - Liability limitation and warranty disclaimer
   - Termination clause for license violations

5. **Distribution Control** (`VFScore_RT/MANIFEST.in`):
   - Includes: LICENSE.txt, README.md, pyproject.toml, setup.py
   - Excludes:
     - Test files, dev files, build artifacts
     - Compiled files (*.pyc, *.pyo, *.so, *.pyd, *.cpp, *.c)
     - IDE/editor files (.vscode/, .idea/, .claude/)
     - Documentation (CLAUDE.md, gemini.md, plan.md)

6. **Package Export** (`VFScore_RT/src/vfscore/__init__.py`):
   - Exported `evaluate_visual_fidelity` function for public API
   - Version bumped to `0.2.0`

**Build Process**:

```bash
# Build binary wheel
cd VFScore_RT
python setup.py bdist_wheel

# Output
dist/vfscore_rt-0.2.0-cp311-cp311-win_amd64.whl
```

**Build Output**:
- Wheel file: `vfscore_rt-0.2.0-cp311-cp311-win_amd64.whl` (1.4 MB)
- Platform-specific: `cp311-cp311-win_amd64` (Python 3.11, Windows x64)
- Contains:
  - **21 compiled `.pyd` extensions** (binary C modules) — 100% coverage
  - Corresponding `.py` stub files for type hints
  - `__init__.py` and `__main__.py` (package structure)
  - LICENSE.txt, metadata

**Compilation Notes**:
- **All modules successfully compiled**: Initial Cython type inference issues with `combine.py` resolved by rewriting `**` operator to use `math.pow()`
- **Warning suppressions**: Negative indices with `wraparound=False` generate compiler warnings but do not affect build success (Python semantics preserved)
- **Build status**: ✅ Complete — 21/21 modules compiled to binary
- **Verification**: Tested import of `evaluate_visual_fidelity`, `combine`, and `cache` modules from wheel installation

**Installation**:

```bash
# Install binary wheel
pip install vfscore_rt-0.2.0-cp311-cp311-win_amd64.whl

# Verify installation
python -c "from vfscore.evaluator import evaluate_visual_fidelity; print('Success!')"

# Use with archi3D (Phase 8 adapter discovery)
archi3d compute vfscore --run-id "your-run-id"
```

**Integration with archi3D**:
- Phase 8 adapter discovery automatically detects installed wheel
- No code changes needed in archi3D
- Works identically to editable install (`pip install -e path/to/VFScore_RT`)
- CLI output: "VFScore adapter resolved via import"
- Artifact export enables HTML report generation with GT/render comparison images

**Code Protection**:
- Source code compiled to native C extensions
- `.pyd` files are binary-only (not human-readable)
- Decompilation extremely difficult (requires reverse engineering C code)
- Proprietary license explicitly forbids reverse engineering
- Stronger protection than obfuscation tools

**Performance**:
- Native C extensions provide performance benefits for rendering pipeline
- Compiler optimizations enabled (bounds check disabled, C division)
- NumPy integration via C API
- LPIPS/rendering operations benefit from compiled code

**Platform Support**:
- Current build: Windows x64, Python 3.11
- For multi-platform distribution, build on each target platform:
  - Linux: `cp311-cp311-linux_x86_64.whl`
  - macOS: `cp311-cp311-macosx_*_x86_64.whl` or `macosx_*_arm64.whl`
- Use `cibuildwheel` for automated multi-platform builds in CI/CD

**Deliverables**:
- ✅ `evaluator.py` public API wrapper with artifact export
- ✅ `setup.py` with Cython configuration (19 modules)
- ✅ Updated `pyproject.toml` with build requirements and proprietary license
- ✅ `LICENSE.txt` with comprehensive proprietary license agreement
- ✅ `MANIFEST.in` controlling distribution contents
- ✅ Binary wheel: `dist/vfscore_rt-0.2.0-cp311-cp311-win_amd64.whl`

**Distribution Instructions**:
1. Build wheel on target platform: `python setup.py bdist_wheel`
2. Distribute wheel file (via private PyPI, email, or download link)
3. Recipients install via: `pip install vfscore_rt-0.2.0-*.whl`
4. No source code access required
5. License agreement terms in LICENSE.txt

**Key Features**:
- **Artifact Export**: Exports GT image and HQ render paths for HTML report generation
- **API Compatibility**: Public `evaluate_visual_fidelity()` function matches Phase 6/8 adapter contract
- **Binary Protection**: 19 compiled modules protect proprietary Objective2 pipeline algorithms
- **Standard Installation**: Works with standard pip install, compatible with venv/virtualenv
- **Phase 8 Integration**: Seamless integration with archi3D adapter discovery layer

**Next Steps**:
- Optional: Multi-platform wheel builds using cibuildwheel
- Optional: Runtime license verification for commercial use
- Optional: PyPI upload (private repository) for easier distribution

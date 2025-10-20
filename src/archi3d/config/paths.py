# archi3d/config/paths.py
from __future__ import annotations

from pathlib import Path

from .schema import EffectiveConfig


class PathResolver:
    """
    Central place to resolve all project paths from the (per-user) workspace.
    Creates mutable subtrees (runs/, tables/, reports/) on demand.
    Never hard-codes absolute paths; everything is under the configured workspace.
    """

    def __init__(self, eff: EffectiveConfig) -> None:
        if eff.user_config is None:
            raise RuntimeError("EffectiveConfig.user_config is required")
        self._eff = eff

        # ---- Workspace (absolute) ----
        self.workspace_root: Path = Path(eff.user_config.workspace).resolve()

        # ---- Static trees (expected to exist) ----
        self.dataset_root: Path = self.workspace_root / "dataset"

        # ---- Mutable trees (created on demand) ----
        self.runs_dir: Path = self.workspace_root / "runs"
        self.tables_dir: Path = self.workspace_root / "tables"
        self.reports_dir: Path = self.workspace_root / "reports"
        self.logs_dir: Path = self.workspace_root / "logs"

        # Aliases for Phase 0 naming convention
        self.tables_root: Path = self.tables_dir
        self.runs_root: Path = self.runs_dir
        self.reports_root: Path = self.reports_dir
        self.logs_root: Path = self.logs_dir

        # Create mutable dirs if missing
        self.ensure_mutable_tree()

        # Canonical table files (created by commands when needed)
        self.items_csv: Path = self.tables_dir / "items.csv"
        self.results_parquet: Path = self.tables_dir / "results.parquet"

    # -------------------------
    # Run-scoped paths
    # -------------------------
    def run_dir(self, run_id: str) -> Path:
        p = self.runs_dir / run_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def run_root(self, run_id: str) -> Path:
        """Alias for run_dir (consistent with Phase 2 naming)."""
        return self.run_dir(run_id)

    def run_config_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "config.yaml"

    def manifest_inputs_csv(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "manifest_inputs.csv"

    def queue_dir(self, run_id: str) -> Path:
        q = self.run_dir(run_id) / "queue"
        q.mkdir(parents=True, exist_ok=True)
        return q

    def outputs_dir(self, run_id: str, algo: str | None = None, job_id: str | None = None) -> Path:
        """
        Get the outputs directory for a run, optionally scoped to algo and/or job_id.

        Phase 3: Supports per-job output directories under runs/<run_id>/outputs/<job_id>/
        """
        base = self.run_dir(run_id) / "outputs"
        base.mkdir(parents=True, exist_ok=True)

        # Phase 3: job_id-scoped outputs
        if job_id:
            job_out = base / job_id
            job_out.mkdir(parents=True, exist_ok=True)
            return job_out

        # Legacy: algo-scoped outputs
        if algo:
            a = base / algo
            a.mkdir(parents=True, exist_ok=True)
            return a

        return base

    def state_dir(self, run_id: str) -> Path:
        """
        Get the state directory for worker marker files.
        Phase 3: runs/<run_id>/state/
        """
        s = self.run_dir(run_id) / "state"
        s.mkdir(parents=True, exist_ok=True)
        return s

    def state_lock_path(self, run_id: str, job_id: str) -> Path:
        """
        Get the lock file path for a specific job's state transitions.
        Phase 3: runs/<run_id>/state/<job_id>.lock
        """
        return self.state_dir(run_id) / f"{job_id}.lock"

    def metrics_dir(self, run_id: str) -> Path:
        m = self.run_dir(run_id) / "metrics"
        m.mkdir(parents=True, exist_ok=True)
        return m

    def reports_out_dir(self, run_id: str) -> Path:
        out = self.reports_dir / run_id
        out.mkdir(parents=True, exist_ok=True)
        return out

    def results_staging_dir(self) -> Path:
        """A directory for workers to write unique, per-job result files."""
        p = self.tables_dir / "results_staging"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def manifest_lock_path(self, run_id: str) -> Path:
        """Returns the path for the manifest lock file within a dedicated locks subdir."""
        locks_dir = self.run_dir(run_id) / "locks"
        locks_dir.mkdir(parents=True, exist_ok=True)
        return locks_dir / "manifest_inputs.csv.lock"

    def results_lock_path(self) -> Path:
        """Returns the path for the results lock file within a dedicated locks subdir."""
        locks_dir = self.tables_dir / "locks"
        locks_dir.mkdir(parents=True, exist_ok=True)
        return locks_dir / "results.parquet.lock"

    # -------------------------
    # Utilities
    # -------------------------
    def rel_to_workspace(self, path: Path) -> Path:
        """Return path relative to workspace (useful for registry)."""
        path = path.resolve()
        return path.relative_to(self.workspace_root)

    def validate_expected_tree(self) -> None:
        """
        Validate the read-only parts of the tree exist (dataset at least).
        Commands should call this and fail early with a clear message.
        """
        if not self.dataset_root.exists():
            raise FileNotFoundError(
                f"Dataset root not found: {self.dataset_root}\n"
                "Please verify your 'workspace/dataset' path."
            )

    def ensure_mutable_tree(self) -> None:
        """
        Create mutable workspace directories (tables/, runs/, reports/, logs/)
        if they don't exist. Safe to call multiple times (idempotent).
        """
        for d in (self.tables_dir, self.runs_dir, self.reports_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    # -------------------------
    # File path getters (Phase 0 SSOT)
    # -------------------------
    def items_csv_path(self) -> Path:
        """Path to the canonical items catalog CSV."""
        return self.tables_dir / "items.csv"

    def items_issues_csv_path(self) -> Path:
        """Path to the items issues/validation CSV."""
        return self.tables_dir / "items_issues.csv"

    def generations_csv_path(self) -> Path:
        """Path to the generations/results CSV."""
        return self.tables_dir / "generations.csv"

    def catalog_build_log_path(self) -> Path:
        """Path to the catalog build log."""
        return self.logs_dir / "catalog_build.log"

    def batch_create_log_path(self) -> Path:
        """Path to the batch create log."""
        return self.logs_dir / "batch_create.log"

    def worker_log_path(self) -> Path:
        """Path to the worker execution log."""
        return self.logs_dir / "worker.log"

    def metrics_log_path(self) -> Path:
        """Path to the metrics computation log."""
        return self.logs_dir / "metrics.log"

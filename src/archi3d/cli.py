# archi3d/cli.py
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, List

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__

app = typer.Typer(add_completion=False, help="Archi3D CLI")
catalog_app = typer.Typer(help="Catalog operations")
batch_app = typer.Typer(help="Batch orchestration")
run_app = typer.Typer(help="Run workers")
metrics_app = typer.Typer(help="Metrics computation")
report_app = typer.Typer(help="Reporting")

app.add_typer(catalog_app, name="catalog")
app.add_typer(batch_app, name="batch")
app.add_typer(run_app, name="run")
app.add_typer(metrics_app, name="metrics")
app.add_typer(report_app, name="report")

console = Console()

# ---------------------------
# Internal helpers (no I/O defaults hard-coded)
# ---------------------------

def _fail(msg: str, code: int = 2) -> None:
    console.print(Panel.fit(f"[red]ERROR[/red] {msg}"))
    raise typer.Exit(code)

def _load_runtime():
    """
    Lazily load config + path resolver. This avoids import errors before those files exist.
    Requires either:
      - env ARCHI3D_WORKSPACE, or
      - ~/.archi3d/config.yaml with 'workspace:', or
      - repo-level global.yaml (workspace not recommended there).
    """
    try:
        from archi3d.config.loader import load_config
        from archi3d.config.paths import PathResolver
    except Exception as e:  # noqa: BLE001
        _fail(
            "Config modules are not available yet. "
            "Please add archi3d/config/{schema.py,loader.py,paths.py}. "
            f"Import error: {e!r}"
        )

    try:
        cfg = load_config()
        resolver = PathResolver(cfg)
    except Exception as e:  # noqa: BLE001
        _fail(f"Failed to load configuration: {e!r}")

    return cfg, resolver

def _parse_algos(csv_or_none: Optional[str], all_algos: List[str]) -> List[str]:
    if not csv_or_none:
        return list(all_algos)
    algos = [a.strip() for a in csv_or_none.split(",") if a.strip()]
    unknown = [a for a in algos if a not in all_algos]
    if unknown:
        _fail(f"Unknown algorithm keys: {unknown}. Allowed: {all_algos}")
    return algos

# ---------------------------
# Root options
# ---------------------------

@app.callback()
def _root(
    version: Optional[bool] = typer.Option(
        None, "--version", "-V", help="Show version and exit", is_eager=True
    )
):
    if version:
        console.print(f"archi3d {__version__}")
        raise typer.Exit(0)

# ---------------------------
# catalog build
# ---------------------------

@catalog_app.command("build")
def catalog_build():
    """
    Scan dataset tree and build/update tables/items.csv.
    Workspace is resolved from config/env; no path defaults are assumed here.
    """
    cfg, paths = _load_runtime()

    try:
        from archi3d.io.catalog import build_items_csv
    except Exception as e:  # noqa: BLE001
        _fail(f"Missing module archi3d.io.catalog (build_items_csv). Import error: {e!r}")

    dataset = paths.dataset_root
    out_csv = paths.tables_dir / "items.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    console.print(Panel.fit(f"[bold]Catalog build[/bold]\nDataset: {dataset}\nOut: {out_csv}"))
    try:
        stats = build_items_csv(dataset_root=dataset, out_csv=out_csv)
    except Exception as e:  # noqa: BLE001
        _fail(f"Catalog build failed: {e!r}")

    table = Table(title="Catalog Summary")
    table.add_column("Items", justify="right")
    table.add_column("With GT", justify="right")
    table.add_column("With ≥1 image", justify="right")
    table.add_row(str(stats.items_total), str(stats.items_with_gt), str(stats.items_with_img))
    console.print(table)

# ---------------------------
# batch create
# ---------------------------

@batch_app.command("create")
def batch_create(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier (required)"),
    algos: Optional[str] = typer.Option(None, "--algos", help="CSV of algorithm keys"),
    only: Optional[str] = typer.Option(None, "--only", help="Filter product_id by glob/regex"),
):
    """
    Freeze a run and create the job queue under runs/<run_id>/.
    """
    cfg, paths = _load_runtime()
    try:
        from archi3d.orchestrator.batch import create_batch
    except Exception as e:  # noqa: BLE001
        _fail(f"Missing module archi3d.orchestrator.batch (create_batch). Import error: {e!r}")

    all_algos = list(cfg.global_config.algorithms)
    selected = _parse_algos(algos, all_algos)

    console.print(
        Panel.fit(
            f"[bold]Batch create[/bold]\nRun: {run_id}\nAlgos: {', '.join(selected)}\nFilter: {only or '—'}"
        )
    )

    try:
        manifest_path = create_batch(
            run_id=run_id,
            algorithms=selected,
            paths=paths,
            only=only,
        )
    except Exception as e:  # noqa: BLE001
        _fail(f"Batch creation failed: {e!r}")

    console.print(f"[green]OK[/green] Manifest written at: {manifest_path}")

# ---------------------------
# run worker
# ---------------------------

@run_app.command("worker")
def run_worker(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier (required)"),
    algo: str = typer.Option(..., "--algo", help="Algorithm key (required)"),
    limit: int = typer.Option(1, "--limit", min=1, help="Max jobs to process in this session"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print actions without executing"),
):
    """
    Claim queue tokens and execute jobs for a given algorithm.
    """
    cfg, paths = _load_runtime()

    if algo not in cfg.global_config.algorithms:
        _fail(f"Unknown algorithm '{algo}'. Allowed: {list(cfg.global_config.algorithms)}")

    try:
        from archi3d.orchestrator.worker import run_worker as _run_worker
    except Exception as e:  # noqa: BLE001
        _fail(f"Missing module archi3d.orchestrator.worker (run_worker). Import error: {e!r}")

    console.print(
        Panel.fit(
            f"[bold]Run worker[/bold]\nRun: {run_id}\nAlgo: {algo}\nLimit: {limit}\nDry-run: {dry_run}"
        )
    )

    try:
        processed = _run_worker(
            run_id=run_id,
            algo=algo,
            limit=limit,
            dry_run=dry_run,
            paths=paths,
        )
    except Exception as e:  # noqa: BLE001
        _fail(f"Worker failed: {e!r}")

    console.print(f"[green]Done[/green] Processed: {processed} job(s).")

# ---------------------------
# metrics compute
# ---------------------------

@metrics_app.command("compute")
def metrics_compute(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier (required)"),
    algo: Optional[str] = typer.Option(None, "--algo", help="Restrict to one algorithm"),
    recompute: bool = typer.Option(False, "--recompute", help="Overwrite existing metrics"),
):
    """
    Compute/refresh metrics for outputs in a run (placeholder metrics for now).
    """
    _, paths = _load_runtime()

    try:
        from archi3d.metrics.compute import run as metrics_run
    except Exception as e:  # noqa: BLE001
        _fail(f"Missing module archi3d.metrics.compute (run). Import error: {e!r}")

    console.print(
        Panel.fit(
            f"[bold]Metrics compute[/bold]\nRun: {run_id}\nAlgo: {algo or 'ALL'}\nRecompute: {recompute}"
        )
    )

    try:
        n_updated = metrics_run(run_id=run_id, algo=algo, recompute=recompute, paths=paths)
    except Exception as e:  # noqa: BLE001
        _fail(f"Metrics compute failed: {e!r}")

    console.print(f"[green]OK[/green] Updated {n_updated} record(s).")

# ---------------------------
# report build
# ---------------------------

@report_app.command("build")
def report_build(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier (required)"),
    out: Optional[Path] = typer.Option(
        None, "--out", help="Output dir (default: reports/<run_id> under workspace)"
    ),
):
    """
    Build a minimal report (CSVs/figures) for a run.
    """
    _, paths = _load_runtime()

    try:
        from archi3d.reporting.report import build as report_build_impl
    except Exception as e:  # noqa: BLE001
        _fail(f"Missing module archi3d.reporting.report (build). Import error: {e!r}")

    out_dir = out or (paths.reports_dir / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    console.print(Panel.fit(f"[bold]Report build[/bold]\nRun: {run_id}\nOut: {out_dir}"))

    try:
        outputs = report_build_impl(run_id=run_id, out_dir=out_dir, paths=paths)
    except Exception as e:  # noqa: BLE001
        _fail(f"Report build failed: {e!r}")

    console.print(f"[green]OK[/green] Report artifacts: {outputs}")

__all__ = ["app"]

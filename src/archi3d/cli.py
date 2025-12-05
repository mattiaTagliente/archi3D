# archi3d/cli.py
from __future__ import annotations

import sys
from datetime import UTC
from pathlib import Path

import pandas as pd
import typer
from filelock import FileLock
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__


def _force_utf8_stdio():
    """Forces stdout and stderr to use UTF-8 encoding."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except TypeError:
                # In some environments (like terminals inside certain IDEs),
                # reconfigure might not accept the encoding argument.
                # We can safely ignore this as it's likely already UTF-8.
                pass


app = typer.Typer(add_completion=False, help="Archi3D CLI")
catalog_app = typer.Typer(help="Catalog and data operations")
batch_app = typer.Typer(help="Batch orchestration")
run_app = typer.Typer(help="Run workers")
compute_app = typer.Typer(help="Compute metrics")
metrics_app = typer.Typer(help="Metrics computation")
report_app = typer.Typer(help="Reporting")

app.add_typer(catalog_app, name="catalog")
app.add_typer(batch_app, name="batch")
app.add_typer(run_app, name="run")
app.add_typer(compute_app, name="compute")
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


def _parse_algos(
    csv_or_none: str | None, all_algos: list[str]
) -> tuple[list[str], bool]:
    """
    Parse algorithm selection string.

    Args:
        csv_or_none: Comma-separated algorithm keys, "ecotest", or None.
        all_algos: List of all available algorithm keys.

    Returns:
        (selected_algos, algo_by_images) tuple.
        algo_by_images is True for ecotest mode.
    """
    if not csv_or_none:
        return list(all_algos), False

    # Check for special keyword "ecotest"
    if csv_or_none.strip().lower() == "ecotest":
        # Return all algorithms, with algo_by_images=True
        return list(all_algos), True

    algos = [a.strip() for a in csv_or_none.split(",") if a.strip()]
    unknown = [a for a in algos if a not in all_algos]
    if unknown:
        _fail(f"Unknown algorithm keys: {unknown}. Allowed: {all_algos}")
    return algos, False


# ---------------------------
# Root options
# ---------------------------


@app.callback()
def _root(
    version: bool | None = typer.Option(
        None, "--version", "-V", help="Show version and exit", is_eager=True
    )
):
    _force_utf8_stdio()
    if version:
        console.print(f"archi3d {__version__}")
        raise typer.Exit(0)


# ---------------------------
# catalog build
# ---------------------------


@catalog_app.command("build")
def catalog_build(
    dataset: Path | None = typer.Option(None, "--dataset", help="Dataset directory path"),
    products_json: Path | None = typer.Option(
        None, "--products-json", help="Path to products-with-3d.json"
    ),
):
    """
    Scan the curated dataset and build tables/items.csv with enrichment from products-with-3d.json.
    Writes items.csv, items_issues.csv, and logs to catalog_build.log.
    """
    _, paths = _load_runtime()

    try:
        from archi3d.db.catalog import build_catalog
    except Exception as e:  # noqa: BLE001
        _fail(f"Missing module archi3d.db.catalog (build_catalog). Import error: {e!r}")

    # Resolve dataset path with defaults and validation
    if dataset is None:
        dataset = paths.dataset_root
    else:
        dataset = Path(dataset).resolve()

    if not dataset.exists():
        _fail(f"Dataset directory not found: {dataset}")

    # Resolve products JSON path with auto-discovery
    if products_json is None:
        # Default: ${workspace}/products-with-3d.json
        products_json = paths.workspace_root / "products-with-3d.json"

        # Auto-discovery: try one level up from workspace if default doesn't exist
        if not products_json.exists():
            fallback = paths.workspace_root.parent / "products-with-3d.json"
            if fallback.exists():
                products_json = fallback
                console.print(f"[yellow]Auto-discovered products JSON at: {products_json}[/yellow]")
            else:
                console.print(
                    "[yellow]Warning: products-with-3d.json not found. "
                    "Proceeding without enrichment.[/yellow]"
                )
                products_json = None
    else:
        products_json = Path(products_json).resolve()
        if not products_json.exists():
            console.print(
                f"[yellow]Warning: Specified products JSON not found: {products_json}. "
                f"Proceeding without enrichment.[/yellow]"
            )
            products_json = None

    # Display build information
    panel_text = f"[bold]Catalog build[/bold]\nDataset: {dataset}"
    if products_json:
        panel_text += f"\nProducts JSON: {products_json}"
    else:
        panel_text += "\nProducts JSON: [yellow]Not available[/yellow]"

    console.print(Panel.fit(panel_text))

    # Execute build
    try:
        items_count, issues_count = build_catalog(
            dataset_path=dataset, products_json_path=products_json, paths=paths
        )
    except Exception as e:  # noqa: BLE001
        _fail(f"Catalog build failed: {e!r}")

    # Display summary
    console.print(
        f"\n[green]Catalog build complete![/green]\n"
        f"  Items: {items_count}\n"
        f"  Issues: {issues_count}"
    )


# ---------------------------
# catalog consolidate
# ---------------------------


@catalog_app.command("consolidate")
def catalog_consolidate():
    """
    Consolidate staged results into the main tables/results.parquet file.
    """
    _, paths = _load_runtime()

    staging_dir = paths.results_staging_dir()
    main_results_path = paths.results_parquet

    staged_files = list(staging_dir.glob("*.parquet"))

    if not staged_files:
        console.print("[yellow]No new results found in the staging area to consolidate.[/yellow]")
        raise typer.Exit()

    console.print(f"Found {len(staged_files)} new result file(s) to consolidate.")

    # Read all staged files into a list of DataFrames
    df_list = [pd.read_parquet(f) for f in staged_files]
    new_results_df = pd.concat(df_list, ignore_index=True)

    # Use the results lock to safely update the main file
    lock_path = paths.results_lock_path()
    with FileLock(str(lock_path)):
        if main_results_path.exists():
            console.print("Appending new results to existing tables/results.parquet...")
            existing_df = pd.read_parquet(main_results_path)
            # Drop duplicates, keeping the most recent entry for a given job_id
            combined_df = pd.concat([existing_df, new_results_df], ignore_index=True)
            combined_df.drop_duplicates(subset=["job_id"], keep="last", inplace=True)
        else:
            console.print("Creating new tables/results.parquet...")
            combined_df = new_results_df

        combined_df.to_parquet(main_results_path, index=False)

    console.print(f"[green]Successfully consolidated results into {main_results_path}[/green]")

    # Optional: Clean up staged files after consolidation
    console.print("Cleaning up staged files...")
    for f in staged_files:
        f.unlink()
    console.print("Done.")


# ---------------------------
# batch create
# ---------------------------


@batch_app.command("create")
def batch_create(
    run_id: str | None = typer.Option(
        None, "--run-id", help="Run identifier (autogenerated if omitted)"
    ),
    algos: str | None = typer.Option(
        None,
        "--algos",
        help="Comma-separated algorithm keys, or 'ecotest' for auto-selection by n_images",
    ),
    image_policy: str = typer.Option(
        "use_up_to_6", "--image-policy", help="Image selection policy"
    ),
    limit: int | None = typer.Option(None, "--limit", help="Maximum number of items to process"),
    include: str | None = typer.Option(
        None,
        "--include",
        help="Include filter (substring match on product_id/variant/product_name)",
    ),
    exclude: str | None = typer.Option(None, "--exclude", help="Exclude filter (substring match)"),
    with_gt_only: bool = typer.Option(False, "--with-gt-only", help="Skip items without GT object"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Compute summary without writing files"),
):
    """
    Create a batch of jobs for one or more algorithms.

    Reads tables/items.csv, applies filters and image selection policy,
    upserts rows to tables/generations.csv with status='enqueued',
    and creates runs/<run_id>/manifest.csv.
    """
    from datetime import datetime

    cfg, paths = _load_runtime()

    try:
        from archi3d.orchestrator.batch import create_batch
    except Exception as e:
        _fail(f"Missing module archi3d.orchestrator.batch (create_batch). Import error: {e!r}")

    # Auto-generate run_id if not provided
    if run_id is None:
        run_id = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")

    # Parse algorithms
    all_algos = list(cfg.global_config.algorithms)
    algo_by_images = False

    if algos:
        selected, algo_by_images = _parse_algos(algos, all_algos)
    # Default: use a sensible default or require explicit algos
    elif all_algos:
        # Use first algorithm as default (or could use all)
        selected = [all_algos[0]]
        console.print(f"[yellow]No --algos specified, using default: {selected[0]}[/yellow]")
    else:
        _fail("No algorithms configured in global.yaml and none specified via --algos")

    # Display batch creation info
    mode_str = "ecotest (auto-select by n_images)" if algo_by_images else "normal"
    panel_text = (
        f"[bold]Batch create (Phase 2)[/bold]\n"
        f"Run: {run_id}\n"
        f"Algos: {', '.join(selected)}\n"
        f"Mode: {mode_str}\n"
        f"Policy: {image_policy}\n"
        f"Include: {include or '---'}\n"
        f"Exclude: {exclude or '---'}\n"
        f"GT only: {with_gt_only}\n"
        f"Limit: {limit or '---'}\n"
        f"Dry-run: {dry_run}"
    )
    console.print(Panel.fit(panel_text))

    try:
        summary = create_batch(
            run_id=run_id,
            algos=selected,
            paths=paths,
            image_policy=image_policy,
            limit=limit,
            include=include,
            exclude=exclude,
            with_gt_only=with_gt_only,
            dry_run=dry_run,
            algo_by_images=algo_by_images,
        )
    except Exception as e:
        import traceback

        _fail(f"Batch creation failed: {e}\n{traceback.format_exc()}")

    # Display summary
    candidates = summary.get("candidates", 0)
    enqueued = summary.get("enqueued", 0)
    skipped = summary.get("skipped", 0)

    if not dry_run:
        console.print("\n[green]Batch creation complete![/green]")
        console.print(f"  Generations CSV: {paths.generations_csv_path()}")
        console.print(f"  Manifest: {paths.run_root(run_id) / 'manifest.csv'}")
        console.print(f"  Log: {paths.batch_create_log_path()}")
    else:
        console.print("\n[yellow]Dry-run complete (no files written)[/yellow]")

    summary_table = Table(title="Batch Creation Summary")
    summary_table.add_column("Metric", justify="left", style="cyan")
    summary_table.add_column("Count", justify="right", style="magenta")
    summary_table.add_row("Candidates", str(candidates))
    summary_table.add_row("[green]Enqueued[/green]", str(enqueued))
    summary_table.add_row("[yellow]Skipped[/yellow]", str(skipped))
    console.print(summary_table)

    if skipped > 0:
        skip_reasons = summary.get("skip_reasons", {})
        if skip_reasons:
            reasons_table = Table(title="Skip Reasons")
            reasons_table.add_column("Reason", justify="left")
            reasons_table.add_column("Count", justify="right")
            for reason, count in skip_reasons.items():
                reasons_table.add_row(reason, str(count))
            console.print(reasons_table)


# ---------------------------
# run worker (Phase 3)
# ---------------------------


@run_app.command("worker")
def run_worker_cmd(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier (required)"),
    jobs: str | None = typer.Option(None, "--jobs", help="Filter job_id by substring/regex"),
    only_status: str = typer.Option(
        "enqueued", "--only-status", help="Comma-separated statuses to process (default: enqueued)"
    ),
    max_parallel: int = typer.Option(
        1, "--max-parallel", help="Maximum concurrent workers (default: 1)"
    ),
    adapter: str | None = typer.Option(
        None, "--adapter", help="Force specific adapter (debug mode)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate execution without calling adapters"
    ),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop on first failure"),
    redo: bool = typer.Option(
        False, "--redo", help="Clear state markers and retry selected jobs"
    ),
):
    """
    Execute generation jobs from tables/generations.csv for a given run.

    Phase 3: Reads SSOT generations.csv, filters by status and optional patterns,
    executes jobs with lifecycle management, updates CSV with results and metadata,
    creates state markers for resumability, supports concurrency via thread pool.
    """
    cfg, paths = _load_runtime()

    # Validate adapter if specified
    if adapter and adapter not in cfg.global_config.algorithms:
        _fail(f"Unknown adapter '{adapter}'. Allowed: {list(cfg.global_config.algorithms)}")

    try:
        from archi3d.orchestrator.worker import run_worker as _run_worker
    except Exception as e:  # noqa: BLE001
        _fail(f"Missing module archi3d.orchestrator.worker (run_worker). Import error: {e!r}")

    # Display execution info
    panel_text = (
        f"[bold]Run Worker (Phase 3)[/bold]\n"
        f"Run: {run_id}\n"
        f"Jobs filter: {jobs or '---'}\n"
        f"Only status: {only_status}\n"
        f"Max parallel: {max_parallel}\n"
        f"Adapter override: {adapter or '---'}\n"
        f"Dry-run: {dry_run}\n"
        f"Fail-fast: {fail_fast}\n"
        f"Redo: {redo}"
    )
    console.print(Panel.fit(panel_text))

    try:
        result = _run_worker(
            run_id=run_id,
            paths=paths,
            jobs=jobs,
            only_status=only_status,
            max_parallel=max_parallel,
            adapter=adapter,
            dry_run=dry_run,
            fail_fast=fail_fast,
            redo=redo,
        )
    except Exception as e:  # noqa: BLE001
        import traceback

        _fail(f"Worker execution failed: {e}\n{traceback.format_exc()}")

    # Display summary
    console.print(
        f"\n[green]Worker execution complete![/green]\n"
        f"  Processed: {result.get('processed', 0)}\n"
        f"  Completed: [green]{result.get('completed', 0)}[/green]\n"
        f"  Failed: [red]{result.get('failed', 0)}[/red]\n"
        f"  Skipped: [yellow]{result.get('skipped', 0)}[/yellow]\n"
        f"  Avg duration: {result.get('avg_duration_s', 0):.2f}s"
    )

    console.print(f"\n  Generations CSV: {paths.generations_csv_path()}")
    console.print(f"  State markers: {paths.state_dir(run_id)}")
    console.print(f"  Worker log: {paths.worker_log_path()}")


# ---------------------------
# metrics compute
# ---------------------------


@metrics_app.command("compute")
def metrics_compute(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier (required)"),
    algo: str | None = typer.Option(None, "--algo", help="Restrict to one algorithm"),
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
):
    """
    Generate an interactive HTML report with all runs, visualizations and statistical analysis.

    The HTML report will be saved to: reports/report.html (within workspace)
    The report includes all runs with a dropdown selector for filtering.
    """
    _, paths = _load_runtime()

    try:
        from archi3d.reporting.report import build_html_report
    except Exception as e:  # noqa: BLE001
        _fail(f"Missing module archi3d.reporting.report. Import error: {e!r}")

    console.print(Panel.fit(f"[bold]HTML Report Generation[/bold]\nRun: {run_id}"))

    try:
        # Build HTML report (saves to reports/report.html, includes all runs)
        html_path = build_html_report(run_id=run_id, paths=paths)
        console.print(f"\n[green]HTML report generated:[/green] {html_path}")

    except Exception as e:  # noqa: BLE001
        _fail(f"Report build failed: {e!r}")


# ---------------------------
# consolidate (Phase 4)
# ---------------------------


@app.command("consolidate")
def consolidate_cmd(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier (required)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Compute changes without writing CSV"),
    strict: bool = typer.Option(False, "--strict", help="Exit with error on any conflict"),
    only_status: str | None = typer.Option(
        None, "--only-status", help="Comma-separated statuses to process (default: all)"
    ),
    fix_status: bool = typer.Option(
        True, "--fix-status", help="Apply status downgrades for missing outputs (default: true)"
    ),
    max_rows: int | None = typer.Option(
        None, "--max-rows", help="Maximum rows to process (optional cap for safety)"
    ),
):
    """
    Reconcile tables/generations.csv with on-disk artifacts and state markers.

    Phase 4: Validates and fixes inconsistencies between CSV and disk state,
    deduplicates rows, fills missing metadata, and ensures SSOT consistency.
    Supports idempotent re-runs.
    """
    _, paths = _load_runtime()

    try:
        from archi3d.orchestrator.consolidate import consolidate
    except Exception as e:  # noqa: BLE001
        _fail(f"Missing module archi3d.orchestrator.consolidate. Import error: {e!r}")

    # Display consolidation info
    panel_text = (
        f"[bold]Consolidate (Phase 4)[/bold]\n"
        f"Run: {run_id}\n"
        f"Only status: {only_status or 'all'}\n"
        f"Fix status: {fix_status}\n"
        f"Max rows: {max_rows or '—'}\n"
        f"Dry-run: {dry_run}\n"
        f"Strict: {strict}"
    )
    console.print(Panel.fit(panel_text))

    try:
        summary = consolidate(
            run_id=run_id,
            paths=paths,
            dry_run=dry_run,
            strict=strict,
            only_status=only_status,
            fix_status=fix_status,
            max_rows=max_rows,
        )
    except Exception as e:  # noqa: BLE001
        import traceback

        _fail(f"Consolidation failed: {e}\n{traceback.format_exc()}")

    # Display summary
    if dry_run:
        console.print("\n[yellow]Dry-run complete (no CSV writes)[/yellow]")
    else:
        console.print("\n[green]Consolidation complete![/green]")

    summary_table = Table(title="Consolidation Summary")
    summary_table.add_column("Metric", justify="left", style="cyan")
    summary_table.add_column("Count", justify="right", style="magenta")

    summary_table.add_row("Considered", str(summary.get("considered", 0)))
    summary_table.add_row("[green]Unchanged[/green]", str(summary.get("unchanged", 0)))
    summary_table.add_row("[yellow]Updated[/yellow]", str(summary.get("upsert_updated", 0)))
    summary_table.add_row("[blue]Inserted[/blue]", str(summary.get("upsert_inserted", 0)))
    summary_table.add_row("Conflicts resolved", str(summary.get("conflicts_resolved", 0)))
    summary_table.add_row("Marker mismatches fixed", str(summary.get("marker_mismatches_fixed", 0)))
    summary_table.add_row(
        "Downgraded (missing output)", str(summary.get("downgraded_missing_output", 0))
    )

    console.print(summary_table)

    # Status histograms
    before = summary.get("status_histogram_before", {})
    after = summary.get("status_histogram_after", {})

    if before or after:
        hist_table = Table(title="Status Changes")
        hist_table.add_column("Status", justify="left")
        hist_table.add_column("Before", justify="right")
        hist_table.add_column("After", justify="right")

        all_statuses = sorted(set(before.keys()) | set(after.keys()))
        for status in all_statuses:
            hist_table.add_row(status, str(before.get(status, 0)), str(after.get(status, 0)))
        console.print(hist_table)

    console.print(f"\n  Generations CSV: {paths.generations_csv_path()}")
    console.print(f"  Metrics log: {paths.metrics_log_path()}")


# ---------------------------
# compute fscore (Phase 5)
# ---------------------------


@compute_app.command("fscore")
def compute_fscore_cmd(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier (required)"),
    jobs: str | None = typer.Option(None, "--jobs", help="Filter job_id by glob/regex/substring"),
    only_status: str = typer.Option(
        "completed",
        "--only-status",
        help="Comma-separated statuses to process (default: completed)",
    ),
    with_gt_only: bool = typer.Option(
        True, "--with-gt-only", help="Require non-empty GT object path (default: true)"
    ),
    redo: bool = typer.Option(
        False, "--redo", help="Recompute even if metrics already present (default: false)"
    ),
    n_points: int = typer.Option(
        100000, "--n-points", help="Poisson disk samples per mesh (default: 100000)"
    ),
    timeout_s: int | None = typer.Option(
        None, "--timeout-s", help="Per-job timeout in seconds (optional)"
    ),
    max_parallel: int = typer.Option(
        1, "--max-parallel", help="Maximum parallel jobs (default: 1)"
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="Limit number of jobs to process (optional)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview selection without running evaluator (default: false)"
    ),
):
    """
    Compute FScore (geometry metrics) for eligible jobs in a run.

    Phase 5: Computes F-score, precision, recall, Chamfer-L2, and alignment
    transforms for completed jobs with GT objects. Upserts results to
    tables/generations.csv and persists per-job artifacts.
    """
    _, paths = _load_runtime()

    try:
        from archi3d.metrics.fscore import compute_fscore
    except Exception as e:  # noqa: BLE001
        _fail(f"Missing module archi3d.metrics.fscore (compute_fscore). Import error: {e!r}")

    # Display execution info
    panel_text = (
        f"[bold]Compute FScore (Phase 5)[/bold]\n"
        f"Run: {run_id}\n"
        f"Jobs filter: {jobs or '—'}\n"
        f"Only status: {only_status}\n"
        f"With GT only: {with_gt_only}\n"
        f"Redo: {redo}\n"
        f"N-points: {n_points}\n"
        f"Timeout: {timeout_s or '—'}s\n"
        f"Max parallel: {max_parallel}\n"
        f"Limit: {limit or '—'}\n"
        f"Dry-run: {dry_run}"
    )
    console.print(Panel.fit(panel_text))

    try:
        summary = compute_fscore(
            run_id=run_id,
            jobs=jobs,
            only_status=only_status,
            with_gt_only=with_gt_only,
            redo=redo,
            n_points=n_points,
            timeout_s=timeout_s,
            max_parallel=max_parallel,
            limit=limit,
            dry_run=dry_run,
        )
    except Exception as e:  # noqa: BLE001
        import traceback

        from archi3d.metrics.discovery import AdapterNotFoundError

        # For adapter discovery errors, show clean message without traceback
        if isinstance(e, AdapterNotFoundError):
            _fail(str(e))
        else:
            _fail(f"FScore computation failed: {e}\n{traceback.format_exc()}")

    # Display summary
    if dry_run:
        console.print("\n[yellow]Dry-run complete (no evaluator calls)[/yellow]")
    else:
        console.print("\n[green]FScore computation complete![/green]")

    summary_table = Table(title="FScore Computation Summary")
    summary_table.add_column("Metric", justify="left", style="cyan")
    summary_table.add_column("Count", justify="right", style="magenta")

    summary_table.add_row("Selected", str(summary.get("n_selected", 0)))
    summary_table.add_row("Processed", str(summary.get("processed", 0)))
    summary_table.add_row("[green]OK[/green]", str(summary.get("ok", 0)))
    summary_table.add_row("[red]Error[/red]", str(summary.get("error", 0)))
    summary_table.add_row("[yellow]Skipped[/yellow]", str(summary.get("skipped", 0)))

    if summary.get("avg_runtime_s"):
        summary_table.add_row("Avg runtime", f"{summary['avg_runtime_s']:.2f}s")

    console.print(summary_table)

    # Show skip reasons if any
    skip_reasons = summary.get("skip_reasons", {})
    if skip_reasons:
        reasons_table = Table(title="Skip Reasons")
        reasons_table.add_column("Reason", justify="left")
        reasons_table.add_column("Count", justify="right")
        for reason, count in skip_reasons.items():
            reasons_table.add_row(reason, str(count))
        console.print(reasons_table)

    console.print(f"\n  Generations CSV: {paths.generations_csv_path()}")
    console.print(f"  Metrics artifacts: {paths.runs_root / run_id / 'metrics' / 'fscore'}")
    console.print(f"  Metrics log: {paths.metrics_log_path()}")


# ---------------------------
# compute vfscore (Phase 6)
# ---------------------------


@compute_app.command("vfscore")
def compute_vfscore_cmd(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier (required)"),
    jobs: str | None = typer.Option(None, "--jobs", help="Filter job_id by glob/regex/substring"),
    only_status: str = typer.Option(
        "completed",
        "--only-status",
        help="Comma-separated statuses to process (default: completed)",
    ),
    use_images_from: str = typer.Option(
        "used",
        "--use-images-from",
        help="Reference image source: 'used' or 'source' (default: used)",
    ),
    repeats: int = typer.Option(
        1, "--repeats", help="Number of LLM scoring repeats for consistency (default: 1)"
    ),
    redo: bool = typer.Option(
        False, "--redo", help="Recompute even if metrics already present (default: false)"
    ),
    max_parallel: int = typer.Option(
        1, "--max-parallel", help="Maximum parallel jobs (default: 1)"
    ),
    timeout_s: int | None = typer.Option(
        None, "--timeout-s", help="Per-job timeout in seconds (optional)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview selection without running evaluator (default: false)"
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="Limit number of jobs to process (optional)"
    ),
):
    """
    Compute VFScore (visual fidelity metrics) for eligible jobs in a run.

    Phase 6: Renders generated models under standardized Cycles setup and uses
    LLM-based visual scoring to compare against reference photos. Upserts results
    to tables/generations.csv and persists per-job artifacts.
    """
    _, paths = _load_runtime()

    try:
        from archi3d.metrics.vfscore import compute_vfscore
    except Exception as e:  # noqa: BLE001
        _fail(f"Missing module archi3d.metrics.vfscore (compute_vfscore). Import error: {e!r}")

    # Validate use_images_from
    if use_images_from not in ["used", "source"]:
        _fail("--use-images-from must be 'used' or 'source'")

    # Display execution info
    panel_text = (
        f"[bold]Compute VFScore (Phase 6)[/bold]\n"
        f"Run: {run_id}\n"
        f"Jobs filter: {jobs or '—'}\n"
        f"Only status: {only_status}\n"
        f"Use images from: {use_images_from}\n"
        f"Repeats: {repeats}\n"
        f"Redo: {redo}\n"
        f"Timeout: {timeout_s or '—'}s\n"
        f"Max parallel: {max_parallel}\n"
        f"Limit: {limit or '—'}\n"
        f"Dry-run: {dry_run}"
    )
    console.print(Panel.fit(panel_text))

    try:
        summary = compute_vfscore(
            run_id=run_id,
            jobs=jobs,
            only_status=only_status,
            use_images_from=use_images_from,
            repeats=repeats,
            redo=redo,
            max_parallel=max_parallel,
            timeout_s=timeout_s,
            dry_run=dry_run,
            limit=limit,
        )
    except Exception as e:  # noqa: BLE001
        import traceback

        from archi3d.metrics.discovery import AdapterNotFoundError

        # For adapter discovery errors, show clean message without traceback
        if isinstance(e, AdapterNotFoundError):
            _fail(str(e))
        else:
            _fail(f"VFScore computation failed: {e}\n{traceback.format_exc()}")

    # Display summary
    if dry_run:
        console.print("\n[yellow]Dry-run complete (no evaluator calls)[/yellow]")
    else:
        console.print("\n[green]VFScore computation complete![/green]")

    summary_table = Table(title="VFScore Computation Summary")
    summary_table.add_column("Metric", justify="left", style="cyan")
    summary_table.add_column("Count", justify="right", style="magenta")

    summary_table.add_row("Selected", str(summary.get("n_selected", 0)))
    summary_table.add_row("Processed", str(summary.get("processed", 0)))
    summary_table.add_row("[green]OK[/green]", str(summary.get("ok", 0)))
    summary_table.add_row("[red]Error[/red]", str(summary.get("error", 0)))
    summary_table.add_row("[yellow]Skipped[/yellow]", str(summary.get("skipped", 0)))

    if summary.get("avg_render_runtime_s"):
        summary_table.add_row("Avg render time", f"{summary['avg_render_runtime_s']:.2f}s")
    if summary.get("avg_scoring_runtime_s"):
        summary_table.add_row("Avg scoring time", f"{summary['avg_scoring_runtime_s']:.2f}s")

    console.print(summary_table)

    # Show skip reasons if any
    skip_reasons = summary.get("skip_reasons", {})
    if skip_reasons:
        reasons_table = Table(title="Skip Reasons")
        reasons_table.add_column("Reason", justify="left")
        reasons_table.add_column("Count", justify="right")
        for reason, count in skip_reasons.items():
            reasons_table.add_row(reason, str(count))
        console.print(reasons_table)

    console.print(f"\n  Generations CSV: {paths.generations_csv_path()}")
    console.print(f"  Metrics artifacts: {paths.runs_root / run_id / 'metrics' / 'vfscore'}")
    console.print(f"  Metrics log: {paths.metrics_log_path()}")


__all__ = ["app"]

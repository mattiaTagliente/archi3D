# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

# archi3d/reporting/report.py
from __future__ import annotations

import csv
import json
import math
import re
import statistics
from itertools import combinations
from pathlib import Path

from archi3d.config.loader import load_config
from archi3d.config.paths import PathResolver

# Optional pandas import for subjective evaluation loading
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


def build(run_id: str, paths: PathResolver) -> Path:
    """
    Generate interactive HTML report for all runs.

    This is a wrapper function for backward compatibility.
    The actual report generation is handled by build_html_report().

    Args:
        run_id: Run identifier (used for compatibility, but report includes all runs)
        paths: PathResolver instance for workspace-aware path resolution

    Returns:
        Path to the generated HTML file (reports/report.html)
    """
    return build_html_report(run_id=run_id, paths=paths)


# ---------------------------------------------------------------
# Subjective Evaluation Data Loading
# ---------------------------------------------------------------

def load_subjective_data(run_id: str, algorithms: list[str], paths: PathResolver) -> dict:
    """
    Load subjective evaluation data from Excel file for a specific run.

    Args:
        run_id: Run identifier
        algorithms: List of algorithm names to match
        paths: PathResolver instance for workspace-aware path resolution

    Returns:
        Dictionary mapping (product_id, algorithm) to subjective scores:
        {
            (product_id, algo): {
                'subj_geo': float or None,  # Geometry score (0-1)
                'subj_vf': float or None     # Visual fidelity score (0-1)
            }
        }
    """
    if not HAS_PANDAS:
        return {}

    # Construct path to subjective evaluation Excel file
    excel_path = paths.runs_root / run_id / "Subjective evaluation.xlsx"

    if not excel_path.exists():
        return {}

    try:
        # Read the 'Average' sheet
        df = pd.read_excel(excel_path, sheet_name='Average')

        # Initialize result dictionary
        data_map = {}

        # Iterate through rows
        for _, row in df.iterrows():
            item_id = str(row.get('ID', '')).strip()
            algo_from_excel = str(row.get('Algoritmo', '')).strip()
            geo_score = row.get('Geometria', None)
            vf_score = row.get('Fedelta visiva', None)

            if not item_id or not algo_from_excel:
                continue

            # Try to match algorithm name
            matched_algo = None
            for algo in algorithms:
                # Check exact match or substring match
                if algo.lower() == algo_from_excel.lower() or algo_from_excel.lower() in algo.lower():
                    matched_algo = algo
                    break

            if not matched_algo:
                continue

            # Try to extract product_id from item_id (format: "product_id_variant" or just "product_id")
            parts = item_id.split('_')
            matched_pid = parts[0] if parts else item_id

            # Store data (convert from 0-100 to 0-1 scale if needed)
            data_map[(matched_pid, matched_algo)] = {
                'subj_geo': float(geo_score) / 100.0 if pd.notnull(geo_score) and geo_score != '' else None,
                'subj_vf': float(vf_score) / 100.0 if pd.notnull(vf_score) and vf_score != '' else None
            }

        return data_map

    except Exception as e:
        # Silently fail if Excel loading fails
        return {}


# ---------------------------------------------------------------
# Statistical Functions (Pure Python implementation)
# ---------------------------------------------------------------

def calculate_rank(data):
    """Assigns ranks to data, handling ties by assigning the average rank."""
    n = len(data)
    sorted_data = sorted([(v, i) for i, v in enumerate(data)], key=lambda x: x[0])
    ranks = [0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and sorted_data[j][0] == sorted_data[j+1][0]:
            j += 1
        rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[sorted_data[k][1]] = rank
        i = j + 1
    return ranks


def norm_cdf(x):
    """Cumulative distribution function for the standard normal distribution."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def mann_whitney_u(x, y):
    """Calculates Mann-Whitney U test p-value (two-sided)."""
    n1 = len(x)
    n2 = len(y)
    if n1 == 0 or n2 == 0:
        return 1.0
    combined = x + y
    ranks = calculate_rank(combined)
    r1 = sum(ranks[:n1])
    u1 = r1 - (n1 * (n1 + 1)) / 2.0
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    mu_u = n1 * n2 / 2.0
    tie_counts = {}
    for r in ranks:
        tie_counts[r] = tie_counts.get(r, 0) + 1
    tie_term = sum((t**3 - t) for t in tie_counts.values() if t > 1)
    n = n1 + n2
    sigma_u = math.sqrt((n1 * n2 / 12.0) * ((n + 1) - tie_term / (n * (n - 1))))
    if sigma_u == 0:
        return 1.0
    z = (u - mu_u) / sigma_u
    return 2 * (1 - norm_cdf(abs(z)))


def remove_outliers(data):
    """Removes outliers using the IQR method."""
    if len(data) < 4:
        return data
    sorted_data = sorted(data)
    q1 = sorted_data[int(len(data) * 0.25)]
    q3 = sorted_data[int(len(data) * 0.75)]
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    return [x for x in data if lower <= x <= upper]


def calculate_stats(data_list):
    """Calculates descriptive stats and pairwise MWU p-values."""
    if not data_list:
        return {}
    algos = {}
    for d in data_list:
        algo = d['algorithm']
        if algo not in algos:
            algos[algo] = {'fscore': [], 'vfscore': [], 'time': []}
        algos[algo]['fscore'].append(d['fscore'])
        algos[algo]['vfscore'].append(d['vfscore'])
        if d['time'] is not None:
            algos[algo]['time'].append(d['time'])

    algo_names = sorted(algos.keys())
    stats = {'descriptive': {}, 'pairwise': {'fscore': {}, 'vfscore': {}}}

    for algo in algo_names:
        stats['descriptive'][algo] = {}
        for metric in ['fscore', 'vfscore', 'time']:
            vals = algos[algo][metric]
            # Filter outliers ONLY for time
            if metric == 'time' and vals:
                vals = remove_outliers(vals)

            if vals:
                stats['descriptive'][algo][metric] = {
                    'mean': statistics.mean(vals),
                    'median': statistics.median(vals),
                    'std': statistics.stdev(vals) if len(vals) > 1 else 0,
                    'count': len(vals)
                }
            else:
                stats['descriptive'][algo][metric] = {'mean': 0, 'median': 0, 'std': 0, 'count': 0}

    for metric in ['fscore', 'vfscore']:
        for a1, a2 in combinations(algo_names, 2):
            v1 = algos[a1][metric]
            v2 = algos[a2][metric]
            p = mann_whitney_u(v1, v2)
            if a1 not in stats['pairwise'][metric]:
                stats['pairwise'][metric][a1] = {}
            if a2 not in stats['pairwise'][metric]:
                stats['pairwise'][metric][a2] = {}
            stats['pairwise'][metric][a1][a2] = p
            stats['pairwise'][metric][a2][a1] = p
    return stats


# ---------------------------------------------------------------
# HTML Report Generation
# ---------------------------------------------------------------

def build_html_report(run_id: str | None, paths: PathResolver) -> Path:
    """
    Generate an interactive HTML report with visualizations and statistical analysis.

    Args:
        run_id: Run identifier to filter results. If None, shows all runs.
        paths: PathResolver instance for workspace-aware path resolution.

    Returns:
        Path to the generated HTML file.
    """
    # Resolve input paths using PathResolver
    csv_path = paths.generations_csv_path()
    items_csv_path = paths.items_csv_path()

    # Load items data
    items_map = {}
    if items_csv_path.exists():
        with open(items_csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                row = {k.strip(): v for k, v in row.items()}
                pid = row.get('product_id')
                variant = row.get('variant', 'default')
                if pid:
                    key = f"{pid}_{variant}"
                    # Prefix image path with ../ for reports subfolder
                    img_path = row.get('image_1_path', '')
                    if img_path:
                        img_path = f"../{img_path}"

                    items_map[key] = {
                        'id': pid,
                        'variant': variant,
                        'name': row.get('product_name', 'N/A'),
                        'category_l1': row.get('category_l1', ''),
                        'category_l2': row.get('category_l2', ''),
                        'category_l3': row.get('category_l3', ''),
                        'image': img_path
                    }

    # Load and process generations data
    data = []
    run_ids = set()
    algorithms_per_run = {}

    if not csv_path.exists():
        raise FileNotFoundError(f"Generations CSV not found: {csv_path}")

    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k.strip(): v for k, v in row.items()}

            # Skip rows without metrics
            if not row.get('fscore') or row['fscore'].strip() == '':
                continue
            if not row.get('vfscore_overall') or row['vfscore_overall'].strip() == '':
                continue

            try:
                fscore = float(row['fscore'])
                vfscore_overall = float(row['vfscore_overall'])
                if vfscore_overall == 0:
                    continue
                vfscore = vfscore_overall / 100.0
                exec_time = None
                if row.get('generation_duration_s'):
                    try:
                        exec_time = float(row['generation_duration_s'])
                    except:
                        pass

                item_run_id = row.get('run_id', 'unknown_run')
                run_ids.add(item_run_id)

                algo = row.get('algo', 'Unknown')
                if item_run_id not in algorithms_per_run:
                    algorithms_per_run[item_run_id] = set()
                algorithms_per_run[item_run_id].add(algo)

                # Construct workspace-relative image paths with ../ prefix for reports subfolder
                vf_artifacts_dir = row.get('vf_artifacts_dir', '').replace('\\', '/')
                parts = vf_artifacts_dir.split('/')
                item_hash = parts[1] if len(parts) >= 2 else 'unknown'
                base_img_path = f"../runs/{item_run_id}/metrics/vfscore/{item_hash}/lpips_debug"

                data.append({
                    'run_id': item_run_id,
                    'id': row.get('product_id', 'N/A'),
                    'variant': row.get('variant', 'default'),
                    'name': row.get('product_name', 'N/A'),
                    'category_l1': row.get('category_l1', 'Uncategorized'),
                    'category_l2': row.get('category_l2', 'Uncategorized'),
                    'category_l3': row.get('category_l3', 'Uncategorized'),
                    'algorithm': algo,
                    'fscore': fscore,
                    'vfscore': vfscore,
                    'time': exec_time,
                    'gt_image': f"{base_img_path}/lpips_input_a_gt.png",
                    'render_image': f"{base_img_path}/lpips_input_b_render.png",
                    'subj_geo': None,
                    'subj_vf': None
                })
            except ValueError:
                continue

    # Load subjective evaluation data for each run
    subjective_data_per_run = {}
    for rid in run_ids:
        algos_list = list(algorithms_per_run.get(rid, []))
        subjective_data_per_run[rid] = load_subjective_data(rid, algos_list, paths)

    # Merge subjective data into main data
    for item in data:
        rid = item['run_id']
        pid = item['id']
        algo = item['algorithm']
        subj_data = subjective_data_per_run.get(rid, {})
        if (pid, algo) in subj_data:
            item['subj_geo'] = subj_data[(pid, algo)]['subj_geo']
            item['subj_vf'] = subj_data[(pid, algo)]['subj_vf']

    # Calculate statistics per run
    stats_per_run = {}
    for rid in run_ids:
        run_data = [d for d in data if d['run_id'] == rid]
        stats_per_run[rid] = calculate_stats(run_data)

    # Prepare JSON data for JavaScript
    json_data = json.dumps(data)
    json_items = json.dumps(list(items_map.values()))
    json_run_ids = json.dumps(sorted(list(run_ids), reverse=True))
    json_stats = json.dumps(stats_per_run)

    # Generate HTML content
    html_content = _generate_html_template(json_data, json_items, json_run_ids, json_stats)

    # Output path is always reports/report.html (report includes all runs)
    output_path = paths.reports_root / "report.html"

    # Ensure parent directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write HTML file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return output_path


def _generate_html_template(json_data: str, json_items: str, json_run_ids: str, json_stats: str) -> str:
    """Generate the HTML template with embedded data."""
    return f"""<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Report Generazione 3D</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <link href="https://cdn.datatables.net/1.13.6/css/dataTables.bootstrap5.min.css" rel="stylesheet">

    <style>
        body {{ font-family: 'Inter', sans-serif; background-color: #f8f9fa; color: #333; }}
        .container-fluid {{ padding: 2rem; max-width: 1600px; }}
        h1 {{ font-weight: 600; margin-bottom: 1.5rem; color: #111; }}
        .card {{ border: none; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 2rem; background: white; }}
        .card-header {{ background-color: white; border-bottom: 1px solid #eee; padding: 1rem 1.5rem; font-weight: 600; border-radius: 12px 12px 0 0 !important; }}
        .card-body {{ padding: 1.5rem; }}
        .nav-tabs .nav-link {{ color: #555; border: none; border-bottom: 2px solid transparent; margin-right: 1rem; padding-bottom: 0.5rem; }}
        .nav-tabs .nav-link.active {{ color: #000; border-bottom: 2px solid #000; font-weight: 600; }}
        .img-comparison {{ display: flex; gap: 10px; margin-top: 10px; }}
        .img-wrapper {{ flex: 1; text-align: center; }}
        .img-wrapper img {{ width: 100%; max-width: 200px; border-radius: 8px; border: 1px solid #eee; }}
        .img-label {{ font-size: 0.8rem; color: #666; margin-top: 5px; }}
        .score-badge {{ font-size: 0.9rem; padding: 0.3rem 0.6rem; border-radius: 6px; background-color: #e9ecef; font-weight: 600; }}
        .item-card:hover {{ transform: translateY(-2px); box-shadow: 0 6px 12px rgba(0,0,0,0.1); }}
        .help-box {{ background-color: #e7f1ff; border-left: 4px solid #0d6efd; padding: 1rem; margin-bottom: 1.5rem; border-radius: 4px; font-size: 0.95rem; }}
        .thumbnail-img {{ width: 60px; height: 60px; object-fit: cover; border-radius: 4px; }}

        /* Stats Table Styles */
        .stats-table th {{ background-color: #f8f9fa; }}
        .p-val-cell {{ font-weight: bold; }}
        .p-sig-high {{ background-color: #d4edda !important; color: #155724; }} /* p < 0.001 */
        .p-sig-med {{ background-color: #fff3cd !important; color: #856404; }} /* p < 0.05 */
        .p-sig-low {{ background-color: #f8d7da !important; color: #721c24; }} /* Not significant */
    </style>
</head>
<body>

<div class="container-fluid">
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h1>Report Generazione 3D</h1>
        <div class="text-muted" id="runIdDisplay">Test run ID: Loading...</div>
    </div>

    <!-- Controls -->
    <div class="card mb-4">
        <div class="card-body d-flex align-items-center gap-3 flex-wrap">
            <div class="d-flex align-items-center gap-2">
                <label for="runSelect" class="form-label mb-0 fw-bold">Test Run ID:</label>
                <select id="runSelect" class="form-select w-auto"></select>
            </div>
            <div class="vr mx-2"></div>
            <div class="d-flex align-items-center gap-2">
                <label for="groupSelect" class="form-label mb-0 fw-bold">Raggruppa per:</label>
                <select id="groupSelect" class="form-select w-auto">
                    <option value="category_l1">Categoria L1</option>
                    <option value="category_l2">Categoria L2</option>
                    <option value="category_l3">Categoria L3</option>
                </select>
            </div>
            <div class="vr mx-2"></div>
            <div class="form-check form-switch">
                <input class="form-check-input" type="checkbox" id="outlierSwitch">
                <label class="form-check-label" for="outlierSwitch">Mostra Outlier</label>
            </div>
        </div>
    </div>

    <!-- Tabs -->
    <ul class="nav nav-tabs mb-4" id="reportTabs" role="tablist">
        <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#obj-boxplot-pane">Obiettivo: Box Plots</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#obj-stats-pane">Obiettivo: Statistiche</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#obj-sparse-pane">Obiettivo: Confronto</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#subj-boxplot-pane">Soggettivo: Box Plots</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#subj-stats-pane">Soggettivo: Statistiche</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#subj-sparse-pane">Soggettivo: Confronto</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#comparison-tab">Obiettivo vs Soggettivo</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#comparison-pane">Confronto Visivo</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#recap-pane">Riepilogo</button></li>
    </ul>

    <div class="tab-content">

        <!-- Obiettivo: Box Plots Section -->
        <div class="tab-pane fade show active" id="obj-boxplot-pane">
            <div class="help-box">
                <strong>Come leggere il Box Plot:</strong>
                <ul class="mb-0 mt-1 ps-3">
                    <li>La <strong>linea centrale</strong> rappresenta la mediana.</li>
                    <li>Il <strong>box</strong> racchiude il 50% centrale (25-75 percentile).</li>
                    <li>I <strong>baffi</strong> indicano l'intervallo (esclusi outlier).</li>
                    <li>Usa "Mostra Outlier" per vedere i punti anomali.</li>
                </ul>
            </div>
            <div class="row">
                <div class="col-md-6 mb-4">
                    <div class="card h-100">
                        <div class="card-header">F-Score per Categoria</div>
                        <div class="card-body"><div id="fscoreBoxPlot" style="height: 500px;"></div></div>
                    </div>
                </div>
                <div class="col-md-6 mb-4">
                    <div class="card h-100">
                        <div class="card-header">VF-Score per Categoria</div>
                        <div class="card-body"><div id="vfscoreBoxPlot" style="height: 500px;"></div></div>
                    </div>
                </div>
                <div class="col-12">
                    <div class="card">
                        <div class="card-header">Tempi di Generazione (Secondi)</div>
                        <div class="card-body"><div id="timeBoxPlot" style="height: 500px;"></div></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Obiettivo: Stats Section -->
        <div class="tab-pane fade" id="obj-stats-pane">
            <div class="help-box">
                <strong>Guida alla lettura delle Statistiche:</strong>
                <ul class="mb-0 mt-1 ps-3">
                    <li><strong>Statistiche Descrittive:</strong> Mostra la media e la deviazione standard (+-) per ogni metrica. <em>Nota: Per i tempi di esecuzione, gli outlier sono stati rimossi per fornire una stima piu stabile.</em></li>
                    <li><strong>Test di Mann-Whitney U:</strong> Verifica se la differenza tra due algoritmi e significativa.</li>
                    <li><strong>Interpretazione p-value:</strong>
                        <ul class="mb-0">
                            <li><span class="badge bg-success text-white">Verde (p < 0.001)</span>: Differenza <strong>molto significativa</strong>. E estremamente improbabile che sia dovuta al caso.</li>
                            <li><span class="badge bg-warning text-dark">Giallo (p < 0.05)</span>: Differenza <strong>significativa</strong>. C'e una buona probabilita che un algoritmo sia effettivamente diverso dall'altro.</li>
                            <li><span class="badge bg-danger text-white">Rosso</span>: <strong>Non significativo</strong>. Le differenze osservate potrebbero essere casuali; i due algoritmi hanno prestazioni simili.</li>
                        </ul>
                    </li>
                </ul>
            </div>

            <div class="row">
                <div class="col-12 mb-4">
                    <div class="card">
                        <div class="card-header">Statistiche Descrittive (Media +- Dev.Std) - Outlier Rimossi per il Tempo</div>
                        <div class="card-body">
                            <div class="table-responsive">
                                <table class="table table-bordered stats-table" id="descStatsTable">
                                    <thead><tr><th>Algoritmo</th><th>F-Score</th><th>VF-Score</th><th>Tempo (s)</th><th>Campioni</th></tr></thead>
                                    <tbody></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">Significativita Pairwise (Mann-Whitney U) - F-Score</div>
                        <div class="card-body">
                            <div class="table-responsive"><table class="table table-bordered text-center" id="mwFscoreTable"></table></div>
                        </div>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">Significativita Pairwise (Mann-Whitney U) - VF-Score</div>
                        <div class="card-body">
                            <div class="table-responsive"><table class="table table-bordered text-center" id="mwVfscoreTable"></table></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Obiettivo: Sparse Plots -->
        <div class="tab-pane fade" id="obj-sparse-pane">
            <div class="help-box">
                <strong>Guida al Confronto Algoritmi:</strong>
                <ul class="mb-0 mt-1 ps-3">
                    <li><strong>Scatter Plot (Sinistra):</strong> Mostra ogni singola generazione. Utile per vedere la dispersione.</li>
                    <li><strong>Centro di Massa (Destra):</strong> Mostra il punto medio (media F-Score, media VF-Score) per ogni algoritmo. La dimensione del punto indica il numero di campioni.</li>
                    <li><strong>Leaderboard:</strong> Classifica gli algoritmi basandosi sulla distanza dal punto ideale (1.0, 1.0). Minore e la distanza, migliore e il compromesso tra qualita geometrica (F-Score) e visiva (VF-Score).</li>
                </ul>
            </div>
            <div class="row">
                <div class="col-lg-6 mb-4">
                    <div class="card h-100">
                        <div class="card-header">Distribuzione Punti (F-Score vs VF-Score)</div>
                        <div class="card-body d-flex justify-content-center">
                            <div id="sparsePlot" style="width: 100%; aspect-ratio: 1/1;"></div>
                        </div>
                    </div>
                </div>
                <div class="col-lg-6 mb-4">
                    <div class="card h-100">
                        <div class="card-header">Centro di Massa (Media F-Score vs Media VF-Score)</div>
                        <div class="card-body d-flex justify-content-center">
                            <div id="centerOfMassPlot" style="width: 100%; aspect-ratio: 1/1;"></div>
                        </div>
                    </div>
                </div>
                <div class="col-12">
                    <div class="card">
                        <div class="card-header">Leaderboard (Distanza dall'Ideale [1.0, 1.0])</div>
                        <div class="card-body">
                            <div class="table-responsive">
                                <table class="table table-striped table-hover" id="leaderboardTable">
                                    <thead>
                                        <tr>
                                            <th>Posizione</th>
                                            <th>Algoritmo</th>
                                            <th>Distanza dall'Ideale</th>
                                            <th>F-Score Medio</th>
                                            <th>VF-Score Medio</th>
                                            <th>Campioni</th>
                                        </tr>
                                    </thead>
                                    <tbody></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Soggettivo: Box Plots Section -->
        <div class="tab-pane fade" id="subj-boxplot-pane">
            <div class="help-box">
                <strong>Valutazione Soggettiva:</strong>
                <p class="mb-0">I dati soggettivi provengono dalle valutazioni umane caricate dal file Excel "Subjective evaluation.xlsx". Se non sono presenti dati soggettivi per il run selezionato, le visualizzazioni risulteranno vuote.</p>
            </div>
            <div class="row">
                <div class="col-md-6 mb-4">
                    <div class="card h-100">
                        <div class="card-header">Geometria Soggettiva per Categoria</div>
                        <div class="card-body"><div id="subjGeoBoxPlot" style="height: 500px;"></div></div>
                    </div>
                </div>
                <div class="col-md-6 mb-4">
                    <div class="card h-100">
                        <div class="card-header">Fedelta Visiva Soggettiva per Categoria</div>
                        <div class="card-body"><div id="subjVfBoxPlot" style="height: 500px;"></div></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Soggettivo: Stats Section -->
        <div class="tab-pane fade" id="subj-stats-pane">
            <div class="help-box">
                <strong>Statistiche Soggettive:</strong>
                <p class="mb-0">Analisi statistica delle valutazioni umane. Le statistiche sono calcolate solo sugli elementi che hanno valutazioni soggettive disponibili.</p>
            </div>
            <div class="row">
                <div class="col-12 mb-4">
                    <div class="card">
                        <div class="card-header">Statistiche Descrittive Soggettive (Media +- Dev.Std)</div>
                        <div class="card-body">
                            <div class="table-responsive">
                                <table class="table table-bordered stats-table" id="subjDescStatsTable">
                                    <thead><tr><th>Algoritmo</th><th>Geometria Soggettiva</th><th>Fedelta Visiva Soggettiva</th><th>Campioni</th></tr></thead>
                                    <tbody></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">Significativita Pairwise (Mann-Whitney U) - Geometria Soggettiva</div>
                        <div class="card-body">
                            <div class="table-responsive"><table class="table table-bordered text-center" id="mwSubjGeoTable"></table></div>
                        </div>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">Significativita Pairwise (Mann-Whitney U) - Fedelta Visiva Soggettiva</div>
                        <div class="card-body">
                            <div class="table-responsive"><table class="table table-bordered text-center" id="mwSubjVfTable"></table></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Soggettivo: Sparse Plots -->
        <div class="tab-pane fade" id="subj-sparse-pane">
            <div class="help-box">
                <strong>Confronto Algoritmi (Valutazioni Soggettive):</strong>
                <p class="mb-0">Analisi della relazione tra geometria e fedelta visiva basata su valutazioni umane.</p>
            </div>
            <div class="row">
                <div class="col-lg-6 mb-4">
                    <div class="card h-100">
                        <div class="card-header">Distribuzione Punti (Geo Soggettiva vs VF Soggettiva)</div>
                        <div class="card-body d-flex justify-content-center">
                            <div id="subjSparsePlot" style="width: 100%; aspect-ratio: 1/1;"></div>
                        </div>
                    </div>
                </div>
                <div class="col-lg-6 mb-4">
                    <div class="card h-100">
                        <div class="card-header">Centro di Massa Soggettivo</div>
                        <div class="card-body d-flex justify-content-center">
                            <div id="subjCenterOfMassPlot" style="width: 100%; aspect-ratio: 1/1;"></div>
                        </div>
                    </div>
                </div>
                <div class="col-12">
                    <div class="card">
                        <div class="card-header">Leaderboard Soggettivo (Distanza dall'Ideale [1.0, 1.0])</div>
                        <div class="card-body">
                            <div class="table-responsive">
                                <table class="table table-striped table-hover" id="subjLeaderboardTable">
                                    <thead>
                                        <tr>
                                            <th>Posizione</th>
                                            <th>Algoritmo</th>
                                            <th>Distanza dall'Ideale</th>
                                            <th>Geometria Soggettiva Media</th>
                                            <th>VF Soggettiva Media</th>
                                            <th>Campioni</th>
                                        </tr>
                                    </thead>
                                    <tbody></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Obiettivo vs Soggettivo Comparison -->
        <div class="tab-pane fade" id="comparison-tab">
            <div class="help-box">
                <strong>Correlazione Obiettivo vs Soggettivo:</strong>
                <p class="mb-0">Analizza la correlazione tra metriche obiettive (FScore, VFScore) e valutazioni soggettive umane usando il coefficiente di Spearman.</p>
            </div>
            <div class="row">
                <div class="col-lg-6 mb-4">
                    <div class="card h-100">
                        <div class="card-header">Correlazione FScore vs Geometria Soggettiva</div>
                        <div class="card-body">
                            <div id="corrFScoreGeo" style="width: 100%; aspect-ratio: 1/1;"></div>
                            <p class="text-center mt-2" id="corrFScoreGeoText"></p>
                        </div>
                    </div>
                </div>
                <div class="col-lg-6 mb-4">
                    <div class="card h-100">
                        <div class="card-header">Correlazione VFScore vs Fedelta Visiva Soggettiva</div>
                        <div class="card-body">
                            <div id="corrVFScoreVf" style="width: 100%; aspect-ratio: 1/1;"></div>
                            <p class="text-center mt-2" id="corrVFScoreVfText"></p>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Comparison -->
        <div class="tab-pane fade" id="comparison-pane">
            <div class="card">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span>Confronto GT vs Render</span>
                    <input type="text" id="searchBox" class="form-control w-auto" placeholder="Cerca ID o Nome...">
                </div>
                <div class="card-body">
                    <div id="comparisonGrid" class="row g-4"></div>
                    <div id="paginationControls" class="mt-4 d-flex justify-content-center gap-2 align-items-center flex-wrap"></div>
                </div>
            </div>
        </div>

        <!-- Recap -->
        <div class="tab-pane fade" id="recap-pane">
            <div class="card">
                <div class="card-header">Riepilogo Articoli</div>
                <div class="card-body">
                    <div class="table-responsive">
                        <table id="recapTable" class="table table-hover align-middle">
                            <thead><tr><th>Immagine</th><th>ID</th><th>Variante</th><th>Nome</th><th>Categoria L1</th><th>Categoria L2</th><th>Categoria L3</th></tr></thead>
                            <tbody></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/dataTables.bootstrap5.min.js"></script>

<script>
    const allData = {json_data};
    const itemsData = {json_items};
    const runIds = {json_run_ids};
    const statsData = {json_stats};

    let currentRun = runIds.length > 0 ? runIds[0] : '';
    let currentGroup = 'category_l1';
    let currentPage = 1;
    const itemsPerPage = 20;
    let showOutliers = false;

    let runData = [];
    let filteredData = [];

    // Spearman correlation calculation
    function spearmanCorrelation(x, y) {{
        if (x.length !== y.length || x.length === 0) return {{ rho: null, p: null }};

        const n = x.length;
        const rankX = getRanks(x);
        const rankY = getRanks(y);

        let sumD2 = 0;
        for (let i = 0; i < n; i++) {{
            const d = rankX[i] - rankY[i];
            sumD2 += d * d;
        }}

        const rho = 1 - (6 * sumD2) / (n * (n * n - 1));

        // Approximate p-value using t-distribution
        const t = rho * Math.sqrt((n - 2) / (1 - rho * rho));
        const p = 2 * (1 - tCDF(Math.abs(t), n - 2));

        return {{ rho, p }};
    }}

    function getRanks(arr) {{
        const sorted = arr.map((v, i) => ({{value: v, index: i}})).sort((a, b) => a.value - b.value);
        const ranks = new Array(arr.length);
        let i = 0;
        while (i < arr.length) {{
            let j = i;
            while (j < arr.length - 1 && sorted[j].value === sorted[j + 1].value) j++;
            const rank = (i + 1 + j + 1) / 2;
            for (let k = i; k <= j; k++) ranks[sorted[k].index] = rank;
            i = j + 1;
        }}
        return ranks;
    }}

    function tCDF(t, df) {{
        // Approximate t-distribution CDF for p-value calculation
        const x = df / (df + t * t);
        return 1 - 0.5 * betaInc(df / 2, 0.5, x);
    }}

    function betaInc(a, b, x) {{
        // Incomplete beta function approximation
        if (x === 0) return 0;
        if (x === 1) return 1;
        const bt = Math.exp(gammaLn(a + b) - gammaLn(a) - gammaLn(b) + a * Math.log(x) + b * Math.log(1 - x));
        if (x < (a + 1) / (a + b + 2)) return bt * betaCF(x, a, b) / a;
        return 1 - bt * betaCF(1 - x, b, a) / b;
    }}

    function betaCF(x, a, b) {{
        const maxIter = 100;
        const eps = 3e-7;
        const qab = a + b;
        const qap = a + 1;
        const qam = a - 1;
        let c = 1;
        let d = 1 - qab * x / qap;
        if (Math.abs(d) < eps) d = eps;
        d = 1 / d;
        let h = d;
        for (let m = 1; m <= maxIter; m++) {{
            const m2 = 2 * m;
            let aa = m * (b - m) * x / ((qam + m2) * (a + m2));
            d = 1 + aa * d;
            if (Math.abs(d) < eps) d = eps;
            c = 1 + aa / c;
            if (Math.abs(c) < eps) c = eps;
            d = 1 / d;
            h *= d * c;
            aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2));
            d = 1 + aa * d;
            if (Math.abs(d) < eps) d = eps;
            c = 1 + aa / c;
            if (Math.abs(c) < eps) c = eps;
            d = 1 / d;
            const del = d * c;
            h *= del;
            if (Math.abs(del - 1) < eps) break;
        }}
        return h;
    }}

    function gammaLn(x) {{
        const cof = [76.18009172947146, -86.50532032941677, 24.01409824083091, -1.231739572450155, 0.1208650973866179e-2, -0.5395239384953e-5];
        let y = x;
        let tmp = x + 5.5;
        tmp -= (x + 0.5) * Math.log(tmp);
        let ser = 1.000000000190015;
        for (let j = 0; j < 6; j++) ser += cof[j] / ++y;
        return -tmp + Math.log(2.5066282746310005 * ser / x);
    }}

    document.addEventListener('DOMContentLoaded', () => {{
        initRunSelect();
        updateRunData();

        document.getElementById('runSelect').addEventListener('change', (e) => {{ currentRun = e.target.value; updateRunData(); }});
        document.getElementById('groupSelect').addEventListener('change', (e) => {{ currentGroup = e.target.value; updatePlots(); renderSubjectivePlots(); }});
        document.getElementById('outlierSwitch').addEventListener('change', (e) => {{ showOutliers = e.target.checked; updatePlots(); renderSubjectivePlots(); }});

        document.getElementById('searchBox').addEventListener('input', (e) => {{
            const term = e.target.value.toLowerCase();
            filteredData = runData.filter(item => item.name.toLowerCase().includes(term) || item.id.toString().includes(term));
            currentPage = 1;
            renderComparison();
        }});

        renderRecap();
    }});

    function initRunSelect() {{
        const select = document.getElementById('runSelect');
        runIds.forEach(id => {{
            const option = document.createElement('option');
            option.value = id;
            option.text = id;
            select.appendChild(option);
        }});
        if (runIds.length > 0) select.value = runIds[0];
    }}

    function updateRunData() {{
        runData = allData.filter(d => d.run_id === currentRun);
        filteredData = [...runData];
        document.getElementById('runIdDisplay').innerText = `Test run ID: ${{currentRun}}`;
        currentPage = 1;
        document.getElementById('searchBox').value = '';
        updatePlots();
        renderStats();
        renderSubjectivePlots();
        renderSubjectiveStats();
        renderCorrelation();
        renderComparison();
    }}

    function updatePlots() {{
        const algorithms = [...new Set(runData.map(d => d.algorithm))].sort();
        const boxPoints = showOutliers ? 'outliers' : false;

        const createBoxTraces = (metric, name) => algorithms.map(algo => {{
            const algoData = runData.filter(d => d.algorithm === algo);
            return {{
                y: algoData.map(d => d[metric]),
                x: algoData.map(d => d[currentGroup]),
                type: 'box',
                name: algo,
                boxpoints: boxPoints,
                jitter: 0.3,
                pointpos: 0
            }};
        }});

        const layout = {{
            boxmode: 'group',
            margin: {{t: 50, b: 100}},
            showlegend: true,
            legend: {{orientation: 'h', y: -0.2}}
        }};

        const config = {{responsive: true}};

        Plotly.newPlot('fscoreBoxPlot', createBoxTraces('fscore'), {{...layout, title: 'F-Score Distribution', yaxis: {{range: [0, 1.05]}}}}, config);
        Plotly.newPlot('vfscoreBoxPlot', createBoxTraces('vfscore'), {{...layout, title: 'VF-Score Distribution', yaxis: {{range: [0, 1.05]}}}}, config);
        Plotly.newPlot('timeBoxPlot', createBoxTraces('time'), {{...layout, title: 'Execution Time Distribution', yaxis: {{title: 'Seconds'}}}}, config);

        // Sparse Plot
        const sparseTraces = algorithms.map(algo => {{
            const algoData = runData.filter(d => d.algorithm === algo);
            return {{
                x: algoData.map(d => d.vfscore),
                y: algoData.map(d => d.fscore),
                mode: 'markers',
                type: 'scatter',
                name: algo,
                text: algoData.map(d => d.name),
                marker: {{size: 8, opacity: 0.7}}
            }};
        }});

        const squareLayout = {{
            xaxis: {{title: 'VF-Score', range: [0, 1.05], constrain: 'domain'}},
            yaxis: {{title: 'F-Score', range: [0, 1.05], scaleanchor: 'x', scaleratio: 1}},
            margin: {{t: 60, b: 60, l: 60, r: 60}},
            hovermode: 'closest',
            autosize: true
        }};

        Plotly.newPlot('sparsePlot', sparseTraces, {{...squareLayout, title: 'F-Score vs VF-Score'}}, config);

        // Center of Mass Plot
        const comData = algorithms.map(algo => {{
            const algoData = runData.filter(d => d.algorithm === algo);
            const meanF = algoData.reduce((sum, d) => sum + d.fscore, 0) / algoData.length;
            const meanVF = algoData.reduce((sum, d) => sum + d.vfscore, 0) / algoData.length;
            return {{ algo, meanF, meanVF, count: algoData.length }};
        }});

        const comTraces = comData.map(d => ({{
            x: [d.meanVF],
            y: [d.meanF],
            mode: 'markers',
            type: 'scatter',
            name: d.algo,
            text: [`${{d.algo}}<br>F: ${{d.meanF.toFixed(3)}}<br>VF: ${{d.meanVF.toFixed(3)}}<br>N: ${{d.count}}`],
            marker: {{ size: Math.sqrt(d.count) * 3, opacity: 0.9, line: {{width: 1, color: 'black'}} }}
        }}));

        Plotly.newPlot('centerOfMassPlot', comTraces, {{...squareLayout, title: 'Centro di Massa (Media)'}}, config);

        // Leaderboard
        const leaderboardBody = document.querySelector('#leaderboardTable tbody');
        leaderboardBody.innerHTML = '';

        // Calculate distance to (1,1)
        const ranked = comData.map(d => {{
            const dist = Math.sqrt(Math.pow(1 - d.meanF, 2) + Math.pow(1 - d.meanVF, 2));
            return {{ ...d, dist }};
        }}).sort((a, b) => a.dist - b.dist);

        ranked.forEach((d, i) => {{
            leaderboardBody.innerHTML += `<tr>
                <td>${{i + 1}}</td>
                <td><strong>${{d.algo}}</strong></td>
                <td>${{d.dist.toFixed(4)}}</td>
                <td>${{d.meanF.toFixed(4)}}</td>
                <td>${{d.meanVF.toFixed(4)}}</td>
                <td>${{d.count}}</td>
            </tr>`;
        }});
    }}

    function renderStats() {{
        const stats = statsData[currentRun];
        if (!stats) return;

        // Descriptive
        const descBody = document.querySelector('#descStatsTable tbody');
        descBody.innerHTML = '';
        Object.keys(stats.descriptive).sort().forEach(algo => {{
            const s = stats.descriptive[algo];
            descBody.innerHTML += `<tr>
                <td>${{algo}}</td>
                <td>${{s.fscore.mean.toFixed(3)}} +- ${{s.fscore.std.toFixed(3)}}</td>
                <td>${{s.vfscore.mean.toFixed(3)}} +- ${{s.vfscore.std.toFixed(3)}}</td>
                <td>${{s.time.mean.toFixed(1)}}s +- ${{s.time.std.toFixed(1)}}s</td>
                <td>${{s.fscore.count}}</td>
            </tr>`;
        }});

        // Pairwise Heatmaps
        const renderHeatmap = (tableId, metric) => {{
            const table = document.getElementById(tableId);
            const algos = Object.keys(stats.pairwise[metric]).sort();
            let html = '<thead><tr><th></th>';
            algos.forEach(a => html += `<th>${{a}}</th>`);
            html += '</tr></thead><tbody>';

            algos.forEach(a1 => {{
                html += `<tr><th>${{a1}}</th>`;
                algos.forEach(a2 => {{
                    if (a1 === a2) {{
                        html += '<td class="bg-light">-</td>';
                    }} else {{
                        const p = stats.pairwise[metric][a1][a2];
                        let cls = 'p-sig-low';
                        if (p < 0.001) cls = 'p-sig-high';
                        else if (p < 0.05) cls = 'p-sig-med';
                        html += `<td class="${{cls}} p-val-cell">${{p.toFixed(4)}}</td>`;
                    }}
                }});
                html += '</tr>';
            }});
            html += '</tbody>';
            table.innerHTML = html;
        }};

        renderHeatmap('mwFscoreTable', 'fscore');
        renderHeatmap('mwVfscoreTable', 'vfscore');
    }}

    function renderSubjectivePlots() {{
        const subjData = runData.filter(d => d.subj_geo !== null || d.subj_vf !== null);
        if (subjData.length === 0) {{
            // No subjective data available
            Plotly.purge('subjGeoBoxPlot');
            Plotly.purge('subjVfBoxPlot');
            document.getElementById('subjGeoBoxPlot').innerHTML = '<p class="text-center text-muted">Nessun dato soggettivo disponibile per questo run.</p>';
            document.getElementById('subjVfBoxPlot').innerHTML = '<p class="text-center text-muted">Nessun dato soggettivo disponibile per questo run.</p>';
            return;
        }}

        const algorithms = [...new Set(subjData.map(d => d.algorithm))].sort();
        const boxPoints = showOutliers ? 'outliers' : false;

        const createSubjBoxTraces = (metric, name) => algorithms.map(algo => {{
            const algoData = subjData.filter(d => d.algorithm === algo && d[metric] !== null);
            return {{
                y: algoData.map(d => d[metric]),
                x: algoData.map(d => d[currentGroup]),
                type: 'box',
                name: algo,
                boxpoints: boxPoints,
                jitter: 0.3,
                pointpos: 0
            }};
        }});

        const layout = {{
            boxmode: 'group',
            margin: {{t: 50, b: 100}},
            showlegend: true,
            legend: {{orientation: 'h', y: -0.2}},
            yaxis: {{range: [0, 1.05]}}
        }};

        const config = {{responsive: true}};

        Plotly.newPlot('subjGeoBoxPlot', createSubjBoxTraces('subj_geo'), {{...layout, title: 'Geometria Soggettiva'}}, config);
        Plotly.newPlot('subjVfBoxPlot', createSubjBoxTraces('subj_vf'), {{...layout, title: 'Fedelta Visiva Soggettiva'}}, config);

        // Subjective sparse plots
        const sparseTraces = algorithms.map(algo => {{
            const algoData = subjData.filter(d => d.algorithm === algo && d.subj_geo !== null && d.subj_vf !== null);
            return {{
                x: algoData.map(d => d.subj_vf),
                y: algoData.map(d => d.subj_geo),
                mode: 'markers',
                type: 'scatter',
                name: algo,
                text: algoData.map(d => d.name),
                marker: {{size: 8, opacity: 0.7}}
            }};
        }});

        const squareLayout = {{
            xaxis: {{title: 'VF Soggettiva', range: [0, 1.05], constrain: 'domain'}},
            yaxis: {{title: 'Geometria Soggettiva', range: [0, 1.05], scaleanchor: 'x', scaleratio: 1}},
            margin: {{t: 60, b: 60, l: 60, r: 60}},
            hovermode: 'closest',
            autosize: true
        }};

        Plotly.newPlot('subjSparsePlot', sparseTraces, {{...squareLayout, title: 'Geo Sogg vs VF Sogg'}}, config);

        // Subjective center of mass
        const comData = algorithms.map(algo => {{
            const algoData = subjData.filter(d => d.algorithm === algo && d.subj_geo !== null && d.subj_vf !== null);
            if (algoData.length === 0) return null;
            const meanGeo = algoData.reduce((sum, d) => sum + d.subj_geo, 0) / algoData.length;
            const meanVf = algoData.reduce((sum, d) => sum + d.subj_vf, 0) / algoData.length;
            return {{ algo, meanGeo, meanVf, count: algoData.length }};
        }}).filter(d => d !== null);

        const comTraces = comData.map(d => ({{
            x: [d.meanVf],
            y: [d.meanGeo],
            mode: 'markers',
            type: 'scatter',
            name: d.algo,
            text: [`${{d.algo}}<br>Geo: ${{d.meanGeo.toFixed(3)}}<br>VF: ${{d.meanVf.toFixed(3)}}<br>N: ${{d.count}}`],
            marker: {{ size: Math.sqrt(d.count) * 3, opacity: 0.9, line: {{width: 1, color: 'black'}} }}
        }}));

        Plotly.newPlot('subjCenterOfMassPlot', comTraces, {{...squareLayout, title: 'Centro di Massa Soggettivo'}}, config);

        // Subjective leaderboard
        const leaderboardBody = document.querySelector('#subjLeaderboardTable tbody');
        leaderboardBody.innerHTML = '';

        const ranked = comData.map(d => {{
            const dist = Math.sqrt(Math.pow(1 - d.meanGeo, 2) + Math.pow(1 - d.meanVf, 2));
            return {{ ...d, dist }};
        }}).sort((a, b) => a.dist - b.dist);

        ranked.forEach((d, i) => {{
            leaderboardBody.innerHTML += `<tr>
                <td>${{i + 1}}</td>
                <td><strong>${{d.algo}}</strong></td>
                <td>${{d.dist.toFixed(4)}}</td>
                <td>${{d.meanGeo.toFixed(4)}}</td>
                <td>${{d.meanVf.toFixed(4)}}</td>
                <td>${{d.count}}</td>
            </tr>`;
        }});
    }}

    function renderSubjectiveStats() {{
        const subjData = runData.filter(d => d.subj_geo !== null || d.subj_vf !== null);
        if (subjData.length === 0) return;

        // Group by algorithm
        const algos = {{}};
        subjData.forEach(d => {{
            if (!algos[d.algorithm]) algos[d.algorithm] = {{ subj_geo: [], subj_vf: [] }};
            if (d.subj_geo !== null) algos[d.algorithm].subj_geo.push(d.subj_geo);
            if (d.subj_vf !== null) algos[d.algorithm].subj_vf.push(d.subj_vf);
        }});

        const algoNames = Object.keys(algos).sort();

        // Descriptive stats
        const descBody = document.querySelector('#subjDescStatsTable tbody');
        descBody.innerHTML = '';
        algoNames.forEach(algo => {{
            const geo = algos[algo].subj_geo;
            const vf = algos[algo].subj_vf;
            const geoMean = geo.length > 0 ? geo.reduce((a, b) => a + b) / geo.length : 0;
            const vfMean = vf.length > 0 ? vf.reduce((a, b) => a + b) / vf.length : 0;
            const geoStd = geo.length > 1 ? Math.sqrt(geo.reduce((sum, v) => sum + Math.pow(v - geoMean, 2), 0) / (geo.length - 1)) : 0;
            const vfStd = vf.length > 1 ? Math.sqrt(vf.reduce((sum, v) => sum + Math.pow(v - vfMean, 2), 0) / (vf.length - 1)) : 0;
            const count = Math.max(geo.length, vf.length);

            descBody.innerHTML += `<tr>
                <td>${{algo}}</td>
                <td>${{geoMean.toFixed(3)}} +- ${{geoStd.toFixed(3)}}</td>
                <td>${{vfMean.toFixed(3)}} +- ${{vfStd.toFixed(3)}}</td>
                <td>${{count}}</td>
            </tr>`;
        }});

        // Pairwise heatmaps (simplified Mann-Whitney U for subjective data)
        // Note: Reusing the calculate_rank and mann_whitney_u functions from Python would require reimplementation
        // For now, just show placeholders or simplified stats
        document.getElementById('mwSubjGeoTable').innerHTML = '<p class="text-center text-muted">Statistiche pairwise non ancora implementate per dati soggettivi.</p>';
        document.getElementById('mwSubjVfTable').innerHTML = '<p class="text-center text-muted">Statistiche pairwise non ancora implementate per dati soggettivi.</p>';
    }}

    function renderCorrelation() {{
        const corrData = runData.filter(d =>
            d.fscore !== null && d.vfscore !== null &&
            d.subj_geo !== null && d.subj_vf !== null
        );

        if (corrData.length < 3) {{
            // Not enough data for meaningful correlation
            document.getElementById('corrFScoreGeo').innerHTML = '<p class="text-center text-muted">Dati insufficienti per il calcolo della correlazione (min 3 campioni).</p>';
            document.getElementById('corrVFScoreVf').innerHTML = '<p class="text-center text-muted">Dati insufficienti per il calcolo della correlazione (min 3 campioni).</p>';
            document.getElementById('corrFScoreGeoText').innerHTML = '';
            document.getElementById('corrVFScoreVfText').innerHTML = '';
            return;
        }}

        // FScore vs Subjective Geometry
        const fscoreVals = corrData.map(d => d.fscore);
        const subjGeoVals = corrData.map(d => d.subj_geo);
        const corrFGeo = spearmanCorrelation(fscoreVals, subjGeoVals);

        const traceFGeo = {{
            x: fscoreVals,
            y: subjGeoVals,
            mode: 'markers',
            type: 'scatter',
            text: corrData.map(d => d.name),
            marker: {{ size: 8, opacity: 0.6 }}
        }};

        Plotly.newPlot('corrFScoreGeo', [traceFGeo], {{
            xaxis: {{ title: 'F-Score' }},
            yaxis: {{ title: 'Geometria Soggettiva' }},
            margin: {{ t: 40, b: 60, l: 60, r: 40 }}
        }}, {{ responsive: true }});

        document.getElementById('corrFScoreGeoText').innerHTML = corrFGeo.rho !== null
            ? `<strong>Spearman rho:</strong> ${{corrFGeo.rho.toFixed(3)}} | <strong>p-value:</strong> ${{corrFGeo.p.toFixed(4)}}`
            : 'Correlazione non calcolabile';

        // VFScore vs Subjective VF
        const vfscoreVals = corrData.map(d => d.vfscore);
        const subjVfVals = corrData.map(d => d.subj_vf);
        const corrVFVf = spearmanCorrelation(vfscoreVals, subjVfVals);

        const traceVFVf = {{
            x: vfscoreVals,
            y: subjVfVals,
            mode: 'markers',
            type: 'scatter',
            text: corrData.map(d => d.name),
            marker: {{ size: 8, opacity: 0.6 }}
        }};

        Plotly.newPlot('corrVFScoreVf', [traceVFVf], {{
            xaxis: {{ title: 'VF-Score' }},
            yaxis: {{ title: 'Fedelta Visiva Soggettiva' }},
            margin: {{ t: 40, b: 60, l: 60, r: 40 }}
        }}, {{ responsive: true }});

        document.getElementById('corrVFScoreVfText').innerHTML = corrVFVf.rho !== null
            ? `<strong>Spearman rho:</strong> ${{corrVFVf.rho.toFixed(3)}} | <strong>p-value:</strong> ${{corrVFVf.p.toFixed(4)}}`
            : 'Correlazione non calcolabile';
    }}

    function renderComparison() {{
        const grid = document.getElementById('comparisonGrid');
        grid.innerHTML = '';
        const sortedData = [...filteredData].sort((a, b) => b.vfscore - a.vfscore);
        const start = (currentPage - 1) * itemsPerPage;
        const pageItems = sortedData.slice(start, start + itemsPerPage);

        pageItems.forEach(item => {{
            const col = document.createElement('div');
            col.className = 'col-md-6 col-lg-4 col-xl-3';
            col.innerHTML = `
                <div class="card item-card h-100">
                    <div class="card-body">
                        <div class="d-flex justify-content-between align-items-start mb-2">
                            <h6 class="card-title mb-0 text-truncate" title="${{item.name}}">${{item.name}}</h6>
                            <span class="badge bg-secondary">${{item.id}}</span>
                        </div>
                        <div class="mb-2"><span class="badge bg-info text-dark me-1">${{item.algorithm}}</span></div>
                        <div class="mb-2">
                            <span class="score-badge text-dark border">F: ${{item.fscore.toFixed(2)}}</span>
                            <span class="score-badge bg-dark text-white">VF: ${{item.vfscore.toFixed(2)}}</span>
                            <span class="small text-muted ms-2">${{item.time ? item.time.toFixed(1) + 's' : ''}}</span>
                        </div>
                        <div class="img-comparison">
                            <div class="img-wrapper"><img src="${{item.gt_image}}" loading="lazy" onerror="this.src='https://via.placeholder.com/200x200?text=No+Image'"><div class="img-label">Ground Truth</div></div>
                            <div class="img-wrapper"><img src="${{item.render_image}}" loading="lazy" onerror="this.src='https://via.placeholder.com/200x200?text=No+Image'"><div class="img-label">Render</div></div>
                        </div>
                    </div>
                </div>`;
            grid.appendChild(col);
        }});

        renderPagination(sortedData.length);
    }}

    function renderPagination(totalItems) {{
        const totalPages = Math.ceil(totalItems / itemsPerPage);
        const pagination = document.getElementById('paginationControls');
        pagination.innerHTML = '';
        if (totalPages <= 1) return;

        const createBtn = (html, onClick, disabled = false, active = false) => {{
            const btn = document.createElement('button');
            btn.className = `btn btn-sm ${{active ? 'btn-primary' : 'btn-outline-secondary'}}`;
            btn.innerHTML = html;
            btn.disabled = disabled;
            btn.onclick = onClick;
            return btn;
        }};

        pagination.appendChild(createBtn('&laquo;', () => {{ currentPage = 1; renderComparison(); }}, currentPage === 1));
        pagination.appendChild(createBtn('&lsaquo;', () => {{ currentPage--; renderComparison(); }}, currentPage === 1));

        let start = Math.max(1, currentPage - 2);
        let end = Math.min(totalPages, start + 4);
        if (end - start < 4) start = Math.max(1, end - 4);

        for (let i = start; i <= end; i++) {{
            pagination.appendChild(createBtn(i, () => {{ currentPage = i; renderComparison(); }}, false, i === currentPage));
        }}

        pagination.appendChild(createBtn('&rsaquo;', () => {{ currentPage++; renderComparison(); }}, currentPage === totalPages));
        pagination.appendChild(createBtn('&raquo;', () => {{ currentPage = totalPages; renderComparison(); }}, currentPage === totalPages));

        const inputGroup = document.createElement('div');
        inputGroup.className = 'input-group input-group-sm w-auto ms-2';
        const input = document.createElement('input');
        input.type = 'number'; input.className = 'form-control'; input.min = 1; input.max = totalPages; input.value = currentPage; input.style.width = '60px';
        const goBtn = document.createElement('button');
        goBtn.className = 'btn btn-outline-secondary'; goBtn.innerText = 'Vai';
        goBtn.onclick = () => {{ let val = parseInt(input.value); if (val >= 1 && val <= totalPages) {{ currentPage = val; renderComparison(); }} }};
        inputGroup.appendChild(input); inputGroup.appendChild(goBtn);
        pagination.appendChild(inputGroup);
        pagination.appendChild(document.createElement('span')).innerText = ` di ${{totalPages}}`;
        pagination.lastChild.className = 'align-self-center ms-2 text-muted small';
    }}

    function renderRecap() {{
        const tbody = document.querySelector('#recapTable tbody');
        tbody.innerHTML = '';
        itemsData.forEach(item => {{
            tbody.innerHTML += `<tr>
                <td><img src="${{item.image}}" class="thumbnail-img" loading="lazy" onerror="this.src='https://via.placeholder.com/60?text=No+Img'"></td>
                <td>${{item.id}}</td><td>${{item.variant}}</td><td>${{item.name}}</td>
                <td>${{item.category_l1}}</td><td>${{item.category_l2}}</td><td>${{item.category_l3}}</td>
            </tr>`;
        }});
        $('#recapTable').DataTable({{ language: {{ url: '//cdn.datatables.net/plug-ins/1.13.6/i18n/it-IT.json' }}, pageLength: 10 }});
    }}
</script>
</body>
</html>
"""
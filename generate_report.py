import csv
import json
import os
import re

# Configuration
CSV_PATH = r'C:\Users\Shadow\testing\tables\generations.csv'
ITEMS_CSV_PATH = r'C:\Users\Shadow\testing\tables\items.csv'
OUTPUT_HTML_PATH = r'C:\Users\Shadow\testing\report.html'
RUN_ID = '2025-08-17_v1'

def load_items():
    items = {}
    if os.path.exists(ITEMS_CSV_PATH):
        with open(ITEMS_CSV_PATH, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Clean keys
                row = {k.strip(): v for k, v in row.items()}
                pid = row.get('product_id')
                if pid:
                    items[pid] = {
                        'id': pid,
                        'name': row.get('product_name', 'N/A'),
                        'category_l1': row.get('category_l1', ''),
                        'category_l2': row.get('category_l2', ''),
                        'category_l3': row.get('category_l3', ''),
                        'image': row.get('image_1_path', '')
                    }
    return items

def generate_html():
    data = []
    items_map = load_items()
    
    # Process generations.csv
    with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Clean keys
            row = {k.strip(): v for k, v in row.items()}
            
            # Filter: F-Score must exist
            if not row.get('fscore') or row['fscore'].strip() == '':
                continue
            
            # Filter: VF-Score must exist and be > 0
            if not row.get('vfscore_overall') or row['vfscore_overall'].strip() == '':
                continue
            
            try:
                fscore = float(row['fscore'])
                vfscore_overall = float(row['vfscore_overall'])
                
                if vfscore_overall == 0:
                    continue
                    
                vfscore = vfscore_overall / 100.0
                
                # Extract hash for image paths
                # Handle both single and double backslashes by replacing with /
                vf_artifacts_dir = row.get('vf_artifacts_dir', '').replace('\\', '/')
                parts = vf_artifacts_dir.split('/')
                
                # Expected format: vfscore/HASH/vfscore_HASH or similar
                # We need the hash which is usually the second part if it starts with vfscore/
                if len(parts) >= 2:
                    item_hash = parts[1]
                else:
                    item_hash = 'unknown'

                # Construct image paths
                base_img_path = f"runs/{RUN_ID}/metrics/vfscore/{item_hash}/lpips_debug"
                gt_image = f"{base_img_path}/lpips_input_a_gt.png"
                render_image = f"{base_img_path}/lpips_input_b_render.png"

                data.append({
                    'id': row.get('product_id', 'N/A'),
                    'name': row.get('product_name', 'N/A'),
                    'category_l1': row.get('category_l1', 'Uncategorized'),
                    'category_l2': row.get('category_l2', 'Uncategorized'),
                    'category_l3': row.get('category_l3', 'Uncategorized'),
                    'algorithm': row.get('algo', 'Unknown'),
                    'fscore': fscore,
                    'vfscore': vfscore,
                    'gt_image': gt_image,
                    'render_image': render_image
                })
            except ValueError:
                continue

    # Convert data to JSON for embedding
    json_data = json.dumps(data)
    json_items = json.dumps(list(items_map.values()))

    html_content = f"""
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Report Generazione 3D</title>
    <!-- Bootstrap CSS -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <!-- Google Fonts -->
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
    <!-- Plotly.js -->
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <!-- DataTables CSS -->
    <link href="https://cdn.datatables.net/1.13.6/css/dataTables.bootstrap5.min.css" rel="stylesheet">
    
    <style>
        body {{
            font-family: 'Inter', sans-serif;
            background-color: #f8f9fa;
            color: #333;
        }}
        .container-fluid {{
            padding: 2rem;
            max-width: 1600px;
        }}
        h1 {{
            font-weight: 600;
            margin-bottom: 1.5rem;
            color: #111;
        }}
        .card {{
            border: none;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.05);
            margin-bottom: 2rem;
            background: white;
        }}
        .card-header {{
            background-color: white;
            border-bottom: 1px solid #eee;
            padding: 1rem 1.5rem;
            font-weight: 600;
            border-radius: 12px 12px 0 0 !important;
        }}
        .card-body {{
            padding: 1.5rem;
        }}
        .nav-tabs .nav-link {{
            color: #555;
            border: none;
            border-bottom: 2px solid transparent;
            margin-right: 1rem;
            padding-bottom: 0.5rem;
        }}
        .nav-tabs .nav-link.active {{
            color: #000;
            border-bottom: 2px solid #000;
            font-weight: 600;
        }}
        .controls {{
            margin-bottom: 1rem;
        }}
        .img-comparison {{
            display: flex;
            gap: 10px;
            margin-top: 10px;
        }}
        .img-wrapper {{
            flex: 1;
            text-align: center;
        }}
        .img-wrapper img {{
            width: 100%;
            max-width: 200px;
            border-radius: 8px;
            border: 1px solid #eee;
        }}
        .img-label {{
            font-size: 0.8rem;
            color: #666;
            margin-top: 5px;
        }}
        .score-badge {{
            font-size: 0.9rem;
            padding: 0.3rem 0.6rem;
            border-radius: 6px;
            background-color: #e9ecef;
            font-weight: 600;
        }}
        .item-card {{
            transition: transform 0.2s;
        }}
        .item-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.1);
        }}
        .help-box {{
            background-color: #e7f1ff;
            border-left: 4px solid #0d6efd;
            padding: 1rem;
            margin-bottom: 1.5rem;
            border-radius: 4px;
            font-size: 0.95rem;
        }}
        .thumbnail-img {{
            width: 60px;
            height: 60px;
            object-fit: cover;
            border-radius: 4px;
        }}
    </style>
</head>
<body>

<div class="container-fluid">
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h1>Report Generazione 3D</h1>
        <div class="text-muted">Generato il: {RUN_ID}</div>
    </div>

    <!-- Controls -->
    <div class="card mb-4">
        <div class="card-body d-flex align-items-center gap-3">
            <label for="groupSelect" class="form-label mb-0 fw-bold">Raggruppa per:</label>
            <select id="groupSelect" class="form-select w-auto">
                <option value="category_l1">Categoria L1</option>
                <option value="category_l2">Categoria L2</option>
                <option value="category_l3">Categoria L3</option>
            </select>
        </div>
    </div>

    <!-- Tabs -->
    <ul class="nav nav-tabs mb-4" id="reportTabs" role="tablist">
        <li class="nav-item" role="presentation">
            <button class="nav-link active" id="boxplot-tab" data-bs-toggle="tab" data-bs-target="#boxplot-pane" type="button" role="tab">Box Plots</button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link" id="sparse-tab" data-bs-toggle="tab" data-bs-target="#sparse-pane" type="button" role="tab">Confronto Algoritmi</button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link" id="comparison-tab" data-bs-toggle="tab" data-bs-target="#comparison-pane" type="button" role="tab">Confronto Visivo</button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link" id="recap-tab" data-bs-toggle="tab" data-bs-target="#recap-pane" type="button" role="tab">Riepilogo</button>
        </li>
    </ul>

    <div class="tab-content" id="reportTabsContent">
        
        <!-- Box Plots Section -->
        <div class="tab-pane fade show active" id="boxplot-pane" role="tabpanel">
            
            <!-- Help Box -->
            <div class="help-box">
                <strong>Come leggere il Box Plot:</strong>
                <ul class="mb-0 mt-1 ps-3">
                    <li>La <strong>linea centrale</strong> rappresenta la mediana dei punteggi.</li>
                    <li>Il <strong>box (rettangolo)</strong> racchiude il 50% centrale dei dati (dal 25° al 75° percentile).</li>
                    <li>I <strong>baffi</strong> (linee esterne) indicano l'intervallo dei dati, esclusi i valori anomali.</li>
                    <li>Eventuali <strong>punti singoli</strong> rappresentano valori anomali (outlier).</li>
                </ul>
            </div>

            <div class="row">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">F-Score per Categoria</div>
                        <div class="card-body">
                            <div id="fscoreBoxPlot" style="height: 500px;"></div>
                        </div>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">VF-Score per Categoria</div>
                        <div class="card-body">
                            <div id="vfscoreBoxPlot" style="height: 500px;"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Sparse Plots Section (Scatter) -->
        <div class="tab-pane fade" id="sparse-pane" role="tabpanel">
            <div class="row">
                <div class="col-12">
                    <div class="card">
                        <div class="card-header">Confronto Algoritmi (F-Score vs VF-Score)</div>
                        <div class="card-body">
                            <div id="sparsePlot" style="height: 600px;"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Comparison Section -->
        <div class="tab-pane fade" id="comparison-pane" role="tabpanel">
            <div class="card">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span>Confronto GT vs Render (Ordinato per VF-Score)</span>
                    <input type="text" id="searchBox" class="form-control w-auto" placeholder="Cerca ID o Nome...">
                </div>
                <div class="card-body">
                    <div id="comparisonGrid" class="row g-4">
                        <!-- Items will be injected here -->
                    </div>
                    <div id="paginationControls" class="mt-4 d-flex justify-content-center gap-2">
                        <!-- Pagination -->
                    </div>
                </div>
            </div>
        </div>

        <!-- Recap Section -->
        <div class="tab-pane fade" id="recap-pane" role="tabpanel">
            <div class="card">
                <div class="card-header">Riepilogo Articoli</div>
                <div class="card-body">
                    <div class="table-responsive">
                        <table id="recapTable" class="table table-hover align-middle">
                            <thead>
                                <tr>
                                    <th>Immagine</th>
                                    <th>ID</th>
                                    <th>Nome</th>
                                    <th>Categoria L1</th>
                                    <th>Categoria L2</th>
                                    <th>Categoria L3</th>
                                </tr>
                            </thead>
                            <tbody>
                                <!-- Items injected via JS -->
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

    </div>
</div>

<!-- Bootstrap JS -->
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<!-- jQuery (required for DataTables) -->
<script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
<!-- DataTables JS -->
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/dataTables.bootstrap5.min.js"></script>

<script>
    // Embedded Data
    const rawData = {json_data};
    const itemsData = {json_items};
    
    // State
    let currentGroup = 'category_l1';
    let currentPage = 1;
    const itemsPerPage = 20;
    let filteredData = [...rawData];

    // Initialize
    document.addEventListener('DOMContentLoaded', () => {{
        initPlots();
        renderComparison();
        renderRecap();
        
        // Event Listeners
        document.getElementById('groupSelect').addEventListener('change', (e) => {{
            currentGroup = e.target.value;
            updatePlots();
        }});

        document.getElementById('searchBox').addEventListener('input', (e) => {{
            const term = e.target.value.toLowerCase();
            filteredData = rawData.filter(item => 
                item.name.toLowerCase().includes(term) || 
                item.id.toString().includes(term)
            );
            currentPage = 1;
            renderComparison();
        }});
    }});

    function initPlots() {{
        updatePlots();
    }}

    function updatePlots() {{
        // Prepare Data for Plotly
        // We want to group by Algorithm. X-axis will be the Categories.
        const algorithms = [...new Set(rawData.map(d => d.algorithm))].sort();
        
        // --- Box Plots ---
        const fscoreTraces = [];
        const vfscoreTraces = [];

        algorithms.forEach(algo => {{
            const algoData = rawData.filter(d => d.algorithm === algo);
            
            // F-Score Trace
            fscoreTraces.push({{
                y: algoData.map(d => d.fscore),
                x: algoData.map(d => d[currentGroup]), // X-axis is the selected category
                type: 'box',
                name: algo,
                boxpoints: false // Hide points to keep it clean
            }});

            // VF-Score Trace
            vfscoreTraces.push({{
                y: algoData.map(d => d.vfscore),
                x: algoData.map(d => d[currentGroup]),
                type: 'box',
                name: algo,
                boxpoints: false
            }});
        }});

        const boxLayoutConfig = {{
            boxmode: 'group', // Group boxes together
            yaxis: {{range: [0, 1.05], title: 'Score'}},
            margin: {{t: 50, b: 100}},
            hovermode: 'closest',
            showlegend: true,
            legend: {{orientation: 'h', y: -0.2}}
        }};

        Plotly.newPlot('fscoreBoxPlot', fscoreTraces, {{...boxLayoutConfig, title: 'F-Score Distribution'}});
        Plotly.newPlot('vfscoreBoxPlot', vfscoreTraces, {{...boxLayoutConfig, title: 'VF-Score Distribution'}});
        
        // --- Sparse Plot (Scatter: F-Score vs VF-Score) ---
        const sparseTraces = [];

        algorithms.forEach(algo => {{
            const algoData = rawData.filter(d => d.algorithm === algo);
            
            sparseTraces.push({{
                x: algoData.map(d => d.vfscore),
                y: algoData.map(d => d.fscore),
                mode: 'markers',
                type: 'scatter',
                name: algo,
                text: algoData.map(d => d.name), // Show name on hover
                marker: {{size: 8, opacity: 0.7}}
            }});
        }});

        const sparseLayoutConfig = {{
            title: 'Confronto Algoritmi: F-Score vs VF-Score',
            xaxis: {{title: 'VF-Score', range: [0, 1.05]}},
            yaxis: {{title: 'F-Score', range: [0, 1.05]}},
            margin: {{t: 60, b: 60, l: 60, r: 60}},
            hovermode: 'closest',
            showlegend: true,
            legend: {{orientation: 'h', y: -0.2}}
        }};

        Plotly.newPlot('sparsePlot', sparseTraces, sparseLayoutConfig);
    }}

    function renderComparison() {{
        const grid = document.getElementById('comparisonGrid');
        grid.innerHTML = '';
        
        // Sort by VFScore descending
        const sortedData = [...filteredData].sort((a, b) => b.vfscore - a.vfscore);
        
        // Pagination
        const start = (currentPage - 1) * itemsPerPage;
        const end = start + itemsPerPage;
        const pageItems = sortedData.slice(start, end);

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
                        <div class="mb-2">
                            <span class="badge bg-info text-dark me-1">${{item.algorithm}}</span>
                        </div>
                        <div class="mb-2">
                            <span class="score-badge text-dark border">F: ${{item.fscore.toFixed(2)}}</span>
                            <span class="score-badge bg-dark text-white">VF: ${{item.vfscore.toFixed(2)}}</span>
                        </div>
                        <div class="img-comparison">
                            <div class="img-wrapper">
                                <img src="${{item.gt_image}}" alt="GT" loading="lazy" onerror="this.src='https://via.placeholder.com/200x200?text=No+Image'">
                                <div class="img-label">Ground Truth</div>
                            </div>
                            <div class="img-wrapper">
                                <img src="${{item.render_image}}" alt="Render" loading="lazy" onerror="this.src='https://via.placeholder.com/200x200?text=No+Image'">
                                <div class="img-label">Render</div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
            grid.appendChild(col);
        }});

        // Render Pagination Controls
        const totalPages = Math.ceil(sortedData.length / itemsPerPage);
        const pagination = document.getElementById('paginationControls');
        pagination.innerHTML = '';
        
        if (totalPages > 1) {{
            const prevBtn = document.createElement('button');
            prevBtn.className = 'btn btn-outline-secondary btn-sm';
            prevBtn.innerText = 'Precedente';
            prevBtn.disabled = currentPage === 1;
            prevBtn.onclick = () => {{ currentPage--; renderComparison(); }};
            pagination.appendChild(prevBtn);

            const info = document.createElement('span');
            info.className = 'align-self-center';
            info.innerText = `Pagina ${{currentPage}} di ${{totalPages}}`;
            pagination.appendChild(info);

            const nextBtn = document.createElement('button');
            nextBtn.className = 'btn btn-outline-secondary btn-sm';
            nextBtn.innerText = 'Successiva';
            nextBtn.disabled = currentPage === totalPages;
            nextBtn.onclick = () => {{ currentPage++; renderComparison(); }};
            pagination.appendChild(nextBtn);
        }}
    }}

    function renderRecap() {{
        const tbody = document.querySelector('#recapTable tbody');
        tbody.innerHTML = '';
        
        itemsData.forEach(item => {{
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><img src="${{item.image}}" class="thumbnail-img" loading="lazy" onerror="this.src='https://via.placeholder.com/60?text=No+Img'"></td>
                <td>${{item.id}}</td>
                <td>${{item.name}}</td>
                <td>${{item.category_l1}}</td>
                <td>${{item.category_l2}}</td>
                <td>${{item.category_l3}}</td>
            `;
            tbody.appendChild(tr);
        }});

        // Initialize DataTables
        $('#recapTable').DataTable({{
            language: {{
                url: '//cdn.datatables.net/plug-ins/1.13.6/i18n/it-IT.json'
            }},
            pageLength: 10
        }});
    }}
</script>
</body>
</html>
"""

    with open(OUTPUT_HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"Report generated at {OUTPUT_HTML_PATH}")

if __name__ == "__main__":
    generate_html()

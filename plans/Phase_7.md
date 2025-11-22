## Phase 7 • Interactive HTML report with VFScore+F-Score and 3-level categories

### Goal

Generate a self-contained **interactive `report.html`** for each run that reproduces the four Archiproducts visualizations conceptually, using **VFScore∈[0,1]** and **F-Score∈[0,1]** with default thresholds **0.65/0.65**, and offering **analysis at category levels L1, L2, and L3**. The page must work offline and allow export of images and filtered CSV.

### Inputs

Read only the SSOT CSV from prior phases (same file already used in Phase-6): `tables/generations.csv`. Required columns (robust aliasing allowed):

* identifiers: `run_id`, `job_id`, optional `product_id`, `variant_id`
* algo: `algo`
* categories: `category_l1`, `category_l2`, `category_l3`
* images used: `used_n_images` (fallbacks: `n_images`, `images_used`)
* metrics: `fscore`, `fscore_status`, `vfscore`, `vf_status`
  Rows are **plot-eligible** only if the metric’s `*_status=="ok"`.

### Business rules

* **Acceptance gate** per item: `(vfscore ≥ vf_min) AND (fscore ≥ f_min)`. Defaults: `vf_min=0.65`, `f_min=0.65`. Both thresholds are user-adjustable.
* **N-images domain** is dynamic: use all distinct values present in the run. Support 1..6 without code changes.
* Do not mutate SSOT. All outputs live under `reports/run_<run_id>/`.

### Deliverables

1. **CLI**: `archi3d report run --run-id <id> --format html [--open] [--vf-min 0.65] [--f-min 0.65]`
   Outputs:

```
reports/run_<id>/
  report.html                  # single-page app, offline
  report_data.json             # or embedded JSON
  acceptance_counts.csv        # summarized under default thresholds
  assets/                      # vendored JS/CSS/fonts
```

2. **Controls panel (runtime)**

* Multi-select: algorithms; categories at **currently selected level**.
* Selector for category level: **L1 / L2 / L3**. When level=**L2** or **L3**, show **badge legends** for upper levels. When level=**L3**, show badges for both L1 and L2. Order categories by `(L1, L2, name)` to keep hierarchy intelligible. 
* N-images selector: buttons or range covering all values present.
* Numeric inputs or sliders for `vf_min`, `f_min` with live updates.
* Toggle for showing the **acceptance region** on scatter plots.
* “Download filtered CSV” for the current subset.

3. **Four visualization sections**
   All sections must react instantly to filters, level changes, and thresholds; legends must reflect only visible algos; empty states show a neutral placeholder.

A) **Scatter by category × N-images**
For each selected category (at the chosen level) and each selected N, plot `x=vfscore`, `y=fscore`, color by `algo`. Draw threshold guide lines at `vf_min` and `f_min` and an optional shaded top-right acceptance rectangle. Show `accepted/total` for each panel. Export each panel as PNG.
(Concept: same acceptance guides seen in the mockups; interactivity per this report.)

B) **Distribution of VFScore by category × algo, split by N-images**
One figure per N; category on x; grouped by algo. A horizontal reference at `vf_min`. **Box-plot definition = Tukey**: Q1–Q3 box, median line, whiskers to last data within Q±1.5·IQR, caps, outliers as points. Provide **hover tooltips** reporting `n, min, Q1, median, Q3, max, IQR, lower/upper fence, outliers count`. Include **“How to read a box plot (Tukey)”** explainer panel. 

C) **Distribution of F-Score by category × algo, split by N-images**
Same as (B) but for `fscore` and reference at `f_min`. Keep the same Tukey stats, tooltips, legends, category ordering, and **upper-level badges** when viewing L2/L3. 

D) **“Optimal number of images” per (algo, category)**
For each `(algo, category_at_selected_level)` aggregate by N: plot points at `(mean_vf, mean_f)` annotated with `N`. Show uncertainty as standard error or 95% CI (pick one and state it in the page). Keep threshold guides and acceptance rectangle visible to help select N. Panels appear only when at least two distinct N values exist.

4. **Explanatory blocks and UX**

* Two compact callouts: **Box-plot reading** and **Interpretation** (high box=better, narrow=low variance, line at 0.65 indicates acceptance). 
* Badges for upper category levels (L1 and optionally L2) under each category label, including a small `n=` count per category (average per-algo item count as in the app). 
* Buttons to **export current chart to PNG**. Export must render with title and legends. 
* Light/dark theme that prints cleanly to PDF.

5. **Summaries**

* `acceptance_counts.csv`: for each `(selected_category_level_value, N, algo)` write `total` and `accepted` under **default** thresholds.
* Small summary cards on the page: number of algos, categories at the chosen level, test count for the selected N, and total dataset size. 

### Architecture constraints

* Keep Phase-7 changes **additive**. Do not change earlier phase schemas or semantics.
* The HTML must render offline (`file://`) and from a static server. No remote CDNs.
* Library choice is open (Plotly, ECharts, Vega, D3, etc.). Prioritize clarity and performance over bundle size.
* Centralize filtering and threshold logic so all views stay consistent.

### Testing and acceptance

* With a run containing multiple algos, categories across **all three levels**, and N in {1..5}, `archi3d report run --run-id <id> --format html` must:

  * Open offline and show the four sections.
  * Let the user switch **L1/L2/L3** and see: hierarchy-aware ordering, upper-level **badges**, and per-category sample `n` counts exactly as defined above. 
  * Update plots, legends, acceptance overlays, and the `accepted/total` counters when filters or thresholds change.
  * Export each chart to PNG with titles and legends.
  * Export the filtered subset to CSV.
  * Print to PDF legibly.

### Non-goals

* No prescriptive Python plotting. The agent is free to design visuals, provided the semantics above hold.
* No changes to how metrics are computed or scaled.
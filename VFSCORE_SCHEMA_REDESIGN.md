# VFScore Schema Redesign for Objective2 Pipeline

## Executive Summary

The current VFScore columns in `generations.csv` were designed for the LLM-based (Objective1) pipeline. Now that we're using the Objective2 pipeline (LPIPS + IoU/AR pose estimation), we need to capture:

1. **Core LPIPS metrics** - The perceptual distance and model used
2. **Pose estimation quality** - IoU, mask error, pose confidence
3. **Score combination parameters** - gamma, pose_compensation_c
4. **Final pose parameters** - azimuth, elevation, radius, FOV, yaw
5. **Pipeline statistics** - num candidates at each step, search mode
6. **Artifact paths** - GT image, HQ render for visualization

## Current Schema (LEGACY - to be replaced)

```
vf_status
vf_error
vfscore_overall              # Final combined score (0-100)
vf_finish                    # NULL (LLM subscore, not used in objective2)
vf_texture_identity          # NULL (LLM subscore, not used in objective2)
vf_texture_scale_placement   # NULL (LLM subscore, not used in objective2)
vf_repeats_n                 # Always 1 in objective2
vf_iqr                       # Always 0 in objective2
vf_std                       # Always 0 in objective2
vf_llm_model                 # NULL (not used in objective2)
vf_rubric_json               # NULL weights (not used in objective2)
vf_render_runtime_s
vf_scoring_runtime_s
vf_config_hash
vf_rationales_dir            # NULL (LLM rationales, not used)
```

## Proposed New Schema

### Status & Error (keep existing)
- `vf_status` (ok/error/skipped)
- `vf_error` (error message if failed)

### Core Score & Metrics (NEW/MODIFIED)
- `vfscore_overall` (KEEP - final combined score 0-100)
- `vf_lpips_distance` (NEW - raw LPIPS perceptual distance, 0-1, lower is better)
- `vf_lpips_model` (NEW - "alex", "vgg", or "squeeze")
- `vf_iou` (NEW - IoU between GT and rendered mask, 0-1, higher is better)
- `vf_mask_error` (NEW - 1 - IoU, mask alignment error)
- `vf_pose_confidence` (NEW - same as IoU, used in score combination)

### Score Combination Parameters (NEW)
- `vf_gamma` (NEW - pose confidence exponent, typically 1.0)
- `vf_pose_compensation_c` (NEW - max slack allowed for poor poses, typically 0.5)

### Final Pose Parameters (NEW)
- `vf_azimuth_deg` (NEW - camera azimuth in degrees)
- `vf_elevation_deg` (NEW - camera elevation in degrees)
- `vf_radius` (NEW - camera distance from object)
- `vf_fov_deg` (NEW - field of view in degrees)
- `vf_obj_yaw_deg` (NEW - object yaw rotation in degrees)

### Pipeline Statistics (NEW)
- `vf_pipeline_mode` (NEW - "tri_criterion", "ar_based", etc.)
- `vf_num_step2_candidates` (NEW - number of coarse pose candidates)
- `vf_num_step4_candidates` (NEW - number of fine pose candidates)
- `vf_num_selected_candidates` (NEW - number of candidates passed to LPIPS scoring)
- `vf_best_lpips_idx` (NEW - index of best candidate in selected set)

### Performance & Provenance (KEEP/MODIFY)
- `vf_render_runtime_s` (KEEP)
- `vf_scoring_runtime_s` (KEEP - but now includes pose search + LPIPS)
- `vf_config_hash` (KEEP)
- `vf_tool_version` (NEW - VFScore version string)

### Artifact Paths (NEW)
- `vf_gt_image_path` (NEW - relative path to GT image used, from artifacts_dir)
- `vf_render_image_path` (NEW - relative path to HQ render used, from artifacts_dir)
- `vf_artifacts_dir` (NEW - workspace-relative path to vfscore_artifacts directory)

### DEPRECATED (to be removed)
- `vf_finish` - LLM subscore, not applicable
- `vf_texture_identity` - LLM subscore, not applicable
- `vf_texture_scale_placement` - LLM subscore, not applicable
- `vf_repeats_n` - Always 1 in objective2
- `vf_iqr` - Not applicable without repeats
- `vf_std` - Not applicable without repeats
- `vf_llm_model` - No LLM in objective2
- `vf_rubric_json` - No rubric in objective2
- `vf_rationales_dir` - No LLM rationales

## Final Column List (36 columns total)

### Status (2)
1. vf_status
2. vf_error

### Core Metrics (6)
3. vfscore_overall
4. vf_lpips_distance
5. vf_lpips_model
6. vf_iou
7. vf_mask_error
8. vf_pose_confidence

### Score Parameters (2)
9. vf_gamma
10. vf_pose_compensation_c

### Pose Parameters (5)
11. vf_azimuth_deg
12. vf_elevation_deg
13. vf_radius
14. vf_fov_deg
15. vf_obj_yaw_deg

### Pipeline Statistics (5)
16. vf_pipeline_mode
17. vf_num_step2_candidates
18. vf_num_step4_candidates
19. vf_num_selected_candidates
20. vf_best_lpips_idx

### Performance (3)
21. vf_render_runtime_s
22. vf_scoring_runtime_s
23. vf_tool_version

### Provenance (3)
24. vf_config_hash
25. vf_gt_image_path
26. vf_render_image_path
27. vf_artifacts_dir

## Score Combination Formula

The final VFScore is computed as:

```
slack = pose_compensation_c * (1 - pose_confidence^gamma)
adjusted_lpips = lpips_distance - slack
normalized_score = max(0, min(1, 1 - adjusted_lpips))
vfscore_overall = normalized_score * 100
```

Where:
- `lpips_distance`: Raw LPIPS perceptual distance (0-1, lower is better)
- `pose_confidence`: Same as IoU (0-1, higher is better)
- `gamma`: Exponent for pose confidence (typically 1.0)
- `pose_compensation_c`: Maximum slack for poor poses (typically 0.5)

## Migration Notes

### For archi3D
1. Update `src/archi3d/metrics/vfscore.py` to add all new columns
2. Update `src/archi3d/metrics/vfscore_adapter.py` VFScoreResponse model
3. Remove deprecated columns from CSV schema
4. Update CLAUDE.md with new schema

### For VFScore
1. Update `src/vfscore/evaluator.py` response dict to include all new fields
2. Ensure `final.json` contains all necessary data
3. Update config.json to include LPIPS model and combiner params
4. Ensure artifacts.json has proper relative paths

## Data Sources

All data is available in the current pipeline output:

### From final.json (pipeline_objective2.py:1133-1145)
- score → vfscore_overall (multiply by 100)
- lpips → vf_lpips_distance
- iou → vf_iou, vf_pose_confidence
- mask_error → vf_mask_error
- params.azimuth_deg → vf_azimuth_deg
- params.elevation_deg → vf_elevation_deg
- params.radius → vf_radius
- params.fov_deg → vf_fov_deg
- params.obj_yaw_deg → vf_obj_yaw_deg
- pipeline_mode → vf_pipeline_mode
- num_step2_candidates → vf_num_step2_candidates
- num_step4_candidates → vf_num_step4_candidates
- num_selected_candidates → vf_num_selected_candidates
- best_lpips_idx → vf_best_lpips_idx
- time_seconds → vf_render_runtime_s + vf_scoring_runtime_s

### From config (loaded in evaluator.py)
- config.objective.lpips.model → vf_lpips_model
- config.objective.combiner.gamma → vf_gamma
- config.objective.combiner.pose_compensation_c → vf_pose_compensation_c

### From artifacts.json
- gt → vf_gt_image_path
- render → vf_render_image_path

### From version
- vfscore.__version__ → vf_tool_version

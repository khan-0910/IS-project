/** Shared types mirroring the backend report schema. */

export const CLASS_NAMES = [
  "planet_candidate",
  "eclipsing_binary",
  "grazing_eclipsing_binary",
  "contamination_blend",
  "instrumental_systematic",
] as const;

export const CLASS_DISPLAY: Record<string, string> = {
  planet_candidate: "PLANET CANDIDATE",
  eclipsing_binary: "ECLIPSING BINARY",
  grazing_eclipsing_binary: "GRAZING EB",
  contamination_blend: "CONTAMINATION",
  instrumental_systematic: "SYSTEMATIC",
};

export const CLASS_CODE: Record<string, string> = {
  planet_candidate: "PC",
  eclipsing_binary: "EB",
  grazing_eclipsing_binary: "GEB",
  contamination_blend: "BLEND",
  instrumental_systematic: "SYS",
};

export type Pt = [number, number];

export interface SeriesSection {
  units?: Record<string, string>;
  raw?: Pt[];
  detrended?: Pt[];
  trend?: Pt[];
  phase_folded?: Pt[];
  phase_model?: Pt[];
  phase_window?: { half_width: number; transit_half: number };
  periodogram?: Pt[];
  periodogram_marker?: { period: number | null; sde_threshold: number };
  sector_bounds?: number[];
  sectors?: number[];
  fit_model?: Pt[];
  fit_data?: Pt[];
  fit_residuals?: Pt[];
  fit_residual_zero?: Pt[];
  status?: string;
  error?: string;
}

export interface DemoCurve {
  target_id: string;
  data_source: string;
  n_sectors?: number;
  series: SeriesSection;
}

export interface LatestResult {
  available?: boolean;
  job_id?: string;
  target_id?: string;
  generated_at?: string;
  period_days?: number;
  sde?: number;
  snr?: number;
  depth_ppm?: number;
  predicted_class?: string;
  verdict?: string;
  n_sectors?: number;
}

export interface McClassification {
  class_names: string[];
  mean_probs: number[];
  std_probs: number[];
  predicted_class: string;
  predicted_class_id: number;
  top_margin: number;
  predictive_entropy: number;
  mutual_information: number;
  n_passes: number;
  flags: { ambiguous: boolean; unstable: boolean; verdict: string; out_of_distribution?: boolean };
  ood?: {
    checked: boolean;
    flag: boolean;
    features: { name: string; value: number; z: number; train_min: number; train_max: number }[];
    rule?: string;
  };
  excluded_inputs?: string[];
  ensemble?: {
    applied?: boolean;
    weight_transformer?: number;
    val_macro_f1_blend?: number;
    created_at?: string;
    note?: string;
  };
  model_checkpoint?: {
    trained_at?: string;
    test_macro_f1?: number | null;
    dataset?: Record<string, unknown>;
  };
  seconds?: number;
}

export interface HarmonicCandidate {
  multiple: number;
  period_days: number;
  power_ratio: number;
  realization: number;
  concentration?: number;
}

/** detection.harmonic_check — EXOVET's alias defence (event realization + scan). */
export interface HarmonicCheck {
  enabled: boolean;
  checked: HarmonicCandidate[];
  switched_to_multiple: number | null;
  realization_at_tls_period: number | null;
  power_at_tls_period?: number | null;
  reason?: string;
}

export interface Detection {
  module?: string;
  seconds?: number;
  method?: string;
  version?: string;
  period_days?: number;
  t0_days?: number;
  duration_days?: number;
  duration_hours?: number;
  depth?: number;
  depth_ppm?: number;
  snr?: number;
  sde?: number;
  fap?: number;
  odd_even_mismatch_sigma?: number;
  n_transits?: number;
  significant?: boolean;
  signal_strength?: string;
  sde_threshold?: number;
  depth_gate?: number | null;
  warnings?: string[];
  harmonic_check?: HarmonicCheck;
  duration_folded_days?: number;
  search_window_days?: number[];
  [k: string]: unknown;
}

export interface TransitFit {
  status: string;
  model?: string;
  acceptance_fraction?: number;
  n_points?: number;
  params?: Record<string, { median: number; err16?: number; err84?: number }>;
  fit_quality?: {
    chi2: number;
    reduced_chi2: number;
    residual_std_ppm: number;
    depth_fit_ppm: number;
  };
  curve?: {
    time: number[];
    model: number[];
    data_time: number[];
    data_flux: number[];
  };
  error?: string;
  reason?: string;
}

export interface Attention {
  view_shares: Record<string, number>;
  views: Record<string, { token_influence: number[]; axis: number[]; view_share: number }>;
  top_tokens: { view: string; index: number; influence: number }[];
  method: string;
  note: string;
}

export interface Ingest {
  data_source: string;
  n_sectors: number;
  n_points_raw: number;
  sectors: (number | null)[];
  provenance: Record<string, unknown>;
}

export interface Report {
  status: string;
  target_id: string;
  generated_at: string;
  schema_version?: string;
  cached?: boolean;
  cache_key?: string;
  cache_saved_at?: string;
  versions?: Versions;
  run_mode?: string;
  progress?: { stages: StageEvent[]; stage_seconds: Record<string, number>; total_seconds?: number };
  ingest?: Ingest;
  preprocessing?: Record<string, unknown>;
  detection?: Detection;
  features?: { scalars: Record<string, number>; feature_warnings: string[] };
  classification?: McClassification;
  attention?: Attention;
  transit_fit?: TransitFit;
  series?: SeriesSection;
  stellar?: Record<string, unknown>;
  feature_registry?: {
    features?: Record<string, FeatureDef>;
    scalar_order?: string[];
  };
  total_seconds?: number;
  error?: string;
}

export interface FeatureDef {
  definition?: string;
  unit?: string;
  source?: string;
  reason?: string;
}

export interface Versions {
  pipeline?: string;
  pipeline_config_sha?: string;
  pipeline_config_date?: string;
  model?: string;
  data?: { source?: string; ground_truth?: string };
  analysis_mode?: string;
}

export interface StageEvent {
  stage: string;
  status: "running" | "done" | "failed";
  seconds?: number | null;
  message?: string;
}

/** GET /api/jobs/{id}?brief=1 — light polling payload. */
export interface JobBrief {
  job_id?: string | null;
  status: "queued" | "running" | "done" | "error";
  mode?: string;
  stage?: string;
  message?: string;
  elapsed_seconds?: number;
  stages?: StageEvent[];
  stage_seconds?: Record<string, number>;
  error?: string;
  error_info?: FriendlyError;
  summary?: Headline;
  cached?: boolean;
  versions?: Versions;
}

export interface FriendlyError {
  code: string;
  message: string;
  detail?: string | null;
  action: string;
}

export interface Headline {
  target_id?: string;
  predicted_class?: string | null;
  top_prob?: number | null;
  verdict?: string | null;
  ambiguous?: boolean | null;
  period_days?: number | null;
  sde?: number | null;
  snr?: number | null;
  depth_ppm?: number | null;
  n_sectors?: number | null;
  total_seconds?: number | null;
  run_mode?: string;
}

export interface ResultRow {
  job_id: string;
  target_id?: string;
  generated_at?: string;
  mode?: string;
  status?: string;
  predicted_class?: string | null;
  confidence?: number | null;
  verdict?: string | null;
  cached?: boolean;
  period_days?: number | null;
  sde?: number | null;
  n_sectors?: number | null;
  total_seconds?: number | null;
}

export interface Job {
  status: "queued" | "running" | "done" | "error";
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  report?: Report;
  error?: string;
}

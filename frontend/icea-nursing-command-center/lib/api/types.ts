import { z } from "zod";

/** JSON without `any` */
export type Json =
  | null
  | boolean
  | number
  | string
  | Json[]
  | { [key: string]: Json };

export const DashboardSummarySchema = z.object({
  episodes: z.number().int(),
  raw_fhir: z.number().int(),
  roster_shifts: z.number().int(),
  normalized: z.object({
    observations: z.number().int(),
    conditions: z.number().int(),
    procedures: z.number().int(),
  }),
  dataset_rows: z.number().int(),
  windows: z.number().int(),
  window_rows: z.number().int(),
  audit_events: z.number().int(),
  governance_decisions: z.number().int(),
  writebacks: z.object({ count: z.number().int() }),
  latest_model: z.object({
    id: z.string().nullable(),
    name: z.string().nullable(),
    version: z.string().nullable(),
    created_at: z.union([z.string(), z.number(), z.null()]).nullable(),
  }),
  latest_training: z.object({
    id: z.string().nullable(),
    created_at: z.union([z.string(), z.number(), z.null()]).nullable(),
    dataset_rows: z.number().nullable(),
  }),
  latest_compute: z.object({
    id: z.string().nullable(),
    created_at: z.union([z.string(), z.number(), z.null()]).nullable(),
    summary: z.record(z.string(), z.unknown()).nullable(),
  }),
  latest_causal: z.object({
    id: z.string().nullable(),
    created_at: z.union([z.string(), z.number(), z.null()]).nullable(),
    summary: z.record(z.string(), z.unknown()).nullable(),
  }),
  latest_governance: z.object({
    id: z.string().nullable(),
    created_at: z.union([z.string(), z.number(), z.null()]).nullable(),
    decision_type: z.string().nullable(),
    actor: z.string().nullable(),
  }),
  latest_data_quality: z.object({
    id: z.string().nullable(),
    created_at: z.union([z.string(), z.number(), z.null()]).nullable(),
    report: z.record(z.string(), z.unknown()).nullable(),
  }),
});

export type DashboardSummary = z.infer<typeof DashboardSummarySchema>;

export const ModelArtifactSchema = z.object({
  id: z.string(),
  name: z.string(),
  version: z.string(),
  target: z.string(),
  features: z.array(z.string()),
  model_type: z.string(),
  model_path: z.string(),
  metrics: z.record(z.string(), z.unknown()),
  created_at: z.union([z.string(), z.number(), z.null()]).nullable().optional(),
});

export type ModelArtifact = z.infer<typeof ModelArtifactSchema>;

export const AuditEventsSchema = z.object({
  count: z.number().int(),
  events: z.array(
    z.object({
      id: z.string(),
      created_at: z.union([z.string(), z.number()]),
      event_type: z.string(),
      actor: z.string().nullable().optional(),
      context: z.string().nullable().optional(),
      payload_sha256: z.string().nullable().optional(),
      prev_hash: z.string().nullable().optional(),
      chain_hash: z.string().nullable().optional(),
      hmac_sig: z.string().nullable().optional(),
    })
  ),
});
export type AuditEvents = z.infer<typeof AuditEventsSchema>;

export const RosterSummarySchema = z.object({
  roster_shifts: z.number().int(),
  units_with_roster: z.number().int(),
});
export type RosterSummary = z.infer<typeof RosterSummarySchema>;

export const WritebackItemSchema = z.object({
  id: z.string(),
  created_at: z.union([z.string(), z.number()]),
  episode_id: z.number().nullable(),
  model_id: z.string(),
  attempted: z.boolean(),
  ok: z.boolean(),
});
export type WritebackItem = z.infer<typeof WritebackItemSchema>;

export const ConformalPredictResponseSchema = z.object({
  episode_id: z.number().int(),
  model_id: z.string(),
  target: z.string(),
  pred: z.number(),
  interval: z.record(z.string(), z.unknown()),
});
export type ConformalPredictResponse = z.infer<typeof ConformalPredictResponseSchema>;

export const ICEAComputeResponseSchema = z.object({
  model: ModelArtifactSchema,
  summary: z.record(z.string(), z.unknown()),
  rows: z.number().int(),
  results: z.object({
    predictions: z.array(z.number()),
    base_value: z.number(),
    icea: z.array(z.number()),
    contributions: z.record(z.string(), z.array(z.number())),
  }),
});
export type ICEAComputeResponse = z.infer<typeof ICEAComputeResponseSchema>;

export const CausalDiscoverResponseSchema = z.object({
  discovery_run_id: z.string(),
  result: z.object({
    dag_edges: z.array(z.tuple([z.string(), z.string()])),
    undirected_edges: z.array(z.tuple([z.string(), z.string()])),
    p_values: z.record(z.string(), z.number()).optional(),
    notes: z.array(z.string()).optional(),
    n_rows: z.number().int().optional(),
  }),
});
export type CausalDiscoverResponse = z.infer<typeof CausalDiscoverResponseSchema>;

export const CausalRunResponseSchema = z.object({
  run_id: z.string(),
});
export type CausalRunResponse = z.infer<typeof CausalRunResponseSchema>;

export const CausalReportResponseSchema = z.record(z.string(), z.unknown());
export type CausalReportResponse = z.infer<typeof CausalReportResponseSchema>;

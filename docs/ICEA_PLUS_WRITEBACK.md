# ICEA+ Writeback Summary Contract for HANDOVER

## Contract priority

The stable contract exposed here is JSON summary output for HANDOVER.
It reuses the governed ICEA+ kernel and the repo's existing persistence, but it does
not introduce new FHIR writeback semantics for enriched follow-up.

Existing FHIR RiskAssessment writeback remains available in the pipeline for the
legacy path. The new longitudinal HANDOVER contract should consume the summary JSON
endpoints below.

## Endpoints

- `GET /api/v1/icea-plus/writeback/patient/`
- `GET /api/v1/icea-plus/writeback/summary/`

## Patient-level payload

The patient/episode contract includes:

- `score_states.initial`
- `score_states.followup`
- `score_states.current`
- `initial_score`
- `enriched_score`
- `current_score`
- `comparison`
- `warnings`
- `support`
- `evidence`
- `provenance`
- `timestamps`
- `non_individual_use`
- `shadow_mode`
- `exploratory_only`

This contract is designed for HANDOVER episode cards and patient/episode drill-downs.

## Aggregate payload

The aggregate summary contract includes:

- `requested_group_by`
- `effective_group_by`
- `formula_version`
- `formula_protocol_hash`
- `status_counts`
- `summary`
- `warnings`
- `results`
- `non_individual_use`
- `shadow_mode`
- `exploratory_only`

Supported `group_by` values:

- `unit`
- `team`
- `shift`

Current degradation rules:

- `team` falls back to `unit`
- `shift` falls back to `unit` in the current repo state because the longitudinal
  enriched contract is episode-level

## HANDOVER guidance

HANDOVER should consume this contract as prudent analytic support:

- show provisional vs complete vs enriched state clearly
- surface warnings, support and stale state
- retain provenance in the UI
- avoid individual nurse ranking or punitive interpretation

## Methodological limits

- enriched rescoring remains observational and exploratory
- no individual labor ranking is exposed by default
- no causal claims are added by this writeback layer
- no new FHIR writeback path is claimed beyond the repo's existing RiskAssessment support

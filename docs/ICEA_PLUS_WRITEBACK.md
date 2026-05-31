# ICEA+ Writeback Summary Contract for HANDOVER

## Contract priority

The stable contract exposed here is JSON summary output for HANDOVER.
It reuses the governed ICEA+ kernel and the repo's existing persistence, but it does
not introduce new FHIR writeback semantics for enriched follow-up.

Individual FHIR RiskAssessment writeback is blocked in shadow mode and does not emit an operational score. The exportable contract is the governed aggregate JSON surface below.

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

This contract is limited to follow-up state and lineage for a requested episode. `initial_score`, `enriched_score`, and `current_score` suppress `score` and `raw_score`; they are not operational patient metrics.

## Aggregate payload

The aggregate summary contract includes:

- `requested_group_by`
- `effective_group_by`
- `formula_version`
- `formula_protocol_hash`
- `status_counts`
- `summary`
- `governance`
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
- cells with fewer than 10 episodes are returned as `suppressed_low_support`
- staff-sensitive cells require at least 5 staff members when that dimension is requested
- suppression never uses zero-fill

## HANDOVER guidance

HANDOVER should consume this contract as prudent analytic support:

- show provisional vs complete vs enriched state clearly
- surface warnings, support and stale state
- retain provenance in the UI
- avoid individual nurse ranking or punitive interpretation
- do not display a patient/episode numeric score from this contract

## Methodological limits

- enriched rescoring remains observational and exploratory
- no individual labor ranking is exposed by default
- no causal claims are added by this writeback layer
- no individual RiskAssessment score is written in shadow mode

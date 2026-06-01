# ICEA+ API

## Endpoints

### POST `/api/v1/icea-plus/score/`

Scores episode- or window-grain rows for governed research/service flows. Dashboard and export surfaces must treat row-level output as shadow-only and must not expose it as an operational patient, episode, nurse, team, or shift score.

Scoring is fail-closed for temporal defensibility. DB rows and controlled external
payloads must include `temporal_spec` with `index_time`, feature window, outcome
window, censoring, and `temporal_spec_version`. Without that contract the response
contains statuses such as `insufficient_temporal_spec`, `temporal_leakage_blocked`,
`legacy_outcome_not_defensible`, or `insufficient_outcome_evidence`, and numeric
`score` / `raw_score` are `null`.

#### Request patterns

1. DB-backed scoring

```json
{
  "model_id": "<uuid>",
  "grain": "episode",
  "from_db": true,
  "unit_id": 1,
  "causal_spec": {
    "treatment": "nurse_hppd",
    "outcome": "delta_ri",
    "confounders": ["ri_initial", "proc_count"],
    "effect_modifiers": ["ri_initial"],
    "n_estimators": 200
  }
}
```

2. Controlled tabular payload

```json
{
  "model_id": "<uuid>",
  "grain": "window",
  "from_db": false,
  "rows": [
    {
      "row_id": "window:1",
      "episode_id": 10,
      "unit_id": 4,
      "ri_initial": 58,
      "proc_count": 3,
      "nurse_proc_count": 2,
      "nurse_proc_count_det": 2,
      "nurse_hppd": 4.2,
      "nurse_skillmix": 0.7,
      "missing_loinc_85556_9_t0": 0,
      "missing_loinc_85556_9_t1": 0,
      "missing_delta_ri": 0,
      "delta_ri": 8.1,
      "temporal_spec": {
        "temporal_spec_version": "icea_temporal_v1",
        "index_time": "2026-03-08T08:00:00Z",
        "feature_window_start": "2026-03-08T08:00:00Z",
        "feature_window_end": "2026-03-08T14:00:00Z",
        "outcome_window_start": "2026-03-08T14:00:00Z",
        "outcome_window_end": "2026-03-09T14:00:00Z",
        "censoring_reason": "not_censored"
      },
      "nurse_shares": {"Practitioner/nurse-1": 0.6, "Practitioner/nurse-2": 0.4}
    }
  ]
}
```

### Temporal and Causal Guardrails

- `/pipeline/build-dataset/` now materializes episode rows as legacy/provisional when the target is discharge or final-stay `delta_ri`.
- `/pipeline/build-windows/` separates feature windows from outcome windows; same-window exposure/outcome without lag is not treated as defensible.
- `/pipeline/train/` rejects rows that are not temporally defensible.
- `/causal/run/` and `/causal/simulate/` return `causal_available=false` when treatment, confounders, and outcome ordering cannot be proven.
- Aggregate endpoints warn `no_comparable_without_case_mix` when unit/date/shift comparisons lack explicit case-mix specification.

#### Response sketch

```json
{
  "formula_version": "icea_plus_v1",
  "formula_protocol_hash": "<sha256>",
  "model": {
    "id": "<uuid>",
    "name": "icea-xgb",
    "version": "v0.7.4",
    "target": "delta_ri"
  },
  "summary": {
    "rows_requested": 12,
    "rows_scored": 12,
    "baseline_mode": "counterfactual_nursing_reference",
    "causal_available": true,
    "default_pilot_weights": {
      "intercept": 0.0,
      "benefit": 1.0,
      "attribution": 1.0,
      "causal": 1.0,
      "quality": 1.0,
      "uncertainty": 1.0
    },
    "status_counts": {
      "complete": 10,
      "provisional": 2,
      "insufficient_evidence": 0
    },
    "component_means": {
      "benefit": 0.31,
      "attribution": 0.18,
      "causal": 0.11,
      "quality": 0.22,
      "uncertainty": -0.09
    }
  },
  "results": [
    {
      "row_id": "episode:101",
      "status": "complete",
      "provisional": false,
      "score": 67.4,
      "raw_score": 0.73,
      "confidence": {"value": 0.81, "label": "high"},
      "flags": {
        "causal_available": true,
        "low_support": false,
        "high_uncertainty": false,
        "missing_key_inputs": false,
        "insufficient_evidence": false
      },
      "components": {
        "benefit": {"raw": 0.9, "normalized": 0.4, "available": true},
        "attribution": {"raw": 0.12, "normalized": 0.2, "available": true},
        "causal": {"raw": 0.3, "normalized": 0.1, "available": true},
        "quality": {"raw": 0.8, "normalized": 0.3, "available": true},
        "uncertainty": {"raw": 0.2, "normalized": -0.1, "available": true}
      },
      "legacy_icea": {
        "nursing_shap_sum": 0.41,
        "prediction": 9.5,
        "baseline_expected": 8.1
      }
    }
  ]
}
```

### GET `/api/v1/icea-plus/explain/`

Returns the active formula definition, weights, limitations, and governed flags.

Optional query params:

- `formula_version=<version>`

### GET `/api/v1/icea-plus/aggregate/`

Aggregates ICEA+ scores over DB-backed cohorts.

#### Query params

- `model_id=<uuid>` required
- `grain=episode|window`
- `group_by=patient|episode|window|shift|nurse|team|unit|date`
- `unit_id=<int>` optional
- `date_from=<iso-datetime>` optional
- `date_to=<iso-datetime>` optional
- `formula_version=<version>` optional
- `causal_run_id=<uuid>` optional
- `outcome_goal=higher_is_better|lower_is_better|adverse_event` optional

Individualizable groupings (`patient`, `episode`, `window`, `nurse`) are accepted only for backward-compatible query parsing and fall back to `unit`. `team` also falls back to `unit`. `shift` is deidentified to a unit/date bucket and is suppressed unless support thresholds are met.

#### Response sketch

```json
{
  "formula_version": "icea_plus_v1",
  "requested_group_by": "nurse",
  "effective_group_by": "unit",
  "warnings": ["nurse_grouping_individualizable_falling_back_to_unit"],
  "non_individual_use": true,
  "shadow_mode": true,
  "governance": {
    "non_individual_use": true,
    "shadow_mode": true,
    "aggregation_level": "unit",
    "min_cell_count": 10,
    "suppressed_cells": 0,
    "formula_version": "icea_plus_v1",
    "model_lineage": {"model_id": "<uuid>", "model_version": "v0.7.4"},
    "generated_at": "<iso-datetime>"
  },
  "results": [
    {
      "group": "1",
      "status": "scored_aggregate",
      "score": 64.2,
      "n_observations": 24,
      "support": {
        "n_observations": 24,
        "n_episodes": 24,
        "n_staff": 0,
        "min_cell_count": 10,
        "min_staff_count": 5,
        "suppressed": false,
        "suppression_reasons": []
      },
      "coverage": 0.92,
      "provisional_fraction": 0.25,
      "complete_fraction": 0.67,
      "insufficient_fraction": 0.08,
      "confidence_band": {
        "p10": 52.1,
        "p50": 63.9,
        "p90": 74.4,
        "weighted_std": 7.3
      },
      "component_means": {
        "benefit": 0.21,
        "attribution": 0.19,
        "causal": 0.05,
        "quality": 0.14,
        "uncertainty": -0.08
      }
    }
  ]
}
```

### POST `/api/v1/icea-plus/calibrate/`

Admin-only endpoint to persist a new governed formula version or update an existing one.

```json
{
  "version": "icea_plus_v1_hospital_a",
  "activate": true,
  "notes": "Hospital A pilot calibration",
  "spec": {
    "weights": {
      "benefit": 1.2,
      "causal": 1.3,
      "uncertainty": 1.1
    }
  }
}
```

### POST `/api/v1/icea-plus/followup/ingest/`

Registers repo-backed follow-up evidence for one episode/model pair and returns the
current longitudinal record for HANDOVER consumption.

```json
{
  "episode_id": 42,
  "model_id": "<uuid>"
}
```

### POST `/api/v1/icea-plus/followup/rescore/`

Triggers enriched rescoring only when the repo has sufficient new follow-up support.
If support is missing, the endpoint returns an explicit non-enriched state instead of
fabricating a later score.

### GET `/api/v1/icea-plus/followup/status/`

Returns the longitudinal state for one episode/model pair.

Required query params:

- `episode_id=<int>`
- `model_id=<uuid>`

### GET `/api/v1/icea-plus/writeback/patient/`

Stable episode JSON summary for service follow-up. It is shadow-only: `initial_score`, `enriched_score`, and `current_score` retain lineage and state but suppress `score` and `raw_score`.

Required query params:

- `episode_id=<int>`
- `model_id=<uuid>`

### GET `/api/v1/icea-plus/writeback/summary/`

Stable aggregate JSON summary. Results include support counts, suppression flags, and governance metadata and are the only exportable ICEA+ writeback surface.

Required query params:

- `model_id=<uuid>`

Optional query params:

- `group_by=unit|team|shift`
- `unit_id=<int>`
- `date_from=<iso-datetime>`
- `date_to=<iso-datetime>`
- `formula_version=<version>`

## API semantics

### Backward compatibility

- Legacy `POST /api/v1/icea/compute/` remains for compatibility, but the command-center UI does not expose it as a patient/episode score.
- Existing causal endpoints remain unchanged.
- ICEA+ is additive to the current API surface.

### Provisional vs insufficient evidence

- `scored_aggregate`: aggregate cell with enough support and only scoreable rows contributing to numeric output.
- `provisional`: non-causal required components are available, causal is not.
- `insufficient_evidence`: required components are missing, so the API refuses to fabricate a final score.
- `contract_mismatch`: feature contract validation failed; no score is emitted.
- `low_feature_coverage`: required coverage threshold failed; no score is emitted.
- `suppressed_low_support`: aggregate cell failed `n_episodes >= 10` or staff support checks; no score is emitted.
- `shadow_only`: individual patient/episode prediction surfaces are non-operational and suppress score fields.

### Longitudinal follow-up states

- `immediate_provisional`: initial score is retained and causal support was unavailable
- `complete`: initial score is retained with required components available
- `enriched_followup`: a later rescore exists and is linked to the initial score
- `insufficient_evidence`: follow-up did not justify an enriched rescore
- `stale`: new follow-up evidence exists and the record should be rescored
- `failed`: an enriched rescore attempt failed, while the initial score remains available
- `pending_followup`: no usable new follow-up evidence has been observed

### Typed errors for follow-up and writeback

Expected error payload shape for follow-up/writeback endpoints:

```json
{
  "detail": "invalid_request",
  "request_type": "query",
  "errors": {
    "model_id": ["Must be a valid UUID."]
  }
}
```

Typed `detail` values used by the contract:

- `invalid_request`
- `model_not_found`
- `episode_not_found`
- `followup_record_not_found`

Expected status codes:

- `400` for invalid query/body payloads
- `404` for missing model/episode/follow-up records

### Security and permissions

- Production/secure defaults are fail-closed. Do not expose sensitive ICEA endpoints without authentication and an explicit ICEA role.
- `ICEA_DEV_ALLOW_INSECURE=true` is only for local development/demo runs without PHI. Keep it `false` in secure mode.
- Minimum ICEA roles are `viewer_aggregate` for non-nominal aggregate reads, `researcher` for causal/reporting research, `admin` for calibration/config/writeback/federated administration, and `service` for backend-to-backend HANDOVER integration.
- `POST /score/`, `POST /predict/conformal/`, and `/causal/*` require `researcher`, `admin`, or `service`.
- `/writeback/*`, `/fhir/writeback/*`, `/federated/*`, and `/calibrate/` require `admin` or `service`, except `/calibrate/` which is `admin` only.
- `policy_learning`, `fairness`, `causal_discover`, `simulate`, and `federated` remain disabled until explicitly enabled with `ICEA_POLICY_LEARNING_ENABLED`, `ICEA_FAIRNESS_ENABLED`, `ICEA_CAUSAL_DISCOVER_ENABLED`, `ICEA_SIMULATE_ENABLED`, and `ICEA_FEDERATED_ENABLED`.
- In `ICEA_SECURE_MODE=true`, startup fails unless `ICEA_AUTH_REQUIRED=true`, `ICEA_RBAC_ENFORCE=true`, `ICEA_DEV_ALLOW_INSECURE=false`, and a dedicated JWT/JWKS key source is configured.

### Logging and lineage

ICEA+ requests are added to the audit chain without storing PHI in the audit payload.
Lineage in the response identifies:

- formula version/hash
- model id/version
- baseline mode
- causal spec hash when used
- request hash
- source grain

## Intended HANDOVER consumption

Dashboard/service consumers should consume these endpoints as follows:

- patient/episode detail cards: state and lineage only; no numeric operational score
- unit/date summary widgets: `GET /aggregate/`
- tooltip/help and governance detail: `GET /explain/`
- longitudinal episode status: `GET /followup/status/`
- stable episode follow-up contract: `GET /writeback/patient/` with scores suppressed
- stable aggregate summary contract: `GET /writeback/summary/`

The UI should always surface:

- status: `scored_aggregate` / `provisional` / `insufficient_evidence` / `contract_mismatch` / `low_feature_coverage` / `suppressed_low_support` / `shadow_only`
- warnings
- support counts and suppression flags
- `non_individual_use`
- `shadow_mode`
- `exploratory_only`

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

### Model Evidence Governance

`/api/v1/models/` enriches each `ModelArtifact` with `evidence_status`,
`defensible`, `missing_evidence`, `intended_use`, `limitations`,
`temporal_spec_version`, `case_mix_status`, `calibration_status`, and
`validation_status`. It does not label incomplete artifacts as `ready`,
`validated`, or `production`.

`/api/v1/icea-plus/score/` and the legacy `/api/v1/icea/compute/` fail closed
with `detail=model_not_defensible` before computing scores when the selected
model lacks the minimum evidence pack. The response includes `missing_evidence`
and statuses such as `evidence_incomplete`, `calibration_unavailable`,
`validation_unavailable`, and `case_mix_insufficient`; it does not include row
results or numeric score claims.

When `baseline_model_id` is supplied, the baseline artifact passes the same
model-evidence gate before its predictions can contribute to benefit or ICEA+
scoring. Missing or non-defensible baselines return
`baseline_model_not_found` or `baseline_model_not_defensible` without row
results. Responses distinguish `primary_model_evidence_status` from
`baseline_model_evidence_status`.

The minimum model evidence pack is stored in `ModelArtifact.metrics.evidence_pack`
when available and must trace:

- `model_id` and `artifact_created_at`
- `dataset_fingerprint` or `dataset_hash`
- positive integer `training_row_count`
- positive integer `validation_row_count`
- `feature_names`
- `observed_feature_columns`
- `feature_support_status=supported`
- `temporal_spec_version` and `temporal_guardrail_status`
- `outcome_definition` and `outcome_window`
- `case_mix_spec` or `case_mix_unavailable_reason`
- `intended_use=shadow_aggregate_research`
- `non_individual_use=true` and `shadow_mode=true`
- `calibration_summary` or `calibration_unavailable_reason`
- `validation_metrics` or `validation_unavailable_reason`
- `limitations`
- provenance/source commit or an explicit unavailable reason

`validation_unavailable_reason` records why validation evidence is absent, but
does not replace a positive `validation_row_count` or make a model defendible.
Zero, negative, string, missing, or otherwise invalid training/validation row
counts fail closed as incomplete model evidence.

Every declared model feature must be present with at least one real, non-null
value in the raw training dataset. Declared-but-absent, entirely empty/NaN, or
zero-filled-only features set `feature_support_status=incomplete` and invalidate
defensibility. Compatibility zero-fill is not training evidence. Legacy or
imported evidence packs without positive observed-feature support also fail
closed until that support can be audited from real training data.

`calibration_unavailable`, `validation_unavailable`, and
`case_mix_insufficient` are not validation claims. They are audit statuses that
prevent a model from being presented as defendible. `shadow_aggregate_research`
means aggregate, exploratory monitoring only; it is not clinical validation and
is not MDR production readiness.

The optional Docker `seed_demo` command uses the same evidence gate as every
other training route. It generates deterministic synthetic rows with observed
feature, temporal, outcome, validation, calibration, and case-mix support, then
registers the artifact only when it is defensible for
`shadow_aggregate_research`. This demo status is not a clinical validation
claim, is not MDR production readiness, and never permits individual
decisioning or individual score exposure.

The legacy `POST /icea/compute/` route is retained as a controlled compatibility
surface, but it does not execute or return individual `predictions`, `icea`,
`contributions`, scores, or numeric summaries. Successful requests return
`status=shadow_only`, `score_summary_redacted=true`, `results={}`,
`shadow_mode=true`, and `non_individual_use=true`. Non-defensible models remain
blocked before computation.

An evidence pack must declare `feature_names` matching the current
`ModelArtifact.features` sequence. Order is part of the model contract because
training and inference construct the model matrix in that order. Adding,
removing, reordering, or attaching evidence for different features produces
`feature_names_mismatch` and makes the model non-defensible until it is
retrained and supplied with matching evidence.

Training endpoints accept an optional `case_mix_spec` object. A sufficient spec
must declare the required case-mix domains consumed by
`validate_case_mix_spec`: `age`, `severity`, `comorbidity`,
`fragility_or_dependency`, `baseline_risk`, and `baseline_load`, either through
`domains` or `variables`. If `case_mix_spec` is omitted, training derives one
only when training columns clearly cover every required domain; derived specs are
marked `source=derived_from_training_data`. If the domains cannot be derived,
the model is still registered for auditability but remains
`model_not_defensible` / `case_mix_insufficient`.

When `variables` is a list, each value declares both the required domain and the
training column with the same name. Dictionary-form `domains` or `variables`
may map a domain to one or more observed training columns. Contradictory
declarations, missing columns, and columns containing only null values do not
satisfy case-mix support.

Case-mix derivation and validation use only columns that are actually present in
the training payload/model frame and contain at least one observed value.
Declared features that are absent from every row, entirely empty, or introduced
only by model zero-fill do not count as case-mix evidence. Such declarations are
recorded with `declared_feature_missing_from_payload` and leave the artifact
non-defensible when required domains lack real support.

Training evidence also requires one comparable outcome definition across rows.
Individually valid temporal specs do not make a mixed target defensible:
different outcome horizons, incompatible temporal-spec versions, or conflicting
declared outcome definitions produce `mixed_outcome_horizons`,
`outcome_window_not_unique`, or `outcome_definition_not_comparable` and block
scoring as `model_not_defensible`.

`/api/v1/models/train/` validates external dataset rows with the same temporal
frame guardrails used by governed scoring and DB training. Only explicit passing
statuses such as `temporal_guardrails_passed`, `temporal_spec_valid`, or
`passed` can support a defendible model. `not_evaluated_external_payload`,
`insufficient_temporal_spec`, `temporal_leakage_blocked`,
`legacy_outcome_not_defensible`, and unknown states are blocking.

Defendible model evidence must also include the canonical minimum limitations:

- `shadow_aggregate_research_only`
- `not_for_individual_decisioning`
- `not_mdr_production_ready`

An arbitrary non-empty limitations note is insufficient. `/api/v1/models/`
exposes `limitations_status` and `temporal_guardrail_status` alongside
`missing_evidence`.

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
    "status_counts": {
      "complete": 10,
      "provisional": 2,
      "insufficient_evidence": 0
    },
    "score_summary": null,
    "score_summary_redacted": true,
    "summary_redacted": true,
    "redaction_reason": "non_individual_shadow_mode"
  },
  "results": [
    {
      "row_id": "episode:101",
      "status": "shadow_only",
      "provisional": false,
      "score": null,
      "raw_score": null,
      "score_suppressed": true,
      "derived_values_redacted": true,
      "flags": {
        "causal_available": true,
        "low_support": false,
        "high_uncertainty": false,
        "missing_key_inputs": false,
        "insufficient_evidence": false,
        "shadow_mode": true,
        "non_individual_use": true
      }
    }
  ],
  "score_summary": null,
  "score_summary_redacted": true,
  "summary_redacted": true,
  "redaction_reason": "non_individual_shadow_mode"
}
```

The row-level score response is intentionally allow-listed. It never exports
individual numeric derivatives such as confidence, predictions, baselines,
benefit, component breakdowns, SHAP/contributions, uncertainty, legacy ICEA,
aggregation support, or lineage transformations. Full numeric rows remain
internal and are only consumed by governed aggregate and follow-up workflows.

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
- `baseline_model_id=<uuid>` optional; must reference a defensible model artifact
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

Evidence-policy blocks are recorded as `governance_blocked`, not `failed`, and
preserve any prior enriched result for redacted longitudinal display. Technical
execution errors remain `failed`. Current model evidence still governs whether a
stored result may contribute to an aggregate writeback.

### GET `/api/v1/icea-plus/followup/status/`

Returns the longitudinal state for one episode/model pair.

Required query params:

- `episode_id=<int>`
- `model_id=<uuid>`

### GET `/api/v1/icea-plus/writeback/patient/`

Stable episode JSON summary for service follow-up. It is shadow-only: `initial_score`, `enriched_score`, and `current_score` retain lineage and state but suppress `score` and `raw_score`.
If no follow-up record exists and current model evidence blocks bootstrap scoring,
the endpoint returns a controlled `model_not_defensible` response instead of a
server error. Existing legacy records remain score-redacted and expose current
model evidence status.

Required query params:

- `episode_id=<int>`
- `model_id=<uuid>`

### GET `/api/v1/icea-plus/writeback/summary/`

Stable aggregate JSON summary. Results include support counts, suppression flags, and governance metadata and are the only exportable ICEA+ writeback surface.

Stored follow-up results do not replace the current model evidence pack. Before
reading internal aggregate rows, the endpoint revalidates the selected artifact
and any stored dedicated baseline model. A missing, invalidated, or
non-defensible model returns a controlled `model_not_defensible` or
`baseline_model_not_defensible` response with no numeric aggregate. Records from
different models or baseline modes are not silently mixed.

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
- `governance_blocked`: current model or baseline evidence blocks a new rescore without classifying the record as a technical failure
- `stale`: new follow-up evidence exists and the record should be rescored
- `failed`: an enriched rescore attempt failed, while the initial score remains available
- `pending_followup`: no usable new follow-up evidence has been observed

Initial follow-up state is derived from the internal aggregate-only scoring row
when available. The public patient/episode row remains `shadow_only` and
score-redacted, and is never used to infer whether the internal scoring result
was `complete`, `provisional`, or `insufficient_evidence`.

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
- Training endpoints require `researcher` or `admin`; the `service` role cannot create models.
- `POST /score/`, legacy `POST /icea/compute/`, `POST /predict/conformal/`, and `/causal/*` require `researcher`, `admin`, or `service`.
- Follow-up, `/writeback/*`, `/fhir/writeback/*`, pipeline mutations, and FHIR quality detail require `admin` or `service`.
- `/calibrate/`, governance audit/change exports, roster upload, and governance decisions are `admin` only.
- `policy_learning`, `fairness`, `causal_discover`, `simulate`, and `federated` remain disabled until explicitly enabled with `ICEA_POLICY_LEARNING_ENABLED`, `ICEA_FAIRNESS_ENABLED`, `ICEA_CAUSAL_DISCOVER_ENABLED`, `ICEA_SIMULATE_ENABLED`, and `ICEA_FEDERATED_ENABLED`.
- In `ICEA_SECURE_MODE=true`, startup fails unless `ICEA_AUTH_REQUIRED=true`, `ICEA_RBAC_ENFORCE=true`, `ICEA_DEV_ALLOW_INSECURE=false`, and a dedicated JWT/JWKS key source is configured.

#### Endpoint/role matrix

| Surface | Allowed roles | Throttle scope | Export/write behavior |
|---|---|---|---|
| `/models/` | researcher, admin, service | `icea_read` | model/evidence metadata only |
| `/models/train/`, `/pipeline/train/` | researcher, admin | `icea_train` | creates model; audited |
| `/icea/compute/`, `/icea-plus/score/` | researcher, admin, service | `icea_compute` | legacy/individual outputs redacted |
| `/icea-plus/aggregate/` | viewer_aggregate, researcher, admin, service | `icea_compute` | aggregate-only; low support suppressed |
| `/icea-plus/followup/*` | admin, service | `icea_read`, `icea_compute`, or `icea_writeback` | patient score always suppressed |
| `/icea-plus/writeback/summary/` | admin, service | `icea_export` | aggregate-only; evidence and support gated |
| `/icea-plus/writeback/patient/` | admin, service | `icea_writeback` | state/lineage only; no numeric score |
| `/predict/conformal/` | researcher, admin, service | `icea_compute` | identifier and prediction suppressed |
| `/causal/*` | researcher, admin, service | `icea_compute` or `icea_export` | research/shadow only |
| `/pipeline/build-dataset/`, `/pipeline/build-windows/` | admin, service | `icea_compute` | mutating and audited |
| `/dashboard/summary/` | viewer_aggregate, researcher, admin, service | `icea_read` | detailed summaries redacted; counts below 10 are null |
| `/fhir/writeback/list/` | admin, service | `icea_export` | aggregate-only; cells below 10 suppressed |
| `/fhir/writeback/riskassessment/` | admin, service | `icea_writeback` | individual numeric writeback blocked |
| `/governance/*` | admin | `icea_export` or `icea_writeback` | protected audit/governance surface |

Scoped throttling is enabled by default. Operators can override
`ICEA_THROTTLE_SCOPE_ICEA_READ`, `ICEA_THROTTLE_SCOPE_ICEA_COMPUTE`,
`ICEA_THROTTLE_SCOPE_ICEA_TRAIN`, `ICEA_THROTTLE_SCOPE_ICEA_EXPORT`, and
`ICEA_THROTTLE_SCOPE_ICEA_WRITEBACK`.

### Logging and lineage

ICEA+ requests, blocks, exports, training, causal runs, suppression, and writeback
requests are added to the audit chain. The API audit helper uses an explicit
allowlist and drops clinical rows, FHIR payloads, patient identifiers, and
episode identifiers before hashing the event.
Permission-denial audit spam is deduplicated for 60 seconds per normalized
resolved route, method, error, and peppered caller hash. If no resolved route
is available, the view class is used rather than a potentially identifying raw
path. Authenticated callers use their stable user key; anonymous/service
callers use a peppered fingerprint of the securely resolved IP and user-agent.
Missing metadata falls back explicitly to `anonymous_unknown` or
`service_unknown`; raw identities, IPs, user-agents, authorization headers,
cookies, tokens, and path identifiers are not stored.
All request-derived `AuditEvent.actor` values use the stable pseudonymous
format `<caller_kind>:<peppered-hmac-sha256>`. The low-level audit writer also
pseudonymizes unexpected raw actors before persistence, and the admin audit
listing pseudonymizes legacy raw actors before returning them.
Lineage in the response identifies:

- formula version/hash
- model id/version
- baseline mode
- causal spec hash when used
- request hash
- source grain

Legacy `/icea/compute/`, `/predict/conformal/`, FHIR RiskAssessment, and
`/fhir/writeback/list/` remain available for compatibility but are censored,
evidence-gated, permission-gated, throttled, audited, and not valid bypasses for
individual scoring or export governance.

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

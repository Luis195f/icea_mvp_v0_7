# ICEA+ Platform MVP — v0.7.0 (SUPER ULTRA)

## What’s new

### Certification-ready Quality Ops Playbook
- Trial Protocol Report now includes `quality_ops_playbook` with actionable, role-assigned mitigations derived from LOINC-attributed missingness and cohort-flow failures.

### Causal Discovery (best-effort)
- New endpoint `POST /api/v1/causal/discover/` to suggest `dag_edges` using a lightweight PC algorithm.
- Optional `spec.dag_discovery` block in `POST /api/v1/causal/run/` to attach suggestions and (optionally) auto-update `dag_edges`.

### Digital Twin (Counterfactual Simulation)
- New endpoint `POST /api/v1/causal/simulate/` to simulate outcome changes under staffing scenarios.
- Optionally attaches conformal intervals when an XGBoost ModelArtifact with conformal calibration is provided.

### Federated Learning scaffold
- New endpoints under `/api/v1/federated/` to define rounds, submit model updates, and aggregate into a weighted ensemble.
- Optional HMAC integrity gate via `ICEA_FEDERATED_SECRET`.

## Backward compatibility
- All v0.6 endpoints preserved.
- New capabilities are additive and feature-flag friendly.

---

# ICEA+ Platform MVP — v0.7.x extension: Official ICEA+ v1 mathematical core

## Added

- New governed ICEA+ formula modules in `icea_core`:
  - `specs.py`
  - `components.py`
  - `formula.py`
  - `scoring.py`
  - `aggregation.py`
- New versioned governance models:
  - `ICEAPlusFormulaVersion`
  - `ICEAPlusComputation`
- Seeded default pilot formula version `icea_plus_v1` with explicit weights and protocol hash.

## New API

- `POST /api/v1/icea-plus/score/`
- `GET /api/v1/icea-plus/explain/`
- `GET /api/v1/icea-plus/aggregate/`
- `POST /api/v1/icea-plus/calibrate/` (admin-only)

## Formula behavior

- Legacy ICEA SHAP nursing contribution is preserved and surfaced as part of ICEA+ lineage.
- Risk-adjusted baseline benefit is computed explicitly.
- Causal contribution uses the existing causal layer when supported by the current repo/spec.
- Missing causal evidence yields `provisional` status instead of silent falsification.
- Missing required non-causal components yields `insufficient_evidence`.
- Uncertainty integrates conformal width, missingness, OOD/drift heuristic, and low-support burden.

## Governance and traceability

- Formula weights, thresholds, and normalization choices are versioned and hashable.
- Responses expose formula lineage, model lineage, baseline mode, and causal spec hash.
- Audit events are appended without logging PHI.

## Documentation

- Added `docs/ICEA_PLUS_MATH.md`
- Added `docs/ICEA_PLUS_API.md`
- Updated `README.md`

---

# ICEA+ Platform MVP — v0.7.x extension: enriched follow-up rescoring and HANDOVER writeback summary

## Added

- New `ICEAPlusFollowupRecord` model to persist episode-level linkage between:
  - initial ICEA+ score
  - follow-up evidence state
  - enriched follow-up rescore
- New ICEA+ endpoints:
  - `POST /api/v1/icea-plus/followup/ingest/`
  - `POST /api/v1/icea-plus/followup/rescore/`
  - `GET /api/v1/icea-plus/followup/status/`
  - `GET /api/v1/icea-plus/writeback/summary/`
  - `GET /api/v1/icea-plus/writeback/patient/`

## Behavior

- Initial episode scores are now persisted for longitudinal follow-up when they come from DB-backed episode scoring.
- Enriched rescoring reuses the governed Prompt 9 kernel and preserves:
  - formula version
  - protocol hash
  - traceability to the initial computation
- If new follow-up evidence is missing or insufficient, the API returns an explicit state instead of fabricating an enriched score.
- HANDOVER receives a stable JSON summary contract with warnings, provenance, support, timestamps and non-individual-use flags.

## Prudence

- Follow-up rescoring remains observational and exploratory.
- No individual/punitive ranking is exposed by default.
- Team/shift writeback summaries degrade to unit-level when the repo lacks reliable longitudinal support at that granularity.

## Documentation

- Added `docs/ICEA_PLUS_FOLLOWUP.md`
- Added `docs/ICEA_PLUS_WRITEBACK.md`
- Updated `docs/ICEA_PLUS_API.md`
- Updated `README.md`

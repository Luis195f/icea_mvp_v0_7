# ICEA Platform — Pilot MVP (v0.7.4)

This version is a **pilot-grade + trial-emulation kit** with an **enterprise-ready, feature-flagged** architecture:

**FHIR ingestion (encounter‑centered) → normalization (deterministic nursing labels) → dataset builder (roster‑aware) → scheduled training → causal runs (bootstrap + sensitivity + CONSORT emulation + cohort-flow stages) → explicitly opted-in policy learning/fairness audit → dashboard → optional FHIR writeback (RiskAssessment)**

Core principles:
- **Traceability first**: raw FHIR JSON is stored for audit/replay.
- **Semantic interoperability**: resources are normalized into canonical tables for analytics.
- **Model governance**: models are versioned artifacts (features + target + metrics).
- **Model evidence gate**: ICEA/ICEA+ scoring fails closed when a `ModelArtifact`
  lacks dataset fingerprint/hash, temporal spec version, guardrail status,
  outcome window, case-mix evidence, intended use, validation/calibration
  evidence or explicit unavailable reasons, limitations, and provenance/source
  traceability.
- **Graceful degradation**: enterprise features are activated via flags and optional dependencies.
- **Shadow aggregate governance**: ICEA/ICEA+ dashboard and export surfaces are aggregate-only, non-punitive, and suppress low-support cells.
- **Fail-closed high-risk APIs**: scoring, causal, writeback, federated, simulate, policy learning, and fairness require explicit auth/RBAC and feature flags outside dev-only mode.
- **Temporal leakage guards**: defensible datasets, scoring, training, and causal runs require an explicit temporal spec with index time, feature window, outcome window, censoring, and case-mix warnings for aggregate comparisons.

## ICEA vs ICEA+

- **ICEA** remains the legacy predictive nursing attribution based mainly on SHAP/group nursing.
- **ICEA+ v1** is the new official mathematical core exposed in this repo through dedicated endpoints and versioned governance.
- **ICEA+** integrates risk-adjusted benefit, relative nursing attribution, causal effect when defensible, process quality, and explicit uncertainty penalties for aggregate shadow monitoring; it is not an individual causal or labor-performance score.

See:
- `docs/ICEA_PLUS_MATH.md`
- `docs/ICEA_PLUS_API.md`
- `docs/ICEA_PLUS_FOLLOWUP.md`
- `docs/ICEA_PLUS_WRITEBACK.md`
- `docs/CI.md`

---

## Quickstart (Docker)

```bash
cd icea_mvp_v0_7
docker compose up --build
```

Services:
- Backend API: `http://localhost:8000/api/v1/`
- Swagger docs: `http://localhost:8000/api/v1/docs/`
- Dashboard (Streamlit): `http://localhost:8501/`

---

## Endpoints (backwards compatible)

### 1) Ingest FHIR resources (raw)

`POST /api/v1/pipeline/ingest/`

```json
{
  "episode_id": 1,
  "patient_id": "FHIR-PATIENT-ID",
  "encounter_id": "FHIR-ENCOUNTER-ID",
  "mode": "encounter",
  "resources": ["Observation", "Condition", "Procedure"]
}
```

### 2) Normalize FHIR → canonical tables

`POST /api/v1/pipeline/normalize/`

```json
{ "episode_id": 1, "truncate": true }
```

### 3) Build analytic dataset (episode-grain)

`POST /api/v1/pipeline/build-dataset/`

```json
{ "truncate": false }
```

Episode-grain `delta_ri` based on discharge/final-stay values is retained only as
legacy/provisional evidence and is marked `legacy_outcome_not_defensible`.
Defensible training/scoring requires fixed-horizon temporal metadata.

### 3b) Build window-grain dataset (episode-windows)

`POST /api/v1/pipeline/build-windows/`

```json
{ "truncate": false, "window_hours": 12, "align": "shift" }
```

### 4) Train model from DB dataset

`POST /api/v1/pipeline/train/`

```json
{ "name": "icea-xgb", "version": "v0.7.4", "target": "delta_ri" }
```

Training persists `ModelArtifact.metrics.evidence_pack` when the dataset is
temporally defensible. The default intended use is
`shadow_aggregate_research` with `non_individual_use=true` and
`shadow_mode=true`. Missing calibration, validation, case-mix, or provenance is
recorded as an explicit unavailable reason rather than fabricated evidence.
Artifacts with `model_not_defensible`, `calibration_unavailable`,
`validation_unavailable`, or `case_mix_insufficient` must not be treated as
clinically validated or MDR production-ready.

### 4.1) ICEA+ v1 score, explain, aggregate

- `POST /api/v1/icea-plus/score/`
- `GET /api/v1/icea-plus/explain/`
- `GET /api/v1/icea-plus/aggregate/`
- `POST /api/v1/icea-plus/calibrate/` (admin-only)
- `POST /api/v1/icea-plus/followup/ingest/`
- `POST /api/v1/icea-plus/followup/rescore/`
- `GET /api/v1/icea-plus/followup/status/`
- `GET /api/v1/icea-plus/writeback/summary/`
- `GET /api/v1/icea-plus/writeback/patient/`

Example score request:

```json
{
  "model_id": "<uuid>",
  "grain": "episode",
  "from_db": true,
  "causal_spec": {
    "treatment": "nurse_hppd",
    "outcome": "delta_ri",
    "confounders": ["ri_initial", "proc_count"],
    "effect_modifiers": ["ri_initial"],
    "n_estimators": 200
  }
}
```

Follow-up/writeback notes:

- enriched rescoring is episode-level in the current repo state
- the original score is preserved and linked to any later enriched score
- HANDOVER should consume the JSON writeback summary contract, not infer individual staff rankings
- `team` and `shift` writeback summaries degrade to `unit` when the repo lacks reliable longitudinal support at that granularity
- dashboard/export outputs use `n_episodes >= 10`; staff-sensitive cells require `n_staff >= 5`
- patient/episode writeback fields suppress numeric `score`/`raw_score` in shadow mode

### 5) Run causal analysis (trial-emulation)

`POST /api/v1/causal/run/`

```json
{
  "spec": {
    "grain": "window",
    "treatment": "nurse_hppd",
    "outcome": "delta_ri",
    "confounders": ["ri_initial", "proc_count"],
    "effect_modifiers": ["ri_initial"],
    "dag_edges": [["ri_initial","nurse_hppd"],["ri_initial","delta_ri"],["nurse_hppd","delta_ri"]],
    "bootstrap": {"n": 200, "alpha": 0.05},
    "sensitivity": {"e_value": true}
  }
}
```

### 5.1) Retrieve Trial Protocol Report (CONSORT + Quality Ops Playbook)

`GET /api/v1/causal/report/?run_id=<uuid>`

The report JSON includes:
- CONSORT-emulated cohort flow (staged eligibility)
- semantic missingness (LOINC)
- **quality_ops_playbook** (recommended actions + owner roles)
- E-values + refuters (when enabled)
- policy learning + fairness audit
- human-in-the-loop decisions (linked at read-time)

### 5.2) Causal discovery (optional, best-effort PC)

`POST /api/v1/causal/discover/`

```json
{
  "grain": "window",
  "variables": ["nurse_hppd","delta_ri","ri_initial","proc_count"],
  "alpha": 0.05,
  "max_cond_set": 2
}
```

### 5.3) Counterfactual Digital Twin simulation (optional)

`POST /api/v1/causal/simulate/`

```json
{
  "run_id": "<uuid>",
  "model_id": "<optional-xgb-uuid>",
  "scenarios": [
    { "name": "add_staffing", "delta": { "nurse_hppd": 0.5 } },
    { "name": "increase_skillmix", "delta": { "nurse_skillmix": 0.1 } }
  ]
}
```

### 5.4) Federated Causal Learning (scaffold; EHDS/GDPR-friendly)

- Start round: `POST /api/v1/federated/round/start/`
- Submit update: `POST /api/v1/federated/round/<round_id>/submit/`
- Aggregate: `POST /api/v1/federated/round/<round_id>/aggregate/`

Set `ICEA_FEDERATED_SECRET` to require signed updates (header: `X-ICEA-FED-SIG`).

### 6) FHIR validation summary (per episode)

`GET /api/v1/fhir/quality/episode/?episode_id=1`

### 7) Conformal prediction (shadow-only interval)

`POST /api/v1/predict/conformal/`

```json
{ "episode_id": 1, "model_id": "<uuid>", "alpha": 0.05 }
```

The endpoint is retained for governed research compatibility, but command-center surfaces suppress the individual prediction value and must not use it as an operational patient score.

---

## Enterprise mode (feature flags)

### Install optional dependencies

Option A (recommended):

```bash
pip install -r requirements-optional.txt
```

Option B (Docker runtime): set `ICEA_INSTALL_OPTIONAL_DEPS=true`.

### Strict FHIR validation (optional)

- `FHIR_STRICT_VALIDATION=true`
- Optional profile enforcement: `FHIR_REQUIRED_PROFILES=<comma-separated URLs>`

### DoWhy refuters (optional)

Add to causal spec:

```json
{
  "spec": {
    "treatment": "nurse_hppd",
    "outcome": "delta_ri",
    "confounders": ["ri_initial"],
    "dag_edges": [["ri_initial","nurse_hppd"],["ri_initial","delta_ri"],["nurse_hppd","delta_ri"]],
    "refuters": ["random_common_cause", "placebo_treatment_refuter"],
    "refuters_strict": false
  }
}
```

### Institutional fairness audit (Fairlearn) (optional)

If `fairlearn` is installed (optional deps), ICEA+ can compute standardized metrics
in addition to the lightweight Disparate Impact report.

- Global flag: `FAIRNESS_USE_FAIRLEARN=true`
- Or per-run:

```json
{
  "spec": {
    "policy_learning": {"max_depth": 3},
    "fairness": {"use_fairlearn": true, "label_col": "delta_ri"}
  }
}
```

### Row-level entity history (django-simple-history) (optional)

For admin/config lineage (Hospital/Unit/ModelArtifact/CausalSpec):

- Install optional deps.
- Set `ICEA_ENABLE_SIMPLE_HISTORY=true`.
- Run `python manage.py migrate`.

The platform also maintains a DB-side `EntityChangeLog` as a safe fallback
when optional history tables are not enabled.

### Rothman component forensic missingness (optional)

If the Rothman Index observation (LOINC 85556-9) is missing at time-zero or follow-up,
ICEA+ reports which *component* measurements are absent (LOINC-coded proxy list).

- Override component codes: `ROTHMAN_COMPONENT_LOINC_CODES=8867-4,8480-6,...`

### Realtime/ASGI (optional)

- `ICEA_RUN_ASGI=true` (uses daphne/uvicorn if installed)
- `ICEA_ENABLE_CHANNELS=true` (enables Channels routing if installed)

---

## Nursing Command Center (Frontend comercial)

Este paquete incluye un frontend **Next.js** (en español) en `frontend/icea-nursing-command-center/`.

- Dev: `docker compose -f docker-compose.dev.yml up --build` (abre `http://localhost:3000`)
- Base compose: `docker compose --profile ui up --build`





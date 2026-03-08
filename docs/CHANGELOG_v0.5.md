# ICEA+ Platform MVP — v0.5.0

This release **keeps all v0.4 endpoints unchanged** and adds trial‑emulation and governance controls.

## New capabilities

### 1) Trial‑emulation upgrades

- **Bootstrap CIs** for causal ATE (non‑parametric) + optional **E‑value sensitivity** summary.
- **Episode windows** (e.g., 12h nursing shifts) with a window‑grain dataset builder.

Endpoints:
- `POST /api/v1/pipeline/build-windows/`
- `POST /api/v1/causal/run/` (now supports `spec.bootstrap`, `spec.sensitivity`, and `spec.grain="window"`)

Example:

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

### 2) Governance / compliance controls

- **Cryptographic audit log** with hash chaining + HMAC signature.
- **Human‑in‑the‑loop** decisions (override/approve/reject) as first‑class records.
- Optional **API key gate** (`ICEA_API_KEY`) without breaking dev flows.

Endpoints:
- `GET /api/v1/governance/audit/events/?limit=100`
- `POST /api/v1/governance/decision/`

## Environment variables

- `ICEA_VERSION=0.5.0`
- `AUDIT_LOG_SECRET=<strong secret>`
- `ICEA_API_KEY=<optional>`
- `ROTHMAN_OBS_CODES=<comma codes>`

---

# ICEA+ Platform MVP — v0.5.1

v0.5.1 is a **compatibility-preserving hardening release** driven by the "Evaluación Integral" report.

## Additions

### 1) FHIR Facade (validation + pagination)

- Minimal **FHIR schema validation** (resourceType/id/meta.lastUpdated) using **Pydantic**.
- Pagination support via Bundle `link[relation="next"]`.
- Validation metadata persisted in `RawFHIRResource` (no pipeline break).

New endpoint:
- `GET /api/v1/fhir/quality/episode/?episode_id=<id>`

### 2) Semantic traceability for nursing taxonomies (optional)

- Optional mapping layer for **NANDA/NIC/NOC → SNOMED/LOINC**.
- Does **not** ship licensed taxonomy content; mappings are loaded from `NNN_MAPPING_PATH` or `NNN_MAPPING_JSON`.

### 3) Window-target determinism upgrades

- Default RI code includes **LOINC 85556-9** if `ROTHMAN_OBS_CODES` is empty.
- New window builder options: `ri_boundary` (`first_last`|`nearest`) and `ri_boundary_tol_minutes`.


---

# ICEA+ Platform MVP — v0.5.2 (super)

v0.5.2 is an **enterprise-ready, feature-flagged upgrade** that preserves all v0.5.1/v0.5.0/v0.4 endpoint contracts.

## Enterprise flags (Graceful Degradation)

### 1) Strict FHIR validation (optional)

- Enable with `FHIR_STRICT_VALIDATION=true`.
- Uses `fhir.resources` (Pydantic models) **if installed**; otherwise falls back to minimal validation.
- Optional profile enforcement via `FHIR_REQUIRED_PROFILES` (comma-separated URLs expected in `meta.profile`).

### 2) Causal refuters (optional)

- `POST /api/v1/causal/run/` now supports `spec.refuters=[...]`.
- If `dowhy` is installed, ICEA runs DoWhy refutations as an **audit layer**.
- If not installed, the request degrades gracefully unless `spec.refuters_strict=true`.

### 3) Realtime/ASGI scaffolding (optional)

- Optional Channels/WebSockets if `ICEA_ENABLE_CHANNELS=true` and `channels` is installed.
- Container start can install enterprise extras with `ICEA_INSTALL_OPTIONAL_DEPS=true`.
- Run ASGI server with `ICEA_RUN_ASGI=true`.


---

# ICEA+ Platform MVP — v0.5.3 (ultra)

v0.5.3 adds **Top-Paper target-trial protocolization** while preserving full backward compatibility.

## Additions

### 1) Target Trial Template (schema-as-code)

- `POST /api/v1/causal/run/` accepts an optional `spec.target_trial` template:
  - `time_zero` (ISO datetime or symbolic anchor),
  - `eligibility` (human-readable criteria),
  - `follow_up.horizon_hours`,
  - `estimand` (ATE|CATE|ATT).
- The template is validated **in-memory** via Pydantic (no DB migrations; safe flexible JSON).

### 2) Cryptographic sealing of the trial protocol

- A deterministic **SHA-256 protocol hash** is computed over the canonical JSON of the spec and persisted as:
  - `spec.protocol_hash`, `spec.protocol_hash_alg`, `spec.protocol_hash_input`.
- If a client provides `protocol_hash`, the server verifies it matches the computed hash (anti p-hacking).

### 3) Follow-up drives outcome horizon (window grain)

- Window builder supports `follow_up_hours` (optional; defaults to `window_hours`).
- When `grain="window"` and `outcome="delta_ri"`, the causal runner can recompute `delta_ri`
  using the follow-up horizon from `target_trial.follow_up.horizon_hours`.


---

# ICEA+ Platform MVP — v0.5.4 (ultra)

v0.5.4 adds **clinical-hard audit artifacts** (CONSORT-emulated) while preserving full backward compatibility.

## Additions

### 1) Trial Protocol Report (CONSORT-emulated)

- Each `POST /api/v1/causal/run/` now persists a `trial_protocol_report` inside the run summary (best-effort).
- New endpoint: `GET /api/v1/causal/report/?run_id=<uuid>`

The report includes:
- Cohort flow (assessed → eligible → complete-case → analyzed)
- Eligibility audit (if structured expressions are provided)
- Missingness audit with **semantic traceability** via `missing_loinc_*` flags

### 2) Semantic missingness flags (LOINC)

- Dataset builders now add non-breaking numeric features plus missingness flags:
  - `missing_vs_*` and `missing_loinc_<code>`
- Window builder adds:
  - `missing_loinc_85556_9_t0`, `missing_loinc_85556_9_t1`, `missing_delta_ri`

### 3) Human-in-the-loop supervision in the report

- `GET /api/v1/causal/report/` attaches `GovernanceDecision` records linked to the run and reports a supervision status:
  - `accepted` | `modified` | `overridden` | `unreviewed`

### 4) E-value as closing metric

- The report surfaces the E-value block already computed in `summary.sensitivity.e_value` as a closing metric.


---

# ICEA+ Platform MVP — v0.5.5

v0.5.5 incorporates the "Ministerio/EMA-proof" recommendations by strengthening auditability
and adding three forward-looking modules (policy learning, fairness audit, conformal prediction)
without breaking backward compatibility.

## Additions

### 1) Cohort-flow by stages (EMA/RWD)

- The CONSORT-emulated report now includes an explicit `eligibility_stages[]` list with
  `n_before`, `n_after`, and `excluded` per eligibility rule (sequential retention).

### 2) Policy learning (best effort)

- `POST /api/v1/causal/run/` attaches a `summary.policy_learning` block.
- Uses `econml.policy.PolicyTree` if available; otherwise falls back to a shallow sklearn tree.
- Produces an interpretable decision rule for actionable treatment regimes (low vs high exposure).

### 3) Algorithmic fairness audit (disparate impact)

- `POST /api/v1/causal/run/` attaches a `summary.fairness_audit` block.
- Computes selection rates by subgroup and the disparate impact ratio, intended for governance.

### 4) Conformal prediction (individual-risk interval)

- Training stores a calibration quantile in `ModelArtifact.metrics.conformal`.
- New endpoint: `POST /api/v1/predict/conformal/` returns `{pred, interval}`.
- `POST /api/v1/fhir/writeback/riskassessment/` can include conformal intervals with `conformal=true`.

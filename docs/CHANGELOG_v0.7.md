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

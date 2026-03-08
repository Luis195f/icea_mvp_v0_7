# ICEA+ Platform MVP — v0.6.0 (ULTRA)

This release is **backwards compatible** with v0.5.x and v0.4.x endpoints.
It focuses on **enterprise-grade auditability** and **institutional compliance**
without compromising MVP deployability.

## Added

- **Institutional fairness audit (optional)** via `fairlearn` (Feature Flag):
  - `FAIRNESS_USE_FAIRLEARN=true` or `spec.fairness.use_fairlearn=true`
  - Reports demographic parity ratio/difference, and (when a label proxy is available) equalized odds difference.

- **Forensic missingness attribution for Rothman components** (LOINC-coded proxy map):
  - New time-anchored flags `missing_loinc_<code>_t0/_t1` populated when RI is missing.
  - Trial Protocol Report includes `semantic_missingness_components`.
  - Override list via `ROTHMAN_COMPONENT_LOINC_CODES`.

- **Policy robustness audit**:
  - E-value style sensitivity for learned policy value (`policy_learning.robustness.e_value`).
  - Optional DoWhy audit on policy decision (when installed + `spec.policy_learning.audit_dowhy=true`).

- **Row-level entity lineage**:
  - New DB model `EntityChangeLog` capturing create/update/delete for base entities/config.
  - New endpoint: `GET /api/v1/governance/entity-changes/`
  - Optional integration with `django-simple-history` (enterprise flag) for deeper history.

## Changed

- Trial Protocol Report missingness gating now relies on **time-anchored** missing flags
  (suffix `_t0/_t1`), preventing accidental complete-case exclusion due to non-anchored
  `missing_loinc_*` feature flags.


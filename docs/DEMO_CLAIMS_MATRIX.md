# ICEA/ICEA+ Demo Claims Matrix

> ICEA/ICEA+ is shadow-only, aggregate-only, non-individual, non-punitive, not clinically validated, not MDR production-ready, and not a clinical decision tool.

No paid services are required for this demo hardening. The local hardening uses Django/DRF, the existing database, existing test tooling, synthetic fixtures, and open-source dependencies already present in the project. It does not require Sentry, Datadog, AWS, Azure, GCP, a paid FHIR server, a paid terminology server, or any SaaS control plane.

Related local demo controls:

- [Local free demo checklist](DEMO_LOCAL_FREE_CHECKLIST.md)
- [Safe demo narrative](DEMO_SAFE_NARRATIVE.md)

## Capability Status

| Capability | Status | Safe demo claim |
| --- | --- | --- |
| Secure mode startup checks | Implemented | Secure mode fails closed when required local secrets/config are absent. |
| PHI field encryption for raw/writeback payloads | Implemented | Sensitive FHIR JSON fields are encrypted at rest when configured. |
| Legacy `/api/v1/icea/compute/` | Implemented as censored compatibility | The endpoint is retained, but successful responses are shadow-only and redacted with empty or safe `results`. |
| ICEA+ aggregate scoring/export | Implemented partially | Aggregate exploratory monitoring with support suppression is available for synthetic/demo data. |
| Local FHIR validation | Implemented as basic and free | Basic local validation rejects clearly malformed synthetic FHIR bundles without any paid FHIR server. |
| Hospital profile validation | Optional/best-effort | Full hospital profile validation still requires approved institutional profiles and validation process. |
| Terminology validation | Not implemented locally | Local checks do not replace a terminology server or approved ValueSet validation. |
| NANDA/NIC/NOC mapping | Optional/best-effort | Mappings are used only when explicit approved mappings are configured. |
| Causal analysis | Documented but not clinically validated | Exploratory aggregate research signal only; no individual causal claim. |
| Counterfactual simulation | Documented but not clinically validated | Exploratory simulation only; not a validated digital twin. |
| Individual patient/episode/staff scoring | Not permitted for demo | Do not show or claim individual scores, raw scores, predictions, or contributions. |
| Clinical decision support | Not implemented / not permitted | Do not claim bedside-grade, MDR-ready, or decision-tool readiness. |
| ROI or economic proof | Not implemented / not permitted | Economic value remains a hypothesis pending external validation. |

## What Can Be Shown In Demo

- Shadow-only aggregate dashboards with low-support suppression.
- Model-evidence gates that block non-defensible artifacts.
- Legacy compute censorship: `status=shadow_only`, `score_summary_redacted=true`, and empty or safe `results`.
- Basic local FHIR validation for synthetic bundles.
- Explicit NANDA/NIC/NOC mappings when configured, with unmapped codes marked rather than inferred.
- Audit, readiness, smoke, and governance checks with pseudonymous/minimized output.
- FHIR writeback compatibility as shadow-only RiskAssessment metadata with individual numeric values suppressed.

## What Must Not Be Claimed

- Do not claim clinical validation.
- Do not claim MDR, EU AI Act, bedside-grade, or production readiness.
- Do not claim causal contribution by an individual nurse, team, patient, episode, or shift.
- Do not claim ROI demonstrated by this repository.
- Do not claim the system measures the real value of each nurse.
- Do not claim a validated digital twin.
- Do not claim terminology-server or hospital-profile validation from the local basic validator.
- Do not claim the system supports punitive, staffing, compensation, or individual performance decisions.

## Required Claim Rewrites

| Unsafe wording | Required wording |
| --- | --- |
| causal nursing contribution | exploratory aggregate operational-research signal |
| validated digital twin | exploratory simulation, not clinically validated |
| enterprise-ready | limited demo/pilot-readiness |
| bedside-grade | remove the claim |
| demonstrated ROI | economic hypothesis pending validation |
| measures the real value of each nurse | prohibited |

## FHIR Local Validation Scope

FHIR local validation is basic and free. It checks the invariants needed for the demo hardening: `resourceType`, valid `id` shape when present, safe local `reference` shapes, well-formed `Bundle.entry.resource`, supported local resource types, Observation coding presence, Encounter-centered bundle flow when requested, and shadow-only RiskAssessment constraints.

It does not replace hospital profile validation.

It does not replace terminology server validation.

No paid FHIR server is required for this demo hardening.

## Mapping Scope

The normalizer prioritizes LOINC for observations, SNOMED CT for conditions/procedures, and NANDA/NIC/NOC only when explicit mappings are configured. No free-text-to-code mapping is performed.

No inferimos equivalencias clinicas no documentadas.

Mappings NANDA/NIC/NOC requieren ValueSets/mappings aprobados antes de piloto clinico.

La normalizacion actual es exploratoria y no validada clinicamente.

## Remaining Blockers Before Clinical Pilot

- Institution-approved FHIR implementation guide/profile validation.
- Approved terminology server or approved local ValueSet validation workflow.
- Clinically reviewed NANDA/NIC/NOC to SNOMED/LOINC mappings.
- External validation/calibration on appropriate data with governance approval.
- Formal clinical safety case, risk management file, usability validation, monitoring plan, and regulatory classification.
- Data protection impact assessment and institutional security review.
- Explicit policy prohibiting punitive or individual performance use in operational deployment.
- External penetration test.
- Real EHR integration.
- Demonstrated ROI.

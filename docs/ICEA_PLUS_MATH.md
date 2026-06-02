# ICEA+ Mathematical Core v1

## Scope

ICEA+ v1 is the official composite index implemented in the current Django/Django REST architecture of this repo.
It extends the legacy ICEA signal without replacing it:

- `ICEA` = predictive nursing attribution signal, mainly SHAP/group nursing, for aggregate shadow analytics.
- `ICEA+` = composite pilot-grade index integrating benefit, predictive nursing attribution, causal effect when defensible, process quality, and uncertainty penalties for governed aggregate monitoring.

ICEA+ v1 is:

- pilot-grade and calibration-ready
- auditable and versioned
- explicit about provisional states and insufficient evidence
- not a claim of universal clinical validity
- not a substitute for clinical judgment
- not suitable as automatic labor-sanction evidence
- not a patient, nurse, team, or small-shift ranking system

## Why the legacy ICEA alone is not enough

SHAP alone measures explanatory contribution inside a predictive model. It is not proof of individual clinical or professional contribution. That signal can be useful for aggregate model governance, but it does not by itself provide:

- case-mix adjusted benefit vs expected baseline
- causal effect estimates
- process-quality evidence
- explicit uncertainty penalties
- multilevel aggregation rules with severity and exposure shares

## Why causal alone is not enough

A causal estimate alone does not capture:

- whether the observed episode did better or worse than expected for its baseline risk
- how much of the predictive signal is being carried by nursing variables inside the deployed model
- whether data quality and uncertainty make the estimate fragile

## Why uncertainty is explicitly penalized

The repo already contains uncertainty and governance signals:

- conformal interval calibration in `ModelArtifact.metrics.conformal`
- feature distribution snapshots in `ModelArtifact.metrics.feature_stats`
- semantic missingness flags in dataset rows
- low-support conditions in causal/model training metadata

ICEA+ v1 penalizes higher uncertainty instead of silently hiding it.

## Temporal Defensibility

An ICEA/ICEA+ row is numerically defensible only when it declares:

- `index_time`
- `feature_window_start`
- `feature_window_end`
- `outcome_window_start`
- `outcome_window_end`
- `censoring_reason`
- `temporal_spec_version`

The minimum rule is `feature_window_end <= outcome_window_start`. Features after the
feature window are leakage and must be excluded or blocked. Outcomes must be observed
after the feature window and within a fixed horizon. Episode-level `ri_final`,
discharge status, length of stay, and last measurement across the whole stay are legacy
or post-outcome signals unless a protocol explicitly governs them.

Current explicit states:

- `insufficient_temporal_spec`: the row cannot prove index, feature window, outcome window, and censoring.
- `temporal_leakage_blocked`: feature and outcome timing overlap incorrectly or future features are present.
- `legacy_outcome_not_defensible`: the target is based on discharge/final/last-stay information rather than a fixed future horizon.
- `insufficient_outcome_evidence`: the outcome window is censored or unobserved; no outcome is fabricated.
- `case_mix_insufficient`: aggregate comparison lacks explicit baseline adjustment domains.
- `model_not_defensible`: the selected `ModelArtifact` lacks required evidence, governance flags, calibration, validation, or case-mix support.
- `calibration_unavailable`: calibration was not computed or support was insufficient; no calibration value is fabricated.
- `validation_unavailable`: validation metrics were not computed or cannot be traced; no validation value is fabricated.

SHAP and feature importance remain predictive explanations only. They are not causal
attribution for an individual patient, nurse, shift, or unit. Unit comparisons require
support thresholds plus case-mix warnings unless age, severity, comorbidity,
fragility/dependence, baseline risk, and baseline load are declared.

## Model Evidence Pack

ICEA+ treats model governance as part of the scoring contract. A model can only
be considered defendible for the implemented shadow aggregate research surface
when its artifact traces dataset identity, row counts, feature names, temporal
specification, temporal guardrail outcome, outcome definition/window, case-mix
specification, intended use, non-individual/shadow flags, calibration summary,
validation metrics, limitations, and provenance or an explicit unavailable
reason.

Unavailable reasons are allowed for audit completeness, but they do not become
positive evidence. A model with `calibration_unavailable`,
`validation_unavailable`, or `case_mix_insufficient` remains non-defensible.
This prevents a score or model card from quietly converting missing evidence
into a validity claim.

## Formal definition

For each episode/window `i`:

- `x_i^0`: baseline / non-nursing covariates
- `n_i`: nursing exposure/intervention vector
- `y_i`: observed utility-oriented outcome
- `y_hat_i_base`: expected outcome under baseline risk / neutral nursing reference
- `phi_i^N`: grouped nursing predictive contribution from SHAP
- `tau_i^N`: estimated nursing causal effect when a defensible causal spec is available
- `q_i`: process-quality index
- `u_i`: uncertainty penalty index
- `s_i`: severity weight used for aggregation
- `e_i,n`: exposure share attributed to professional `n` when performer evidence exists

### Component definitions

1. Risk-adjusted benefit

`B_i = z(g(y_i) - g(y_hat_i_base))`

Implementation notes in this repo:

- `g(.)` is identity for outcomes where higher is better.
- `g(.)` flips sign for adverse / lower-is-better outcomes.
- `y_hat_i_base` uses one of two governed modes:
  - dedicated baseline model if `baseline_model_id` is supplied
  - otherwise the deployed model with nursing features replaced by cohort-median nursing reference values

2. Relative nursing attribution

`A_i = z(phi_i^N / (sum_j |phi_i,j| + epsilon))`

Implementation notes:

- SHAP is computed from the existing `ICEAEngine`.
- `phi_i^N` is the summed SHAP contribution of governed nursing columns.
- This preserves backward compatibility with the legacy ICEA signal while contextualizing it relative to total explainability.

3. Causal nursing component

`C_i = z(sign_goal * tau_i^N)`

Implementation notes:

- Uses the existing `ICEACausal` layer.
- Default effect mode is marginal per unit treatment effect.
- If the repo cannot support a defensible causal estimate for the requested cohort/spec, `C_i` is omitted and the row becomes `provisional` instead of faking causal certainty.

4. Process quality

`Q_i = z(q_i)`

Current repo-supported basis:

- structured completeness from `missing_*` flags
- documentation consistency from `nurse_proc_count_det / nurse_proc_count` when available
- timeliness from time-anchored `missing_loinc_*_t0/_t1` flags when available

If these signals are not available, process quality is marked unavailable instead of inferred from nonexistent data.

5. Uncertainty penalty

`U_i = z(u_i)`

Current repo-supported basis:

- conformal width burden from `metrics.conformal.q_hat`
- missingness burden from `missing_*` flags
- OOD/drift heuristic from `metrics.feature_stats`
- low-support burden from training rows and causal-fit rows

## Nuclear score

`ICEA+_i_raw = beta0 + betaB*B_i + betaA*A_i + betaC*C_i + betaQ*Q_i - betaU*U_i`

`ICEA+_i = 100 * sigmoid(ICEA+_i_raw)`

### Default pilot weights

Current default seed in the repo:

- `beta0 = 0.0`
- `betaB = 1.0`
- `betaA = 1.0`
- `betaC = 1.0`
- `betaQ = 1.0`
- `betaU = 1.0`

These are explicit pilot defaults, not institutionally calibrated weights.
They are versioned and can be updated through the formula governance path.

## Normalization

The governed default is robust-z normalization using median and MAD:

- method: `robust_z`
- MAD scale: `1.4826`
- epsilon: `1e-6`
- clip: `4.0`
- fallback: standard deviation, then identity fallback if the reference cohort is degenerate

## Availability states

### Complete

Returned when all required components are available:

- benefit
- attribution
- quality
- uncertainty
- causal

### Provisional

Returned when required non-causal components are available but causal is not.
The row includes `causal_available=false` and warning `causal_unavailable_score_is_provisional`.

### Insufficient evidence

Returned when required components are missing and the repo would otherwise have to invent the score.

## Safeguards and warnings

ICEA+ v1 emits explicit safeguards such as:

- `causal_available=false`
- `low_support`
- `high_uncertainty`
- `missing_key_inputs`
- `insufficient_evidence`
- OOD/drift-related warnings when the heuristic fires

## Aggregation

For governed aggregate cells:

`ICEA+_(n,group) = sum_i (s_i * e_i,n * ICEA+_i) / sum_i (s_i * e_i,n + epsilon)`

Current repo support:

- date
- unit
- deidentified shift-like unit/date buckets only when support thresholds are met

Individualizable groupings (`patient`, `episode`, `window`, `nurse`) are not exportable dashboard groupings. `team` and unsupported staff dimensions degrade to `unit`. Cells with fewer than 10 episodes, or fewer than 5 staff when a staff-sensitive dimension is requested, return `suppressed_low_support` with `score=null`.

## Severity and exposure shares

- `s_i` is derived from baseline expected risk severity.
- `e_i,n` comes from observed nursing procedure performer shares when available.
- If individual attribution evidence is absent or weak, ICEA+ does not fabricate nurse-level granularity.

## Traceability

Each ICEA+ score row includes lineage fields such as:

- formula version and protocol hash
- model id/version
- baseline mode and baseline reference values
- causal spec hash when applicable
- outcome and treatment used
- governed nursing columns
- source grain and request hash

## Correct interpretation

ICEA+ v1 should be read as a composite operational-research signal for aggregate shadow monitoring under the current data and assumptions.
It is not a universal clinical truth statement, an individual causal attribution, or a labor performance metric.

Reasonable uses:

- pilot benchmarking
- unit/date monitoring with warnings, support counts, and suppression visible
- input to dashboards and handover views with provisional vs complete distinction
- audit trails for institutional calibration work

Incorrect uses:

- claiming definitive causality from the score alone
- using the score without uncertainty/warning context
- using it as the sole basis for staff punishment or credentialing decisions
- ranking patients, nurses, teams, or small shifts

## HANDOVER integration contract

Dashboard/service consumers should consume ICEA+ through the REST layer and display:

- patient/episode state and lineage without numeric score
- aggregate unit/date score only when support thresholds are met
- compact component breakdown (`B`, `A`, `C`, `Q`, `U`)
- `scored_aggregate` vs `provisional` vs `insufficient_evidence` vs `suppressed_low_support`
- warnings such as high uncertainty or low support

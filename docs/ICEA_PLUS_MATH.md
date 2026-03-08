# ICEA+ Mathematical Core v1

## Scope

ICEA+ v1 is the official composite index implemented in the current Django/Django REST architecture of this repo.
It extends the legacy ICEA signal without replacing it:

- `ICEA` = predictive nursing attribution, mainly SHAP/group nursing.
- `ICEA+` = composite pilot-grade index integrating benefit, predictive nursing attribution, causal effect when defensible, process quality, and uncertainty penalties.

ICEA+ v1 is:

- pilot-grade and calibration-ready
- auditable and versioned
- explicit about provisional states and insufficient evidence
- not a claim of universal clinical validity
- not a substitute for clinical judgment
- not suitable as automatic labor-sanction evidence

## Why the legacy ICEA alone is not enough

SHAP alone measures explanatory contribution inside a predictive model. That is useful, but it does not by itself provide:

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

For actor/team/unit-level aggregation:

`ICEA+_(n,group) = sum_i (s_i * e_i,n * ICEA+_i) / sum_i (s_i * e_i,n + epsilon)`

Current repo support:

- patient/episode
- window
- date
- unit
- shift using window boundaries
- nurse when `NormalizedProcedure.performer_actor_ref` provides reliable performer evidence

If nurse-level attribution is not reliable, aggregation degrades explicitly to unit-level output.

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

ICEA+ v1 should be read as a composite operational-research score of nursing-adjusted contribution under the current data and assumptions.
It is not a universal clinical truth statement.

Reasonable uses:

- pilot benchmarking
- unit/shift monitoring with warnings visible
- input to dashboards and handover views with provisional vs complete distinction
- audit trails for institutional calibration work

Incorrect uses:

- claiming definitive causality from the score alone
- using the score without uncertainty/warning context
- using it as the sole basis for staff punishment or credentialing decisions

## HANDOVER integration contract

HANDOVER should consume ICEA+ through the REST layer and display:

- patient/episode score
- aggregate unit/shift score
- compact component breakdown (`B`, `A`, `C`, `Q`, `U`)
- `complete` vs `provisional` vs `insufficient_evidence`
- warnings such as high uncertainty or low support

# ICEA+ Follow-up and Enriched Rescoring

## Scope

This repo now supports a conservative episode-level longitudinal layer for ICEA+.
It does not replace the governed Prompt 9 kernel. All scoring and rescoring still
flow through `icea_core.scoring.score_icea_plus()`.

Current repo-backed follow-up sources:

- updated `EpisodeFeatureRow` features/targets
- follow-up `NormalizedObservation` rows
- follow-up `NormalizedProcedure` rows
- `EpisodeWindow` presence as additional support metadata

Current limitation:

- enriched rescoring is implemented only at `episode` grain
- window follow-up contributes support metadata, not a separate longitudinal score contract

## States

- `immediate_provisional`: initial score exists but causal support was unavailable
- `complete`: initial score exists with the required components available
- `enriched_followup`: a later rescore was generated from new repo-backed follow-up evidence
- `insufficient_evidence`: follow-up exists but does not justify an enriched rescore
- `stale`: follow-up evidence exists and the record should be rescored
- `failed`: a rescore attempt failed and the initial score remains preserved
- `pending_followup`: no usable new follow-up evidence has been observed yet

The original row result is always retained for traceability. The enriched result is stored as a linked
second computation and never silently overwrites the initial result. Patient/episode writeback summaries suppress numeric score fields and expose state/lineage only.

## Sufficiency rule

The repo only triggers enriched rescoring when all of the following are true:

- an initial ICEA+ episode record exists or can be bootstrapped from the current DB row
- the episode has an outcome supported by the current repo state
- there is new repo-backed follow-up evidence since the initial score or last rescore
- the underlying row remains temporally defensible: follow-up features cannot be used
  as baseline predictors for an earlier outcome window

If those conditions are not met, the API returns an explicit non-enriched state instead
of fabricating a later score.

Follow-up ingestion, rescoring, and writeback summaries use the same model
evidence gate as ICEA+ scoring because they all flow through
`score_icea_plus()`. If the referenced `ModelArtifact` is
`model_not_defensible`, follow-up cannot bootstrap or rescore from that model.
The failure is provisional/shadow governance, not a clinical conclusion about
the patient or staff.

Episode-level follow-up remains legacy/provisional when the only available outcome is
`ri_final`, discharge status, length of stay, or the last observation across the stay.
Future datasets should prefer window-grain rows with an explicit lag:
baseline/index -> feature window -> outcome window -> censoring.

## API

- `POST /api/v1/icea-plus/followup/ingest/`
- `POST /api/v1/icea-plus/followup/rescore/`
- `GET /api/v1/icea-plus/followup/status/`

Example ingest request:

```json
{
  "episode_id": 42,
  "model_id": "<uuid>"
}
```

Example rescore request:

```json
{
  "episode_id": 42,
  "model_id": "<uuid>"
}
```

## Traceability

Each longitudinal record persists:

- episode/model linkage
- initial computation id
- enriched computation id when present
- formula version
- protocol hash
- initial and enriched score payloads
- follow-up status
- warnings and support metadata
- `last_followup_at`
- `last_rescore_at`

The stored result keeps a private aggregate-only row alongside the public
redacted row so later follow-up summaries can calculate supported aggregate
cells. Patient-facing summaries never expose that internal row or an individual
numeric score, and aggregate outputs still apply minimum-cell suppression.

## Prudence and governance

- Follow-up rescoring remains observational and exploratory.
- It does not establish causal proof for an individual patient or professional.
- It must not be used for patient, nurse, team, or small-shift ranking.
- `non_individual_use` remains enabled by default.
- When individual-level nurse attribution is weak, the APIs emit warnings and HANDOVER
  should degrade to team/unit interpretation.

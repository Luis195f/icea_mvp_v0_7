# ICEA Operational Readiness

This repository is pilot/demo software. These checks make the current ICEA
surface more objectively verifiable before a demo or deployment rehearsal. They
do not establish clinical validation, MDR production readiness, individual
scoring permission, punitive use, or decision support authorization.

## Commands

Run readiness from `backend/`:

```bash
python manage.py icea_readiness_check
```

Run the deterministic smoke test from `backend/`:

```bash
python manage.py icea_smoke_test
```

Both commands emit JSON:

- `status=pass`: no blocking failures or warnings.
- `status=warn`: no blocking failures, but operator attention is needed.
- `status=fail`: one or more blocking checks failed.

By default, Django `call_command()` and local runs always return parseable JSON.
Use `--strict-exit` when a shell/CI wrapper should exit with code `1` on
`status=fail`:

```bash
python manage.py icea_readiness_check --strict-exit
python manage.py icea_smoke_test --strict-exit
```

The JSON shape is:

```json
{
  "status": "pass",
  "checks": [{"code": "config.secret_key.present", "status": "pass", "detail": "non-sensitive detail"}],
  "warnings": [],
  "failures": []
}
```

Secrets, tokens, clinical payloads, PHI, raw patient identifiers, and raw audit
actors must not appear in command output.

## Secure Minimum

For a secure/institutional rehearsal, configure at least:

```bash
DJANGO_DEBUG=false
SECRET_KEY=<strong random secret>
ALLOWED_HOSTS=<explicit hosts, no wildcard>
ICEA_SECURE_MODE=true
ICEA_DEV_ALLOW_INSECURE=false
ICEA_AUTH_REQUIRED=true
ICEA_RBAC_ENFORCE=true
JWT_SIGNING_KEY=<dedicated JWT key>
AUDIT_LOG_SECRET=<strong audit secret>
ICEA_ENABLE_THROTTLING=true
```

`JWT_VERIFYING_KEY` or `OIDC_JWKS_URL` can satisfy the token key-source check
instead of `JWT_SIGNING_KEY` when asymmetric/OIDC validation is used.

## Demo Model

Readiness expects a governed `ModelArtifact` for demo smoke:

- `intended_use=shadow_aggregate_research`
- `shadow_mode=true`
- `non_individual_use=true`
- required limitations present
- no declared model features missing from the payload
- evidence defensible only for shadow aggregate research

Seed local synthetic demo data before demo smoke when needed:

```bash
python manage.py seed_demo --rows 800 --name icea-demo --model-version v1
```

The demo model remains synthetic and shadow-only. It is not clinically
validated, not MDR production-ready, not an individual score, not punitive, and
not a clinical decision tool.

## Smoke Contract

The smoke command uses Django's in-process API client and synthetic/repo-backed
demo data. It does not call external services. It verifies:

- health and readiness are parseable
- `/models/` blocks unauthenticated access and responds to an authorized role
- `/icea-plus/score/` does not expose numeric individual score/raw_score/prediction fields
- `/icea-plus/aggregate/` returns shadow aggregate metadata
- legacy `/icea/compute/` remains present but censored
- protected endpoints block unauthenticated requests with `401` or `403`
- writeback/export surfaces are protected and do not expose patient/episode score
- audit events are generated with pseudonymous actors
- non-defensible current and baseline models are blocked

Do not treat HTTP `301` redirects as security success. Use canonical
trailing-slash routes in tests and scripts.

## Endpoint Matrix

| Surface | State | Protection | Permitted output |
| --- | --- | --- | --- |
| `POST /api/v1/icea/compute/` | legacy compatibility | researcher/admin/service | `shadow_only`, redacted summary, empty `results` |
| `POST /api/v1/icea-plus/score/` | governed shadow scoring | researcher/admin/service | row lineage/state only; numeric individual scores suppressed |
| `GET /api/v1/icea-plus/aggregate/` | aggregate dashboard/export | viewer_aggregate/researcher/admin/service | aggregate cells only, low-support suppression |
| `GET /api/v1/icea-plus/writeback/summary/` | aggregate writeback JSON | admin/service | aggregate summary only, evidence/support gated |
| `GET /api/v1/icea-plus/writeback/patient/` | episode follow-up state | admin/service | state/lineage only, no numeric score |
| `POST /api/v1/fhir/writeback/riskassessment/` | legacy FHIR compatibility | admin/service, optional HMAC/anti-replay | shadow-only, model-evidence gated |
| `GET /api/v1/fhir/writeback/list/` | legacy aggregate export | admin/service | aggregate-only, identifiers suppressed |

## CI And Main Hygiene

Check recent CI:

```bash
gh run list --branch main --limit 5
```

Check local cleanliness before merging:

```bash
git status --short --branch
git diff --check
```

The backend CI includes the readiness/smoke command contract tests. Full local
verification remains:

```bash
cd backend
python manage.py test -v 2
```

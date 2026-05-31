# CI/CD Gates

ICEA is an aggregate, exploratory, shadow-mode analytics surface. CI must
protect that posture: no operational individual score, no labor ranking, no
silent zero-fill, no exposed generated model artifacts, and no real secrets.

## Required GitHub Actions jobs

- `hygiene`: runs whitespace checks and fails if a PR versions generated
  `backend/models/*.json`, real `.env` files, SQLite databases, Python caches,
  Node dependency folders, or coverage runtime artifacts.
- `backend-tests`: installs the backend CI dependency set with Python 3.12 and
  runs the full Django suite with `python manage.py test -v 2`.
- `backend-risk-regression-tests`: reruns the high-risk guardrails explicitly:
  fail-closed/RBAC, HANDOVER to ICEA contract mismatch, low feature coverage,
  aggregation suppression, writeback/no individualization, and follow-up
  non-operational score behavior.
- `frontend-check`: uses `npm ci` because the command-center frontend has
  `package-lock.json`, then runs existing `lint`, optional `test`, and `build`
  scripts.
- `codeql`: remains in `.github/workflows/codeql.yml` for Python and
  JavaScript/TypeScript static analysis.

Configure GitHub branch protection so all of these jobs are required before
merge to `main`.

## Local backend verification on Windows

From `C:\h\icea_mvp_v0_7\backend`:

```powershell
C:\h\icea_mvp_v0_7\.venv\Scripts\python.exe manage.py test -v 2
```

From `C:\h\icea_mvp_v0_7`:

```powershell
git diff --check
```

## Dependency notes

`requirements.txt` currently pins `econml==0.15.0` alongside
`scikit-learn==1.5.1`, which is a likely resolver conflict for Linux CI.
`requirements-ci.txt` is intentionally limited to dependencies needed for
backend test discovery/execution and uses `econml==0.16.0`, matching the
Windows requirements pin already present in this repository. Optional enterprise
packages remain in `requirements-optional.txt` and are not installed by CI.

## Current limitations

- CI does not prove external FHIR connectivity, Redis/PostgreSQL deployment
  behavior, hosted OIDC/JWKS availability, or clinical/regulatory validation.
- Causal discovery and policy/fairness features are kept feature-flagged in the
  test environment; CI validates they remain closed unless explicitly enabled.
- Frontend verification depends on GitHub Actions having a normal Node/npm
  toolchain. Local Windows verification may be unavailable if `npm` is not on
  `PATH`.
- Branch protection is a GitHub repository setting and cannot be enforced by
  files in the repo alone.

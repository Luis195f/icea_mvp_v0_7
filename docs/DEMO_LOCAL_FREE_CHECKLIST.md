# ICEA/ICEA+ Local Free Demo Checklist

This checklist is for a local technical demo rehearsal only. It uses synthetic data, local tooling, and ephemeral keys. It is not a clinical validation, production readiness sign-off, MDR/EU AI Act assessment, ROI proof, or authorization for individual decisioning.

Canonical claim boundaries live in [docs/DEMO_CLAIMS_MATRIX.md](DEMO_CLAIMS_MATRIX.md). The safe demo narrative lives in [docs/DEMO_SAFE_NARRATIVE.md](DEMO_SAFE_NARRATIVE.md).

## 1. Local Environment

- Confirm you are on the expected branch:

```powershell
git status --short --branch
```

- Activate the local virtual environment if present:

```powershell
.\.venv\Scripts\Activate.ps1
```

- If no venv is present, use the project Python already configured on the workstation. Do not install dependencies during the demo check.

## 2. Minimum Safe Variables

Use `.env.demo.local.example` as the non-secret template. For a local secure rehearsal, set:

```text
DJANGO_DEBUG=false
SECRET_KEY=<generated locally>
ALLOWED_HOSTS=localhost,127.0.0.1,testserver
ICEA_SECURE_MODE=true
ICEA_DEV_ALLOW_INSECURE=false
ICEA_AUTH_REQUIRED=true
ICEA_RBAC_ENFORCE=true
JWT_SIGNING_KEY=<generated locally>
AUDIT_LOG_SECRET=<generated locally>
PHI_ENCRYPTION_KEYS=<generated Fernet key>
ICEA_ENABLE_THROTTLING=true
CORS_ALLOW_ALL_ORIGINS=false
```

Generate strong ephemeral local values without printing them in shared channels:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If `cryptography` is unavailable, a Fernet-compatible key can be generated with Python standard library code:

```powershell
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Never commit generated values. Never use real patient, clinician, institution, token, password, cloud, SaaS, paid FHIR server, or paid terminology-server credentials for this demo.

## 3. Backend Rehearsal

Run from `backend/` with only local synthetic/demo artifacts:

```powershell
python manage.py migrate
python manage.py seed_demo --rows 800 --name icea-demo --model-version v1
python manage.py icea_readiness_check --strict-exit
python manage.py icea_smoke_test --strict-exit
python manage.py test -v 2
```

Expected posture:

- No PHI.
- No real patient data.
- No real clinician data.
- No individual score exposed.
- Aggregate-only outputs remain aggregate-only.
- Shadow-only outputs remain shadow-only.
- Non-punitive and non-individual use remains explicit.
- Readiness and smoke output do not print secrets, PHI, raw actors, or clinical payloads.

## 4. Frontend Rehearsal

Run from `frontend/icea-nursing-command-center/`:

```powershell
npm run test --if-present
npm run lint --if-present
npm run build
```

Do not run `npm install` as part of the verification script. Use the dependencies already present for the local workspace.

## 5. One-Command Local Verification

From the repo root:

```powershell
.\scripts\verify_demo_local.ps1
```

The script:

- runs `git status --short --branch`
- runs `git diff --check`
- prefers `.\.venv\Scripts\python.exe` when it exists
- generates ephemeral process-local values for `SECRET_KEY`, `JWT_SIGNING_KEY`, `AUDIT_LOG_SECRET`, and `PHI_ENCRYPTION_KEYS`
- sets `ALLOWED_HOSTS=localhost,127.0.0.1,testserver`
- isolates generated demo state under a unique OS temp directory (`db`, `data`, `models`, and `tmp`)
- points `DATABASE_URL`, `ICEA_MODEL_DIR`, `ICEA_DATA_DIR`, `TMP`, `TEMP`, and `TMPDIR` at that temp state for the run
- runs migrations, synthetic seed, backend tests, readiness, smoke, frontend tests, lint, and build
- runs backend unit tests in the repo's normal test posture, then restores strict demo secure mode for readiness and smoke
- removes only script-owned temp state, plus unexpected repo-local `backend/data/` artifacts only when it can prove they were created during the run
- prints final `git status --short --branch`

It does not print secrets, require cloud, call paid services, install dependencies, commit, push, change branches, merge, or use real data.
It should not create or mutate repo-local `backend/db.sqlite3`, `backend/models`, or `backend/data`.

If `npm` is unavailable but dependencies are already present, the script can use
a `node` fallback for the local contract, lint, and build entrypoints. Set
`NODE_EXE` to a local `node.exe` path when Node is not on `PATH`.

## 6. What To Show In Demo

- Local technical readiness output with `status=pass` where applicable.
- Synthetic seed command and clear synthetic-data framing.
- Shadow-only and aggregate-only dashboard/API surfaces.
- Evidence-gated model behavior.
- Redacted legacy compute behavior.
- Low-support suppression and governance metadata.
- Audit/readiness/smoke outputs that avoid secrets and PHI.
- The claims matrix and safe narrative when explaining boundaries.

## 7. What Not To Say In Demo

- Do not say it is clinically validated.
- Do not say it is ready for clinical use or production.
- Do not say it is MDR-ready or EU AI Act-ready.
- Do not say it causally predicts the nursing contribution.
- Do not say it measures the real value of each nurse.
- Do not say ROI is demonstrated.
- Do not say it is bedside-grade or a validated digital twin.
- Do not say it supports individual staffing, reward, punishment, or performance decisions.

## 8. Local Cleanup

The demo verifier stores its generated database, dataset, model, and temp files under an OS temp directory and removes that script-owned directory at exit, even if a step fails. It should not contaminate repo-local `backend/db.sqlite3`, `backend/models`, or `backend/data`.

After manual rehearsal steps, remove generated local artifacts only:

```powershell
Remove-Item -Recurse -Force backend\data -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force coverage, htmlcov, .pytest_cache -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Remove-Item -Recurse -Force frontend\icea-nursing-command-center\.next -ErrorAction SilentlyContinue
```

Do not delete tracked source files, lockfiles, migrations, or docs.

## 9. Before Commit

- `git diff --check` passes.
- `git status --short --branch` shows only expected source/doc/script changes.
- No generated secrets are present in diffs.
- No demo data under `backend/data/` is staged.
- No `.sqlite3`, coverage, cache, `.next`, or `node_modules` artifact is staged.
- No claim text suggests clinical validation, production readiness, MDR/EU AI Act readiness, ROI proof, or individual scoring permission.

## 10. Before PR

- Backend tests pass.
- `icea_readiness_check --strict-exit` passes.
- `icea_smoke_test --strict-exit` passes.
- Frontend test/lint/build pass.
- `.env.demo.local.example` still contains placeholders only.
- The PR description states local/free/synthetic constraints and residual non-local blockers.

## 11. Before Merge

- CI is green or failures are understood and unrelated.
- Review confirms no Prompt 1 security hardening was weakened.
- Review confirms Prompt 2 frontend/backend contract redaction and allowlisting remain intact.
- Review confirms post-hotfix behavior is preserved: malformed `Observation.code` does not 500, raw compute payload inspection remains strict, unknown fields are not preserved as arbitrary parse output, and insecure final `.passthrough()` output was not reintroduced.
- Demo docs and script do not require paid services, cloud, real data, or real secrets.

## What Cannot Be Solved By Local Free Hardening

This repo-only rehearsal cannot resolve:

- external clinical validation
- prospective validation
- calibration on real institutional data
- CEIm/ethics committee approval
- institutional DPIA
- hospital FHIR implementation profiles
- approved terminology server validation
- clinically validated NANDA/NIC/NOC mappings
- external penetration testing
- formal MDR/EU AI Act assessment
- clinical usability evaluation
- integration with a real EHR
- demonstrated ROI

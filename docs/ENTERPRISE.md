# ICEA+ Enterprise Mode (Feature-Flagged)

ICEA+ uses **graceful degradation**: enterprise features are activated only when:
1) the relevant environment flag is enabled, and
2) the optional dependency is installed.

This keeps the MVP image small and reduces operational risk.

---

## 1) Install enterprise extras

```bash
pip install -r requirements-optional.txt
```

Docker runtime install (optional):

- Set `ICEA_INSTALL_OPTIONAL_DEPS=true`.

---

## 2) Strict FHIR validation (schema + profile enforcement)

Enable:

- `FHIR_STRICT_VALIDATION=true`

Optional profile enforcement:

- `FHIR_REQUIRED_PROFILES=<comma-separated profile URLs>`

Fail closed (misconfiguration):

- `FHIR_STRICT_FAIL_CLOSED=true`

Behavior:
- If `fhir.resources` is installed: validates each resource against base FHIR R4 schema.
- If not installed:
  - default: warns and continues using minimal validation (pilot-safe)
  - fail-closed: returns error severity for strict-unavailable

---

## 3) DoWhy refuters (audit layer)

Add to causal spec:

```json
{
  "spec": {
    "treatment": "nurse_hppd",
    "outcome": "delta_ri",
    "confounders": ["ri_initial"],
    "dag_edges": [["ri_initial","nurse_hppd"],["ri_initial","delta_ri"],["nurse_hppd","delta_ri"]],
    "refuters": ["random_common_cause", "placebo_treatment_refuter"],
    "refuters_strict": false
  }
}
```

Notes:
- Refuter results are included under `summary.refuters`.
- ICEA's primary ATE/CATE remains EconML-based; DoWhy is used as an independent **robustness audit**.

---

## 4) ASGI / Realtime (optional)

- `ICEA_RUN_ASGI=true` starts daphne/uvicorn if installed.
- `ICEA_ENABLE_CHANNELS=true` enables Channels routing.

WebSocket health route:
- `ws://<host>/ws/ping/`


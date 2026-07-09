# ICEA/ICEA+ Safe Demo Narrative

Use this narrative for local technical demos with synthetic data only. It complements [docs/DEMO_CLAIMS_MATRIX.md](DEMO_CLAIMS_MATRIX.md) and the local checklist in [docs/DEMO_LOCAL_FREE_CHECKLIST.md](DEMO_LOCAL_FREE_CHECKLIST.md).

## Safe Opening

This is a technical MVP demo of ICEA/ICEA+ in shadow-only mode. It uses synthetic data generated locally for rehearsal. It is not a clinical tool, not clinically validated, not production-ready, and not approved for individual decisions.

The purpose of this demo is to show technical guardrails: secure local configuration, evidence gates, redaction, aggregate-only outputs, auditability, and safe failure modes before any clinical study design.

## Phrases You May Use

- "This is a technical MVP in shadow-only mode."
- "It works with synthetic data for the demo."
- "It does not calculate or display a usable individual score."
- "It is not a clinical tool."
- "It has not been prospectively validated."
- "It is used to explore aggregate signals and governance."
- "The current goal is to reduce technical risk before designing a clinical study."
- "It will not be used to evaluate or sanction nurses."
- "There is no demonstrated ROI."
- "The economic hypothesis requires external validation."

## Phrases You Must Not Use

- "It causally predicts the nursing contribution."
- "It measures the real value of each nurse."
- "It is ready for clinical use."
- "It is ready for production."
- "ROI is demonstrated."
- "MDR-ready."
- "EU AI Act-ready."
- "Bedside-grade."
- "Validated digital twin."
- "It supports individual staffing decisions."
- "It supports sanctioning or rewarding nurses."

## Demo Flow

1. Start with the boundary statement: local, free, synthetic, shadow-only, aggregate-only, non-individual, non-punitive, not clinically validated.
2. Show `.env.demo.local.example` as a placeholder-only template, not real secrets.
3. Run or show `scripts/verify_demo_local.ps1` as the repeatable local rehearsal path.
4. Show synthetic seeding with `seed_demo --rows 800 --name icea-demo --model-version v1`.
5. Show readiness and smoke checks.
6. Show frontend contract/lint/build checks.
7. Show the claims matrix if questions move toward clinical, regulatory, ROI, or individual performance claims.

## Handling Risky Questions

If asked whether this is clinically validated, answer: "No. Passing local readiness and smoke checks only shows that the technical demo guardrails are working. Clinical validation would require external data, institutional approvals, study design, and prospective evaluation."

If asked whether it can be used to rank nurses, answer: "No. The current posture is non-individual and non-punitive. Individual staffing, reward, sanction, or performance decisions are outside the allowed use."

If asked whether it is production-ready, answer: "No. The repo supports a local technical rehearsal. Production readiness would require institutional security, privacy, EHR integration, operations, validation, and regulatory work that cannot be completed by this repo alone."

If asked whether ROI is proven, answer: "No. ROI is a hypothesis that would require external validation and real-world evaluation."

## Non-Local Blockers

The following remain out of scope for a local/free repo rehearsal:

- external clinical validation
- prospective validation
- real-data calibration
- CEIm/ethics approval
- institutional DPIA
- hospital FHIR profile validation
- approved terminology server validation
- clinically reviewed NANDA/NIC/NOC mappings
- external penetration testing
- MDR/EU AI Act formal assessment
- clinical usability evaluation
- EHR integration
- demonstrated ROI

import assert from "node:assert/strict";

import { ICEAComputeResponseSchema } from "../lib/api/types";

const redactedResponse = {
  model: {
    id: "11111111-1111-4111-8111-111111111111",
    name: "icea-demo-governed",
    version: "v-test",
    target: "delta_ri",
    features: ["nurse_hppd", "ri_initial"],
    model_type: "xgboost",
    model_path: "models/demo.json",
    metrics: {},
  },
  summary: {
    status: "shadow_only",
    rows_requested: 1,
    score_summary: null,
    score_summary_redacted: true,
    warnings: ["individual_outputs_suppressed", "legacy_compute_redacted"],
  },
  rows: 1,
  results: {},
  status: "shadow_only",
  detail: "legacy_compute_redacted",
  shadow_mode: true,
  non_individual_use: true,
  score_summary: null,
  score_summary_redacted: true,
  warnings: ["individual_outputs_suppressed", "legacy_compute_redacted"],
};

assert.equal(ICEAComputeResponseSchema.safeParse(redactedResponse).success, true);

for (const forbiddenKey of [
  "prediction",
  "predictions",
  "raw_score",
  "score",
  "contributions",
  "icea",
  "patient_id",
  "episode_id",
]) {
  for (const [location, unsafe] of Object.entries({
    topLevel: {
      ...redactedResponse,
      [forbiddenKey]: forbiddenKey === "predictions" ? [0.42] : "must-not-pass",
    },
    results: {
      ...redactedResponse,
      results: {
        [forbiddenKey]: forbiddenKey === "predictions" ? [0.42] : "must-not-pass",
      },
    },
    summary: {
      ...redactedResponse,
      summary: {
        ...redactedResponse.summary,
        [forbiddenKey]: forbiddenKey === "predictions" ? [0.42] : "must-not-pass",
      },
    },
    nested: {
      ...redactedResponse,
      summary: {
        ...redactedResponse.summary,
        governance: {
          nested: {
            [forbiddenKey]: forbiddenKey === "predictions" ? [0.42] : "must-not-pass",
          },
        },
      },
    },
  })) {
    assert.equal(
      ICEAComputeResponseSchema.safeParse(unsafe).success,
      false,
      `Expected schema to reject ${forbiddenKey} in ${location}`,
    );
  }
}

console.log("ICEA compute contract accepts redacted shadow-only responses and rejects individual outputs.");

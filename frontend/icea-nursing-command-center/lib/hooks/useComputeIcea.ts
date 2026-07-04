"use client";

import { useMutation } from "@tanstack/react-query";
import { bffPost } from "@/lib/api/bff";
import { ICEAComputeResponseSchema, type ICEAComputeResponse } from "@/lib/api/types";

export type ComputePayload = {
  model_id: string;
  data: Array<Record<string, unknown>>;
  features?: string[];
  nurse_cols?: string[];
  group_map?: Record<string, string[]>;
};

/**
 * @deprecated Legacy compute is retained only as a governed audit surface.
 * The backend returns a shadow-only, aggregate-only redacted response with no
 * individual predictions, scores, raw scores, ICEA values, or contributions.
 */
export function useComputeIcea() {
  return useMutation<ICEAComputeResponse, Error, ComputePayload>({
    mutationFn: (payload) => bffPost("/icea/compute/", payload, ICEAComputeResponseSchema)
  });
}

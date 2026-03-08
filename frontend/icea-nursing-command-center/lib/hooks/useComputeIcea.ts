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

export function useComputeIcea() {
  return useMutation<ICEAComputeResponse, Error, ComputePayload>({
    mutationFn: (payload) => bffPost("/icea/compute/", payload, ICEAComputeResponseSchema)
  });
}

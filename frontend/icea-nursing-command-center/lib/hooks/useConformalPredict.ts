"use client";

import { useMutation } from "@tanstack/react-query";
import { bffPost } from "@/lib/api/bff";
import { ConformalPredictResponseSchema, type ConformalPredictResponse } from "@/lib/api/types";

export type ConformalPayload = {
  episode_id: number;
  model_id: string;
  alpha?: number;
};

export function useConformalPredict() {
  return useMutation<ConformalPredictResponse, Error, ConformalPayload>({
    mutationFn: (payload) => bffPost("/predict/conformal/", payload, ConformalPredictResponseSchema)
  });
}

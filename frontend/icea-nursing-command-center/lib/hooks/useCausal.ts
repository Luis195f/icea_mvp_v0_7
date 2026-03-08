"use client";

import { useMutation } from "@tanstack/react-query";
import { bffPost } from "@/lib/api/bff";
import {
  CausalDiscoverResponseSchema,
  CausalRunResponseSchema,
  type CausalDiscoverResponse,
  type CausalRunResponse,
  type CausalReportResponse
} from "@/lib/api/types";
import { z } from "zod";

const AnyRecord = z.record(z.string(), z.unknown());

export function useCausalDiscover() {
  return useMutation<CausalDiscoverResponse, Error, { variables: string[]; grain?: "episode" | "window"; alpha?: number; max_cond_set?: number; forbid_edges?: string[][]; from_db?: boolean; rows?: Array<Record<string, unknown>>; unit_id?: number; }>({
    mutationFn: (payload) => bffPost("/causal/discover/", payload, CausalDiscoverResponseSchema)
  });
}

export function useCausalRun() {
  return useMutation<CausalRunResponse, Error, { spec: Record<string, unknown> }>({
    mutationFn: (payload) => bffPost("/causal/run/", payload, CausalRunResponseSchema)
  });
}

export function useCausalSimulate() {
  return useMutation<Record<string, unknown>, Error, Record<string, unknown>>({
    mutationFn: (payload) => bffPost("/causal/simulate/", payload, AnyRecord)
  });
}

export async function fetchCausalReport(runId: string): Promise<CausalReportResponse> {
  const q = new URLSearchParams({ run_id: runId }).toString();
  const res = await fetch(`/api/bff/causal/report/?${q}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`No se pudo obtener informe causal: ${res.status}`);
  const json: unknown = await res.json();
  return AnyRecord.parse(json);
}

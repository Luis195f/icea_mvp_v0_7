"use client";

import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { bffGet } from "@/lib/api/bff";
import { WritebackItemSchema, type WritebackItem } from "@/lib/api/types";

const WritebacksSchema = z.array(WritebackItemSchema);

export function useWritebacks() {
  return useQuery<WritebackItem[]>({
    queryKey: ["writebacks"],
    queryFn: () => bffGet("/fhir/writeback/list/", WritebacksSchema),
    refetchInterval: 20_000
  });
}

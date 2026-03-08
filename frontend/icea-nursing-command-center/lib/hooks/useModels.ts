"use client";

import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { bffGet } from "@/lib/api/bff";
import { ModelArtifactSchema, type ModelArtifact } from "@/lib/api/types";

const ModelsSchema = z.array(ModelArtifactSchema);

export function useModels() {
  return useQuery<ModelArtifact[]>({
    queryKey: ["models"],
    queryFn: () => bffGet("/models/", ModelsSchema),
    staleTime: 60_000
  });
}

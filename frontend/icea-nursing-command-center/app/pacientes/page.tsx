"use client";

import React from "react";
import { Button, Tile, TextArea, Select, SelectItem, InlineNotification, Accordion, AccordionItem } from "@carbon/react";
import { useModels } from "@/lib/hooks/useModels";
import { useComputeIcea } from "@/lib/hooks/useComputeIcea";
import ShapForceLikeChart from "@/components/charts/ShapForceLikeChart";
import { useConformalPredict } from "@/lib/hooks/useConformalPredict";

type Row = Record<string, unknown>;

function safeJsonParse(input: string): Row | null {
  try {
    const v: unknown = JSON.parse(input);
    if (typeof v === "object" && v !== null && !Array.isArray(v)) return v as Row;
    return null;
  } catch {
    return null;
  }
}

export default function PacientesPage() {
  const { data: models, isLoading: modelsLoading } = useModels();
  const compute = useComputeIcea();
  const conformal = useConformalPredict();

  const [modelId, setModelId] = React.useState<string>("");
  const [rowJson, setRowJson] = React.useState<string>(() =>
    JSON.stringify(
      {
        nurse_hppd: 5.4,
        nurse_skillmix: 0.72,
        nurse_proc_count_det: 14,
        age: 78,
        comorbidity_index: 3,
        los_hours: 72
      },
      null,
      2
    )
  );

  const [episodeId, setEpisodeId] = React.useState<string>("");

  const selectedModel = models?.find((m) => m.id === modelId);

  React.useEffect(() => {
    if (!modelId && models && models.length > 0) setModelId(models[0].id);
  }, [modelId, models]);

  const runCompute = () => {
    const row = safeJsonParse(rowJson);
    if (!row) return;

    const features = selectedModel?.features;
    // Best-effort nurse_cols inference (same heuristic as backend).
    const nurseCols = (features ?? [])
      .filter((f) => f.startsWith("nurse_") || f.startsWith("nic_"))
      .concat(["nurse_proc_count_det", "nurse_hppd", "nurse_skillmix", "nurse_proc_count"])
      .filter((v, idx, arr) => arr.indexOf(v) === idx);

    // Per-feature groups for SHAP-like plot (each feature = its own group).
    const groupMap: Record<string, string[]> = {};
    (features ?? Object.keys(row)).slice(0, 80).forEach((f) => {
      groupMap[f] = [f];
    });
    groupMap["nursing"] = nurseCols;

    compute.mutate({
      model_id: modelId,
      data: [row],
      features: features,
      nurse_cols: nurseCols,
      group_map: groupMap
    });
  };

  const runConformal = () => {
    const ep = Number(episodeId);
    if (!Number.isFinite(ep) || ep <= 0) return;
    conformal.mutate({ episode_id: ep, model_id: modelId });
  };

  const computeRes = compute.data;
  const contrib = computeRes?.results?.contributions ?? null;
  const base = computeRes?.results?.base_value ?? null;
  const pred = computeRes?.results?.predictions?.[0] ?? null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Pacientes: riesgo y explicación</h1>
        <p className="text-sm text-neutral-700">
          Predicción individual con garantía conformal y explicación basada en contribuciones tipo SHAP.
        </p>
      </div>

      <Tile className="p-4 rounded-icea space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Select
            id="model"
            labelText="Modelo (ModelArtifact)"
            value={modelId}
            onChange={(e) => setModelId(e.currentTarget.value)}
            disabled={modelsLoading || !models || models.length === 0}
          >
            {(models ?? []).map((m) => (
              <SelectItem key={m.id} value={m.id} text={`${m.name}:${m.version} (${m.target})`} />
            ))}
          </Select>

          <div className="space-y-2">
            <label className="text-sm font-medium">Episodio (opcional: conformal desde DB)</label>
            <div className="flex gap-2">
              <input
                className="w-full rounded-icea border border-neutral-300 px-3 py-2 text-sm"
                placeholder="episode_id (ej. 1)"
                value={episodeId}
                onChange={(e) => setEpisodeId(e.currentTarget.value)}
              />
              <Button kind="secondary" size="sm" onClick={runConformal} disabled={!modelId || conformal.isPending}>
                Conformal
              </Button>
            </div>
            {conformal.data ? (
              <InlineNotification
                kind="success"
                lowContrast
                title="Predicción conformal"
                subtitle={`pred=${conformal.data.pred.toFixed(3)} | target=${conformal.data.target}`}
              />
            ) : null}
          </div>
        </div>

        <TextArea
          id="row-json"
          labelText="Fila de features (JSON)"
          helperText="Para explicación tipo SHAP usamos /icea/compute/ con group_map por feature (1 fila)."
          value={rowJson}
          onChange={(e) => setRowJson(e.currentTarget.value)}
          rows={10}
        />

        <div className="flex gap-2">
          <Button kind="primary" onClick={runCompute} disabled={!modelId || compute.isPending}>
            Calcular riesgo + contribuciones
          </Button>
          <Button
            kind="ghost"
            onClick={() =>
              setRowJson(
                JSON.stringify(
                  {
                    nurse_hppd: 4.1,
                    nurse_skillmix: 0.61,
                    nurse_proc_count_det: 22,
                    age: 85,
                    comorbidity_index: 5,
                    los_hours: 96
                  },
                  null,
                  2
                )
              )
            }
          >
            Cargar ejemplo (alto riesgo)
          </Button>
        </div>

        {compute.error ? (
          <InlineNotification kind="error" lowContrast title="Error" subtitle={compute.error.message} />
        ) : null}
      </Tile>

      {contrib && base !== null && pred !== null ? (
        <Tile className="p-4 rounded-icea space-y-3">
          <div>
            <h2 className="text-lg font-semibold">Explicación (tipo SHAP — force/waterfall)</h2>
            <p className="text-sm text-neutral-700">
              Base (expected value) + suma de contribuciones = predicción. Se destaca el bloque «nursing» como ICEA.
            </p>
          </div>
          <ShapForceLikeChart contributions={contrib} baseValue={base} prediction={pred} focusGroup="nursing" />
        </Tile>
      ) : null}

      <Accordion>
        <AccordionItem title="Notas clínicas de interpretación">
          <ul className="list-disc pl-6 text-sm text-neutral-800 space-y-1">
            <li>Una contribución positiva empuja el riesgo/resultado hacia arriba; negativa lo reduce.</li>
            <li>ICEA (nursing) cuantifica el aporte marginal de exposiciones de enfermería en la predicción.</li>
            <li>Si el hospital exige ENS Alto, active anti-replay en backend y configure <code>ICEA_AUDIT_SECRET</code> en el BFF.</li>
          </ul>
        </AccordionItem>
      </Accordion>
    </div>
  );
}

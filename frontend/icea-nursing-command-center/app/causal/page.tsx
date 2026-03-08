"use client";

import React from "react";
import { Tile, Button, TextArea, InlineNotification, Accordion, AccordionItem } from "@carbon/react";
import { useCausalDiscover, useCausalRun, useCausalSimulate, fetchCausalReport } from "@/lib/hooks/useCausal";
import CausalDag from "@/components/causal/CausalDag";

function safeParseRecord(text: string): Record<string, unknown> | null {
  try {
    const v: unknown = JSON.parse(text);
    if (typeof v === "object" && v !== null && !Array.isArray(v)) return v as Record<string, unknown>;
    return null;
  } catch {
    return null;
  }
}

export default function CausalPage() {
  const discover = useCausalDiscover();
  const run = useCausalRun();
  const simulate = useCausalSimulate();

  const [vars, setVars] = React.useState<string>("unit_staffing_level,patient_acuity,length_of_stay");
  const [specText, setSpecText] = React.useState<string>(() =>
    JSON.stringify(
      {
        treatment: "unit_staffing_level",
        outcome: "length_of_stay",
        confounders: ["patient_acuity"],
        dag_edges: [
          ["unit_staffing_level", "patient_acuity"],
          ["patient_acuity", "length_of_stay"],
          ["unit_staffing_level", "length_of_stay"]
        ],
        grain: "episode"
      },
      null,
      2
    )
  );

  const [simText, setSimText] = React.useState<string>(() =>
    JSON.stringify(
      {
        spec: {
          treatment: "unit_staffing_level",
          outcome: "length_of_stay",
          confounders: ["patient_acuity"],
          dag_edges: [
            ["unit_staffing_level", "patient_acuity"],
            ["patient_acuity", "length_of_stay"],
            ["unit_staffing_level", "length_of_stay"]
          ],
          grain: "episode"
        },
        scenarios: [
          { name: "Baseline", set: {}, delta: {} },
          { name: "+1 RN equivalente", set: {}, delta: { unit_staffing_level: 1 } }
        ]
      },
      null,
      2
    )
  );

  const [report, setReport] = React.useState<Record<string, unknown> | null>(null);

  const onDiscover = () => {
    const variables = vars.split(",").map((s) => s.trim()).filter(Boolean);
    discover.mutate({ variables, alpha: 0.05, max_cond_set: 2, grain: "episode", from_db: true });
  };

  const onRun = () => {
    const spec = safeParseRecord(specText);
    if (!spec) return;
    run.mutate({ spec });
  };

  React.useEffect(() => {
    const load = async () => {
      const id = run.data?.run_id;
      if (!id) return;
      const r = await fetchCausalReport(id);
      setReport(r);
    };
    void load();
  }, [run.data?.run_id]);

  const onSim = () => {
    const payload = safeParseRecord(simText);
    if (!payload) return;
    simulate.mutate(payload);
  };

  const dagEdges = (discover.data?.result?.dag_edges ?? []) as Array<[string, string]>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Causal & gemelo digital</h1>
        <p className="text-sm text-neutral-700">
          Descubrimiento de DAG (best-effort), análisis causal y simulación contrafactual.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Tile className="p-4 rounded-icea space-y-3">
          <h2 className="text-lg font-semibold">Descubrimiento de DAG</h2>
          <TextArea
            id="vars"
            labelText="Variables (separadas por coma)"
            value={vars}
            onChange={(e) => setVars(e.currentTarget.value)}
            rows={2}
          />
          <Button kind="secondary" onClick={onDiscover} disabled={discover.isPending}>
            Sugerir aristas
          </Button>

          {discover.error ? (
            <InlineNotification kind="error" lowContrast title="Error" subtitle={discover.error.message} />
          ) : null}

          <div className="rounded-icea border border-neutral-200 bg-white p-3">
            <div className="text-sm font-medium">Aristas sugeridas</div>
            <ul className="mt-2 list-disc pl-6 text-sm text-neutral-800 space-y-1">
              {dagEdges.length === 0 ? <li>—</li> : null}
              {dagEdges.map((e, idx) => (
                <li key={idx}>
                  {e[0]} → {e[1]}
                </li>
              ))}
            </ul>
          </div>
        </Tile>

        <Tile className="p-4 rounded-icea space-y-3">
          <h2 className="text-lg font-semibold">DAG interactivo (D3)</h2>
          <p className="text-sm text-neutral-700">
            Herramienta de razonamiento causal (no depender de color; nodos etiquetados).
          </p>
          <CausalDag edges={dagEdges.length > 0 ? dagEdges : [["unit_staffing_level","patient_acuity"],["patient_acuity","length_of_stay"]]} />
        </Tile>
      </div>

      <Tile className="p-4 rounded-icea space-y-3">
        <h2 className="text-lg font-semibold">Ejecutar análisis causal</h2>
        <TextArea
          id="spec"
          labelText="spec (JSON)"
          helperText="Se envía a POST /api/v1/causal/run/. Ajusta según tu protocolo."
          value={specText}
          onChange={(e) => setSpecText(e.currentTarget.value)}
          rows={10}
        />
        <div className="flex gap-2">
          <Button kind="primary" onClick={onRun} disabled={run.isPending}>
            Ejecutar
          </Button>
        </div>
        {run.error ? <InlineNotification kind="error" lowContrast title="Error" subtitle={run.error.message} /> : null}
        {run.data ? <InlineNotification kind="success" lowContrast title="Run creado" subtitle={`run_id=${run.data.run_id}`} /> : null}
      </Tile>

      {report ? (
        <Tile className="p-4 rounded-icea space-y-2">
          <h3 className="text-base font-semibold">Informe (resumen)</h3>
          <pre className="overflow-auto rounded-icea border border-neutral-200 bg-white p-3 text-xs">{JSON.stringify(report, null, 2)}</pre>
        </Tile>
      ) : null}

      <Tile className="p-4 rounded-icea space-y-3">
        <h2 className="text-lg font-semibold">Simulación (gemelo digital)</h2>
        <TextArea
          id="sim"
          labelText="payload (JSON)"
          helperText="Se envía a POST /api/v1/causal/simulate/."
          value={simText}
          onChange={(e) => setSimText(e.currentTarget.value)}
          rows={10}
        />
        <Button kind="secondary" onClick={onSim} disabled={simulate.isPending}>
          Simular
        </Button>
        {simulate.error ? <InlineNotification kind="error" lowContrast title="Error" subtitle={simulate.error.message} /> : null}
        {simulate.data ? (
          <pre className="overflow-auto rounded-icea border border-neutral-200 bg-white p-3 text-xs">{JSON.stringify(simulate.data, null, 2)}</pre>
        ) : null}
      </Tile>

      <Accordion>
        <AccordionItem title="Nota metodológica">
          <p className="text-sm text-neutral-800">
            El DAG formaliza hipótesis causales (confusores, mediadores). El sistema prioriza trazabilidad y gobernanza:
            cada ejecución genera eventos de auditoría y puede requerir decisión HITL en despliegues ENS Alto.
          </p>
        </AccordionItem>
      </Accordion>
    </div>
  );
}

"use client";

import React from "react";
import { Tile, InlineNotification, Button, TextArea } from "@carbon/react";
import { useAuditEvents } from "@/lib/hooks/useAuditEvents";
import { useWritebacks } from "@/lib/hooks/useWritebacks";

function safeParseRecord(text: string): Record<string, unknown> | null {
  try {
    const v: unknown = JSON.parse(text);
    if (typeof v === "object" && v !== null && !Array.isArray(v)) return v as Record<string, unknown>;
    return null;
  } catch {
    return null;
  }
}

export default function GobernanzaPage() {
  const audit = useAuditEvents(50);
  const writebacks = useWritebacks();

  const [decisionJson, setDecisionJson] = React.useState<string>(() =>
    JSON.stringify(
      {
        decision_type: "override",
        actor: "command_center_admin",
        rationale: "Revisión clínica: validar antes de ejecutar writeback.",
        payload: { canal: "dashboard" }
      },
      null,
      2
    )
  );

  const postDecision = async () => {
    const payload = safeParseRecord(decisionJson);
    if (!payload) return;
    const res = await fetch("/api/bff/governance/decision/", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      alert(await res.text());
      return;
    }
    audit.refetch();
    alert("Decisión registrada.");
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Gobernanza & auditoría</h1>
        <p className="text-sm text-neutral-700">
          Trazabilidad criptográfica (cadena) y decisiones HITL. En ENS Alto, esto es infraestructura de seguridad clínica.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Tile className="p-4 rounded-icea space-y-3">
          <h2 className="text-lg font-semibold">Eventos de auditoría (recientes)</h2>
          {audit.error ? <InlineNotification kind="error" lowContrast title="Error" subtitle={audit.error.message} /> : null}
          <div className="overflow-auto rounded-icea border border-neutral-200 bg-white">
            <table className="min-w-full text-xs">
              <thead className="bg-neutral-50">
                <tr>
                  <th className="p-2 text-left">Fecha</th>
                  <th className="p-2 text-left">Tipo</th>
                  <th className="p-2 text-left">Contexto</th>
                  <th className="p-2 text-left">Chain hash</th>
                </tr>
              </thead>
              <tbody>
                {(audit.data?.events ?? []).map((e) => (
                  <tr key={e.id} className="border-t border-neutral-100">
                    <td className="p-2">{String(e.created_at)}</td>
                    <td className="p-2">{e.event_type}</td>
                    <td className="p-2">{e.context ?? ""}</td>
                    <td className="p-2 font-mono">{(e.chain_hash ?? "").slice(0, 16)}…</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Tile>

        <Tile className="p-4 rounded-icea space-y-3">
          <h2 className="text-lg font-semibold">Writebacks (últimos)</h2>
          {writebacks.error ? (
            <InlineNotification kind="error" lowContrast title="Error" subtitle={writebacks.error.message} />
          ) : null}
          <div className="overflow-auto rounded-icea border border-neutral-200 bg-white">
            <table className="min-w-full text-xs">
              <thead className="bg-neutral-50">
                <tr>
                  <th className="p-2 text-left">Fecha</th>
                  <th className="p-2 text-left">Identificador</th>
                  <th className="p-2 text-left">Modelo</th>
                  <th className="p-2 text-left">OK</th>
                </tr>
              </thead>
              <tbody>
                {(writebacks.data ?? []).map((w) => (
                  <tr key={w.id} className="border-t border-neutral-100">
                    <td className="p-2">{String(w.created_at)}</td>
                    <td className="p-2">Suprimido</td>
                    <td className="p-2 font-mono">{w.model_id.slice(0, 8)}…</td>
                    <td className="p-2">{w.ok ? "Sí" : "No"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Tile>
      </div>

      <Tile className="p-4 rounded-icea space-y-3">
        <h2 className="text-lg font-semibold">Decisión HITL</h2>
        <TextArea
          id="decision"
          labelText="Payload (JSON)"
          helperText="POST /api/v1/governance/decision/ (idealmente restringido por RBAC en producción)."
          value={decisionJson}
          onChange={(e) => setDecisionJson(e.currentTarget.value)}
          rows={8}
        />
        <Button kind="primary" onClick={postDecision}>
          Registrar decisión
        </Button>
      </Tile>
    </div>
  );
}

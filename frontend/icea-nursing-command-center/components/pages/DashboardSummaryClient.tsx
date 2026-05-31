"use client";

import React from "react";
import { InlineNotification, Tile, Tag } from "@carbon/react";
import MetricCard from "@/components/cards/MetricCard";
import StatusPill from "@/components/cards/StatusPill";
import { useDashboardSummary } from "@/lib/hooks/useDashboardSummary";

export function DashboardSummaryClient() {
  const { data, isLoading, error } = useDashboardSummary();

  if (isLoading) {
    return (
      <Tile className="p-4 rounded-icea">
        <p className="text-sm text-neutral-700">Cargando telemetría…</p>
      </Tile>
    );
  }

  if (error || !data) {
    return (
      <InlineNotification
        kind="error"
        lowContrast
        title="No se pudo cargar la telemetría del backend"
        subtitle={error ? error.message : "Sin datos"}
      />
    );
  }

  const modelLabel = data.latest_model?.name ? `${data.latest_model.name}:${data.latest_model.version ?? ""}` : "—";

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-4">
        <MetricCard title="Episodios" value={String(data.episodes)} subtitle="Cohorte ingestada" status={<StatusPill kind="info" label="Estructura" />} />
        <MetricCard title="Recursos FHIR" value={String(data.raw_fhir)} subtitle="Crudo validado" status={<StatusPill kind="neutral" label="Proceso" />} />
        <MetricCard title="Filas dataset" value={String(data.dataset_rows)} subtitle="Base agregada" status={<StatusPill kind="info" label="Shadow" />} />
        <MetricCard title="Modelo" value={modelLabel} subtitle="Último artefacto no operativo" status={<StatusPill kind="neutral" label="Validación" />} />
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Tile className="p-4 rounded-icea">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-semibold">Normalizado</h3>
            <Tag type="cool-gray" size="sm">ETL</Tag>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <div><dt className="text-neutral-600">Observaciones</dt><dd className="font-medium">{data.normalized.observations}</dd></div>
            <div><dt className="text-neutral-600">Condiciones</dt><dd className="font-medium">{data.normalized.conditions}</dd></div>
            <div><dt className="text-neutral-600">Procedimientos</dt><dd className="font-medium">{data.normalized.procedures}</dd></div>
            <div><dt className="text-neutral-600">Ventanas</dt><dd className="font-medium">{data.windows}</dd></div>
          </dl>
        </Tile>

        <Tile className="p-4 rounded-icea">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-semibold">Gobernanza</h3>
            <Tag type="purple" size="sm">HITL</Tag>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <div><dt className="text-neutral-600">Eventos auditoría</dt><dd className="font-medium">{data.audit_events}</dd></div>
            <div><dt className="text-neutral-600">Decisiones</dt><dd className="font-medium">{data.governance_decisions}</dd></div>
            <div><dt className="text-neutral-600">Writebacks</dt><dd className="font-medium">{data.writebacks.count}</dd></div>
            <div><dt className="text-neutral-600">D. calidad</dt><dd className="font-medium">{data.latest_data_quality?.id ? "OK" : "—"}</dd></div>
          </dl>
        </Tile>

        <Tile className="p-4 rounded-icea">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-semibold">Dotación</h3>
            <Tag type="green" size="sm">Turnos</Tag>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <div><dt className="text-neutral-600">Turnos roster</dt><dd className="font-medium">{data.roster_shifts}</dd></div>
            <div><dt className="text-neutral-600">Causal agregado</dt><dd className="font-medium">{data.latest_causal?.id ? "OK" : "—"}</dd></div>
            <div><dt className="text-neutral-600">Compute latest</dt><dd className="font-medium">{data.latest_compute?.id ? "OK" : "—"}</dd></div>
            <div><dt className="text-neutral-600">Entrenamiento</dt><dd className="font-medium">{data.latest_training?.id ? "OK" : "—"}</dd></div>
          </dl>
        </Tile>
      </div>
    </div>
  );
}

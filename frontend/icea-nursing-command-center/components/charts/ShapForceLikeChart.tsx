"use client";

type Props = {
  focusGroup?: string;
};

/**
 * @deprecated `/icea/compute/` is shadow-only and aggregate-only. This legacy
 * component intentionally renders a censored state instead of individual SHAP,
 * prediction, ICEA, score, or contribution values.
 */
export default function ShapForceLikeChart({ focusGroup }: Props) {
  return (
    <div className="rounded-icea border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
      <div className="font-semibold">Respuesta censurada por modo shadow-only / aggregate-only</div>
      <div className="mt-1">No se muestran puntuaciones individuales.</div>
      {focusGroup ? <div className="mt-2 text-xs">Grupo solicitado: {focusGroup}</div> : null}
    </div>
  );
}

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EconomicImpact:
    delta_los_days: float
    bed_day_savings: float
    net_savings: float
    notes: str


def simulate_staffing_roi(
    *,
    delta_icea: float,
    elasticity_los_per_icea: float = -0.12,
    cost_per_bed_day: float = 900.0,
    extra_staff_cost: float = 0.0,
) -> EconomicImpact:
    """Translate ICEA improvements into an economic proxy.

    Parameters
    - delta_icea: expected increase in ICEA (signed).
    - elasticity_los_per_icea: expected LOS change (days) per ICEA unit.
      Negative means higher ICEA reduces LOS.
    - cost_per_bed_day: euro per inpatient day (local accounting estimate).
    - extra_staff_cost: incremental staffing cost (same horizon as LOS estimate).

    Returns
    - Estimated LOS delta and savings.

    WARNING: This is a *simulation placeholder* for an MVP dashboard.
    Real deployments should calibrate elasticity using hospital-specific data.
    """

    delta_los = elasticity_los_per_icea * float(delta_icea)
    bed_day_savings = -delta_los * float(cost_per_bed_day)
    net = bed_day_savings - float(extra_staff_cost)

    return EconomicImpact(
        delta_los_days=delta_los,
        bed_day_savings=bed_day_savings,
        net_savings=net,
        notes="MVP proxy. Calibrate elasticity with local quasi-experimental evidence.",
    )

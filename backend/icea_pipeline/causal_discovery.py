from __future__ import annotations

"""Causal discovery utilities (best-effort, pilot-grade).

Implements a small PC (Peter-Clark) algorithm variant using Fisher-Z tests for
(conditional) independence under a linear-Gaussian assumption.

Why this exists:
- The Target Trial/DAG is user-specified today.
- v0.7 adds optional discovery to *suggest* dag_edges based on observed unit data,
  without mutating the protocol unless explicitly requested.

Constraints:
- No new hard dependencies: uses numpy/pandas only.
- Designed for small/medium variable sets (typical causal specs: < 30 vars).
- Never blocks main causal pipeline.
"""

from dataclasses import dataclass
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class DiscoveryResult:
    method: str
    alpha: float
    max_cond_set: int
    variables: list[str]
    dag_edges: list[list[str]]
    undirected_edges: list[list[str]]
    p_values: dict[str, float]
    notes: list[str]


def _corr(x: np.ndarray, y: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size != y.size or x.size < 3:
        return 0.0
    vx = float(np.var(x))
    vy = float(np.var(y))
    if vx <= 0 or vy <= 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def _fisher_z_p(r: float, n: int, k: int = 0) -> float:
    """Two-sided p-value for (partial) correlation via Fisher Z.

    n: sample size
    k: number of conditioning variables
    """
    r = max(min(float(r), 0.999999), -0.999999)
    dof = max(n - k - 3, 1)
    z = 0.5 * np.log((1 + r) / (1 - r)) * np.sqrt(dof)
    # Approximate normal tail without scipy (erfc).
    p = float(2.0 * 0.5 * np.math.erfc(abs(z) / np.sqrt(2.0)))
    return min(max(p, 0.0), 1.0)


def _residualize(y: np.ndarray, Z: np.ndarray) -> np.ndarray:
    """Linear residual of y after regressing on Z (with intercept)."""
    y = np.asarray(y, dtype=float)
    if Z.size == 0:
        return y
    Z = np.asarray(Z, dtype=float)
    if Z.ndim == 1:
        Z = Z.reshape(-1, 1)
    # add intercept
    X = np.concatenate([np.ones((Z.shape[0], 1)), Z], axis=1)
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        y_hat = X @ beta
        return y - y_hat
    except Exception:
        return y


def _partial_corr(df: pd.DataFrame, a: str, b: str, cond: list[str]) -> tuple[float, float]:
    """Return (partial_correlation, p_value)."""
    cols = [a, b] + list(cond)
    sub = df[cols].replace([np.inf, -np.inf], np.nan).dropna()
    n = int(len(sub))
    if n < max(10, 3 + len(cond)):
        return 0.0, 1.0
    xa = sub[a].astype(float).values
    xb = sub[b].astype(float).values
    if cond:
        Z = sub[cond].astype(float).values
        ra = _residualize(xa, Z)
        rb = _residualize(xb, Z)
        r = _corr(ra, rb)
        p = _fisher_z_p(r, n=n, k=len(cond))
        return r, p
    r = _corr(xa, xb)
    p = _fisher_z_p(r, n=n, k=0)
    return r, p


def discover_dag_pc(
    df: pd.DataFrame,
    *,
    variables: list[str],
    alpha: float = 0.05,
    max_cond_set: int = 2,
    forbid_edges: list[list[str]] | None = None,
) -> DiscoveryResult:
    """Run a lightweight PC discovery to propose a DAG.

    Returns:
      - undirected skeleton edges
      - oriented dag_edges (best-effort: v-structures + limited Meek rules)
    """

    forbid = set()
    for e in forbid_edges or []:
        if isinstance(e, (list, tuple)) and len(e) == 2:
            forbid.add((str(e[0]), str(e[1])))
            forbid.add((str(e[1]), str(e[0])))

    vars_ok = [v for v in variables if v in df.columns]
    notes: list[str] = []
    if len(vars_ok) < 3:
        return DiscoveryResult(
            method="pc",
            alpha=float(alpha),
            max_cond_set=int(max_cond_set),
            variables=vars_ok,
            dag_edges=[],
            undirected_edges=[],
            p_values={},
            notes=["insufficient_variables"],
        )

    # adjacency as sets
    adj: dict[str, set[str]] = {v: set(vars_ok) - {v} for v in vars_ok}
    sepset: dict[tuple[str, str], set[str]] = {}
    pvals: dict[str, float] = {}

    # Step 1/2: remove edges by conditional independence tests
    # l = size of conditioning set
    for l in range(0, int(max_cond_set) + 1):
        removed_any = True
        while removed_any:
            removed_any = False
            for (a, b) in list(combinations(vars_ok, 2)):
                if b not in adj[a]:
                    continue
                if (a, b) in forbid:
                    # hard forbid
                    adj[a].discard(b)
                    adj[b].discard(a)
                    sepset[(a, b)] = set()
                    sepset[(b, a)] = set()
                    removed_any = True
                    continue

                neighbors = list(adj[a] - {b})
                if len(neighbors) < l:
                    continue

                # test all conditioning sets of size l
                independent = False
                best_p = 0.0
                best_s = []
                for cond in combinations(neighbors, l) if l > 0 else [()]:
                    _, p = _partial_corr(df, a, b, list(cond))
                    if p > best_p:
                        best_p = p
                        best_s = list(cond)
                    if p > alpha:
                        independent = True
                        sepset[(a, b)] = set(cond)
                        sepset[(b, a)] = set(cond)
                        break

                pvals[f"{a}--{b}"] = float(best_p)
                if independent:
                    adj[a].discard(b)
                    adj[b].discard(a)
                    removed_any = True

    # Skeleton edges
    skeleton = sorted({tuple(sorted((a, b))) for a in vars_ok for b in adj[a] if a < b})

    # Orient v-structures: a - c - b where a and b not adjacent and c not in sepset(a,b)
    directed: set[tuple[str, str]] = set()
    undirected: set[tuple[str, str]] = set(skeleton)

    def _is_adj(x: str, y: str) -> bool:
        return y in adj[x]

    for c in vars_ok:
        neigh = list(adj[c])
        for a, b in combinations(neigh, 2):
            if _is_adj(a, b):
                continue
            sep = sepset.get((a, b), set())
            if c not in sep:
                # a -> c <- b
                directed.add((a, c))
                directed.add((b, c))

    # Remove oriented edges from undirected set
    def _rm_und(a: str, b: str) -> None:
        undirected.discard(tuple(sorted((a, b))))

    for a, b in list(directed):
        _rm_und(a, b)

    # Limited Meek rules (best-effort)
    changed = True
    while changed:
        changed = False
        # Rule 1: a -> b - c and a not adj c => orient b -> c
        for (a, b) in list(directed):
            for c in list(adj[b]):
                if tuple(sorted((b, c))) not in undirected:
                    continue
                if not _is_adj(a, c):
                    directed.add((b, c))
                    _rm_und(b, c)
                    changed = True

        # Rule 2: a - b, and there exists a -> c -> b, then orient a -> b
        for (x, y) in list(undirected):
            a, b = x, y
            # check a -> c -> b
            for c in vars_ok:
                if (a, c) in directed and (c, b) in directed:
                    directed.add((a, b))
                    _rm_und(a, b)
                    changed = True
                    break
                if (b, c) in directed and (c, a) in directed:
                    directed.add((b, a))
                    _rm_und(a, b)
                    changed = True
                    break

    dag_edges = [[a, b] for a, b in sorted(directed)]
    und_edges = [[a, b] for a, b in sorted([list(e) for e in undirected])]

    notes.append("linear_gaussian_assumption")
    notes.append("best_effort_orientation")

    return DiscoveryResult(
        method="pc",
        alpha=float(alpha),
        max_cond_set=int(max_cond_set),
        variables=vars_ok,
        dag_edges=dag_edges,
        undirected_edges=und_edges,
        p_values=pvals,
        notes=notes,
    )

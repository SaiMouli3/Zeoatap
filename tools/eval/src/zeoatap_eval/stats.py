"""Small-sample statistics for rare safety events (SAFETY_VALIDATION §4.2).

Everything here is exact: Clopper-Pearson for proportions and the exact
Poisson (chi-square) interval for rates. No normal approximations, because
safety test sets are small and the interesting cases are at 0 or n.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import beta, chi2


@dataclass(frozen=True)
class Interval:
    lower: float
    upper: float

    def as_dict(self) -> dict:
        return {"lower": self.lower, "upper": self.upper}


def clopper_pearson(k: int, n: int, confidence: float = 0.95, sided: str = "two") -> Interval:
    """Exact binomial interval for k successes in n trials.

    sided="two": the usual two-sided interval, alpha split between the tails.
    sided="lower"/"upper": a one-sided bound at `confidence`; the other side is
    0 or 1. With k=0 and n=30 the two-sided upper bound is 1-0.025**(1/30)
    = 0.1157; the one-sided upper bound is 1-0.05**(1/30) = 0.0950, which is
    what the rule of three (3/n = 0.1) approximates.
    """
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n, got k={k} n={n}")
    if n == 0:
        return Interval(0.0, 1.0)
    alpha = 1.0 - confidence
    if sided == "two":
        a_lo = a_hi = alpha / 2
    elif sided == "lower":
        a_lo, a_hi = alpha, 0.0
    elif sided == "upper":
        a_lo, a_hi = 0.0, alpha
    else:
        raise ValueError(f"sided must be two|lower|upper, got {sided!r}")
    lo = 0.0 if k == 0 or a_lo == 0 else float(beta.ppf(a_lo, k, n - k + 1))
    hi = 1.0 if k == n or a_hi == 0 else float(beta.ppf(1 - a_hi, k + 1, n - k))
    return Interval(lo, hi)


def rule_of_three(n: int) -> float | None:
    """Approximate one-sided 95% upper bound on a rate when 0 of n failed."""
    return 3.0 / n if n > 0 else None


def rule_of_three_n(max_rate: float) -> int:
    """Smallest n for which 0 failures in n supports `rate < max_rate` by the
    rule of three, e.g. 0.10 -> 30, 0.05 -> 60."""
    if not 0 < max_rate < 1:
        raise ValueError("max_rate must be in (0, 1)")
    return math.ceil(3.0 / max_rate - 1e-9)


def poisson_rate_interval(events: int, exposure: float, confidence: float = 0.95,
                          sided: str = "two") -> Interval | None:
    """Exact (Garwood) interval for a Poisson rate, e.g. false alerts per
    camera-hour. Returns None when there is no exposure."""
    if exposure <= 0:
        return None
    alpha = 1.0 - confidence
    a_lo, a_hi = {"two": (alpha / 2, alpha / 2), "lower": (alpha, 0.0), "upper": (0.0, alpha)}[sided]
    lo = 0.0 if events == 0 or a_lo == 0 else chi2.ppf(a_lo, 2 * events) / 2
    hi = math.inf if a_hi == 0 else chi2.ppf(1 - a_hi, 2 * events + 2) / 2
    return Interval(float(lo) / exposure, float(hi) / exposure)


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile (numpy default); None for no data."""
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), q))

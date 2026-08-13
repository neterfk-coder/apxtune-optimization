"""Estadística para benchmarks ruidosos.

Una corrida no es una medición. Todo aquí existe para poder decir
"esto mejoró" sin mentir: mediana en vez de media, intervalo de confianza
por bootstrap, y una prueba no paramétrica antes de aceptar cualquier cambio.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class Estimate:
    """Resumen de una serie de mediciones repetidas."""

    n: int
    median: float
    lo: float          # límite inferior del IC 95%
    hi: float          # límite superior del IC 95%
    mad: float         # desviación absoluta mediana
    rsd: float         # dispersión relativa (%), proxy de ruido del entorno
    samples: list[float]

    @property
    def noisy(self) -> bool:
        """Por encima de ~5% de dispersión el entorno no es confiable."""
        return self.rsd > 5.0

    def __str__(self) -> str:
        return f"{self.median:.3g} [{self.lo:.3g}, {self.hi:.3g}] n={self.n}"


def median(xs: list[float]) -> float:
    if not xs:
        raise ValueError("serie vacía")
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def mad(xs: list[float]) -> float:
    m = median(xs)
    return median([abs(x - m) for x in xs])


def bootstrap_ci(
    xs: list[float], iters: int = 4000, alpha: float = 0.05, seed: int = 0
) -> tuple[float, float]:
    """IC percentil por bootstrap sobre la mediana.

    No asume normalidad, que es justo lo que no se cumple en benchmarks
    (colas largas por throttling, vecinos ruidosos, page faults).
    """
    if len(xs) < 2:
        return (xs[0], xs[0]) if xs else (0.0, 0.0)
    rng = random.Random(seed)
    n = len(xs)
    meds = [median([xs[rng.randrange(n)] for _ in range(n)]) for _ in range(iters)]
    meds.sort()
    lo = meds[max(0, int(iters * alpha / 2))]
    hi = meds[min(iters - 1, int(iters * (1 - alpha / 2)))]
    return lo, hi


def summarize(xs: list[float], seed: int = 0) -> Estimate:
    m = median(xs)
    lo, hi = bootstrap_ci(xs, seed=seed)
    d = mad(xs)
    return Estimate(
        n=len(xs),
        median=m,
        lo=lo,
        hi=hi,
        mad=d,
        rsd=(1.4826 * d / m * 100) if m else 0.0,
        samples=list(xs),
    )


def mann_whitney_u(a: list[float], b: list[float]) -> float:
    """p-valor de dos colas por aproximación normal, con corrección de empates.

    Se usa como filtro: si p >= 0.05 el cambio se descarta aunque la mediana
    haya subido. Evita coleccionar ruido y llamarlo optimización.
    """
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return 1.0

    merged = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks = [0.0] * len(merged)
    tie_term = 0.0
    i = 0
    while i < len(merged):
        j = i
        while j + 1 < len(merged) and merged[j + 1][0] == merged[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg
        t = j - i + 1
        if t > 1:
            tie_term += t**3 - t
        i = j + 1

    ra = sum(r for r, (_, g) in zip(ranks, merged) if g == 0)
    ua = ra - na * (na + 1) / 2
    ub = na * nb - ua
    u = min(ua, ub)

    mu = na * nb / 2
    n = na + nb
    var = (na * nb / 12) * ((n + 1) - tie_term / (n * (n - 1))) if n > 1 else 0
    if var <= 0:
        return 1.0
    z = (u - mu + 0.5) / math.sqrt(var)
    return max(0.0, min(1.0, 2 * 0.5 * math.erfc(abs(z) / math.sqrt(2))))


@dataclass
class Comparison:
    """Veredicto sobre si B es mejor que A."""

    speedup: float          # >1 = mejor, ya orientado según el goal de la métrica
    pct: float              # cambio porcentual
    p_value: float
    significant: bool
    accepted: bool
    reason: str


def compare(
    base: Estimate,
    cand: Estimate,
    goal: str = "max",
    min_effect_pct: float = 2.0,
    alpha: float = 0.05,
) -> Comparison:
    """Acepta el candidato solo si gana de verdad.

    Tres puertas, en orden: dirección correcta, tamaño de efecto mínimo
    (2% por defecto, por debajo de eso no vale la complejidad), y
    significancia estadística.
    """
    if base.median == 0:
        return Comparison(1.0, 0.0, 1.0, False, False, "baseline en cero")

    if goal == "max":
        speedup = cand.median / base.median
    else:
        speedup = base.median / cand.median

    pct = (speedup - 1) * 100
    p = mann_whitney_u(base.samples, cand.samples)
    significant = p < alpha

    if pct < min_effect_pct:
        reason = f"efecto {pct:+.1f}% por debajo del umbral {min_effect_pct:.1f}%"
        accepted = False
    elif not significant:
        reason = f"efecto {pct:+.1f}% pero p={p:.3f} (ruido)"
        accepted = False
    else:
        reason = f"efecto {pct:+.1f}%, p={p:.4f}"
        accepted = True

    return Comparison(speedup, pct, p, significant, accepted, reason)

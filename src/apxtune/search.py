"""Búsqueda de configuración.

Estrategia: descenso por coordenadas en dos fases.

  Exploración   pocas repeticiones, barre los valores de un eje a la vez.
  Confirmación  repeticiones completas, y el cambio solo entra si pasa
                el filtro estadístico de stats.compare().

Se eligió esto sobre una búsqueda bayesiana a propósito. El grid real de
inferencia tiene entre 100 y 5000 puntos y los ejes son casi separables,
así que el descenso por coordenadas converge en O(suma de ejes) en vez de
O(producto), y —más importante— produce una atribución legible: cuánto
aportó cada decisión por separado. Un número final sin desglose no le
sirve a nadie que quiera aprender del resultado.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bench import RunResult, describe, measure, measure_paired
from .isa import ArmTarget
from .space import Workload
from .stats import Comparison, compare


@dataclass
class Step:
    """Un cambio aceptado o rechazado, con su contribución medida."""

    axis: str
    from_value: object
    to_value: object
    comparison: Comparison
    accepted: bool
    metric: str
    before: float
    after: float
    note: str = ""


@dataclass
class Validation:
    """Comprobación final del baseline contra la mejor configuración.

    Es una medición nueva e intercalada, no una resta de números viejos. El
    speedup del titular sale de aquí.
    """

    base: RunResult
    best: RunResult
    comparison: Comparison
    repeats: int
    ok: bool = True
    error: str = ""

    @property
    def speedup(self) -> float:
        return self.comparison.speedup


@dataclass
class TuningRun:
    workload: str
    target: ArmTarget
    baseline: RunResult
    best: RunResult
    steps: list[Step] = field(default_factory=list)
    trials: int = 0
    wall_s: float = 0.0
    aborted: str = ""
    validation: Validation | None = None

    @property
    def accepted(self) -> list[Step]:
        return [s for s in self.steps if s.accepted]

    def speedup(self, metric: str, goal: str) -> float:
        """Speedup global.

        Si hay validación final, ese es el número: se midió head-to-head y en
        el mismo intervalo de tiempo. El cociente de las medianas de la
        búsqueda es el plan B — baseline y óptimo pueden estar separados por
        horas de deriva térmica, y esa diferencia no es atribuible al tuning.
        """
        if self.validation and self.validation.ok:
            return self.validation.speedup
        b = self.baseline.value(metric)
        a = self.best.value(metric)
        if not b or b != b:
            return 1.0
        return a / b if goal == "max" else b / a


def _order_axes(workload: Workload, target: ArmTarget) -> list:
    """Ejes primero por tamaño de efecto esperado.

    Los preludes (recompilar con KleidiAI, cambiar la cuantización) mueven
    la aguja mucho más que afinar --ubatch-size, y además cambian el óptimo
    de los demás ejes. Ajustarlos al final sería tirar el trabajo previo.
    """
    rank = {"prelude": 0, "arg": 1, "env": 2}
    axes = [a for a in workload.axes if len(a.applicable(target)) > 1]
    return sorted(axes, key=lambda a: (rank.get(a.kind, 3), -len(a.applicable(target))))


def tune(
    workload: Workload,
    target: ArmTarget,
    passes: int = 2,
    explore_repeats: int = 3,
    min_effect_pct: float = 2.0,
    alpha: float = 0.05,
    validate: bool = True,
    validate_repeats: int | None = None,
    log=print,
) -> TuningRun:
    metric = workload.primary
    cfg = workload.base_config(target)

    log(f"\n▸ baseline  {describe(cfg, workload) or '(configuración por defecto)'}")
    baseline = measure(workload, cfg, target)
    if not baseline.ok:
        return TuningRun(
            workload=workload.name,
            target=target,
            baseline=baseline,
            best=baseline,
            aborted=f"el baseline no corrió: {baseline.error[:400]}",
        )

    est = baseline.estimates[metric.name]
    log(f"  {metric.name} = {est} {metric.unit}")
    if est.noisy:
        log(
            f"  ⚠ dispersión {est.rsd:.1f}% — entorno ruidoso. "
            "Fija la frecuencia, aísla los cores o usa una instancia dedicada."
        )

    run = TuningRun(workload=workload.name, target=target, baseline=baseline, best=baseline)
    current, current_res = dict(cfg), baseline
    run.wall_s += baseline.wall_s
    run.trials += 1

    axes = _order_axes(workload, target)
    if not axes:
        log("  no hay ejes con más de un valor aplicable a este hardware")
        return run

    for p in range(passes):
        changed = False
        log(f"\n▸ pase {p + 1}/{passes}")

        for ax in axes:
            values = [v for v in ax.applicable(target) if v != current[ax.name]]
            if not values:
                continue

            log(f"\n  · eje {ax.name}" + (f"  ({ax.note})" if ax.note else ""))

            # Fase de exploración: barato, solo para ordenar candidatos.
            scored = []
            for v in values:
                trial = dict(current)
                trial[ax.name] = v
                r = measure(workload, trial, target, repeats=explore_repeats)
                run.trials += 1
                run.wall_s += r.wall_s
                if not r.ok:
                    log(f"      {ax.label(v):<28} falló: {r.error.splitlines()[0][:70]}")
                    continue
                val = r.value(metric.name)
                scored.append((val, v, r))
                log(f"      {ax.label(v):<28} {val:.4g} {metric.unit}")

            if not scored:
                continue

            scored.sort(key=lambda t: t[0], reverse=(metric.goal == "max"))
            _, best_v, _ = scored[0]

            # Fase de confirmación: repeticiones completas contra el actual.
            trial = dict(current)
            trial[ax.name] = best_v
            confirm = measure(workload, trial, target)
            run.trials += 1
            run.wall_s += confirm.wall_s
            if not confirm.ok:
                continue

            cmp_ = compare(
                current_res.estimates[metric.name],
                confirm.estimates[metric.name],
                goal=metric.goal,
                min_effect_pct=min_effect_pct,
                alpha=alpha,
            )
            step = Step(
                axis=ax.name,
                from_value=current[ax.name],
                to_value=best_v,
                comparison=cmp_,
                accepted=cmp_.accepted,
                metric=metric.name,
                before=current_res.value(metric.name),
                after=confirm.value(metric.name),
                note=ax.note,
            )
            run.steps.append(step)

            if cmp_.accepted:
                log(
                    f"    ✓ {ax.name}: {ax.label(current[ax.name])} → {ax.label(best_v)}"
                    f"   {cmp_.pct:+.1f}%  (p={cmp_.p_value:.4f})"
                )
                current, current_res = trial, confirm
                run.best = confirm
                changed = True
            else:
                log(f"    ✗ {ax.name}: descartado — {cmp_.reason}")

        if not changed:
            log("\n  sin cambios aceptados en este pase, convergió")
            break

    if validate and run.accepted:
        run.validation = final_validation(
            workload, target, run, repeats=validate_repeats, alpha=alpha, log=log
        )

    return run


def final_validation(
    workload: Workload,
    target: ArmTarget,
    run: TuningRun,
    repeats: int | None = None,
    alpha: float = 0.05,
    log=print,
) -> Validation:
    """Baseline contra óptimo, medidos de nuevo y entrelazados.

    La búsqueda acumula ~una decena de comparaciones a alpha=0.05, así que por
    pura multiplicidad se espera alguna aceptación afortunada; y su baseline se
    midió al principio, posiblemente horas antes que el óptimo. Ninguna de las
    dos cosas invalida la atribución por pasos, que es orientativa — pero sí
    invalidarían el número del titular si saliera de ahí.

    Esto es una sola comparación, decidida de antemano, con las dos ramas
    medidas en el mismo intervalo de tiempo. Es la única cifra de esta corrida
    que se puede defender sin asteriscos.
    """
    metric = workload.primary
    reps = repeats if repeats is not None else workload.repeats

    log(f"\n▸ validación final  baseline vs óptimo, {reps} pares intercalados")

    base_res, best_res = measure_paired(
        workload, run.baseline.config, run.best.config, target, repeats=reps
    )
    run.trials += 2
    run.wall_s += base_res.wall_s + best_res.wall_s

    if not (base_res.ok and best_res.ok):
        err = (base_res.error or best_res.error).splitlines()[0][:200]
        log(f"  ⚠ la validación no se pudo completar: {err}")
        return Validation(
            base=base_res,
            best=best_res,
            comparison=compare(
                run.baseline.estimates[metric.name],
                run.best.estimates[metric.name],
                goal=metric.goal,
                min_effect_pct=0.0,
                alpha=alpha,
            ),
            repeats=reps,
            ok=False,
            error=err,
        )

    cmp_ = compare(
        base_res.estimates[metric.name],
        best_res.estimates[metric.name],
        goal=metric.goal,
        min_effect_pct=0.0,
        alpha=alpha,
    )
    log(
        f"  {base_res.value(metric.name):.4g} → {best_res.value(metric.name):.4g}"
        f" {metric.unit}   {cmp_.speedup:.2f}×  (p={cmp_.p_value:.4f})"
    )
    if not cmp_.accepted:
        log(
            "  ⚠ la ventaja no sobrevive a la medición entrelazada — "
            "los pasos aceptados pueden ser deriva del entorno, no tuning"
        )
    return Validation(base=base_res, best=best_res, comparison=cmp_, repeats=reps)


def attribution(run: TuningRun, metric: str, goal: str) -> list[dict]:
    """Cascada: cuánto aportó cada decisión aceptada, en orden.

    Es la vista que hace el resultado enseñable — no 'quedó 2.4x más rápido'
    sino 'KleidiAI +38%, Q4_0 +51%, threads +9%'.
    """
    out = []
    for s in run.accepted:
        out.append(
            {
                "axis": s.axis,
                "from": str(s.from_value),
                "to": str(s.to_value),
                "before": s.before,
                "after": s.after,
                "pct": s.comparison.pct,
                "p": s.comparison.p_value,
                "note": s.note,
            }
        )
    return out

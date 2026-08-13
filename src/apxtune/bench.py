"""Ejecución y medición del workload.

Reglas de la casa:
  - Siempre hay warmup, y el warmup no cuenta.
  - Nunca se reporta una corrida sola.
  - Si el entorno está ruidoso, se dice en el reporte en vez de esconderlo.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .isa import ArmTarget
from .space import Workload
from .stats import Estimate, summarize


@dataclass
class RunResult:
    config: dict
    estimates: dict[str, Estimate]
    ok: bool = True
    error: str = ""
    wall_s: float = 0.0
    stdout_tail: str = ""
    command: str = ""

    def value(self, metric: str) -> float:
        e = self.estimates.get(metric)
        return e.median if e else float("nan")


def render(workload: Workload, config: dict, target: ArmTarget) -> tuple[str, dict]:
    """Convierte una configuración en (comando, entorno).

    Los ejes `prelude` se anteponen encadenados con && para que un fallo de
    compilación aborte la medición en vez de medir el binario anterior.
    """
    cmd = workload.command
    env: dict[str, str] = dict(workload.env)
    preludes: list[str] = []

    subs = dict(config)
    subs.setdefault("physical_cores", target.physical_cores)
    subs.setdefault("logical_cpus", target.logical_cpus)
    subs.setdefault("big_cluster", target.big_cluster.cpuset if target.big_cluster else "")

    for ax in workload.axes:
        val = config.get(ax.name)
        if ax.kind == "env" and ax.env:
            env[ax.env] = str(val)
        elif ax.kind == "prelude" and str(val).strip():
            preludes.append(str(val))

    for k, v in subs.items():
        cmd = cmd.replace("{" + k + "}", str(v))

    if preludes:
        cmd = " && ".join(preludes + [cmd])
    return cmd, env


def _run_once(cmd: str, env: dict, cwd: str, timeout: int) -> tuple[bool, str, float]:
    full_env = dict(os.environ)
    full_env.update({k: str(v) for k, v in env.items()})
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            ["bash", "-lc", cmd],
            cwd=cwd,
            env=full_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout tras {timeout}s", time.perf_counter() - t0
    except OSError as e:
        return False, str(e), time.perf_counter() - t0
    dt = time.perf_counter() - t0
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    if p.returncode != 0:
        return False, out, dt
    return True, out, dt


def measure(
    workload: Workload,
    config: dict,
    target: ArmTarget,
    repeats: int | None = None,
    on_progress=None,
) -> RunResult:
    """Corre el workload N veces y devuelve estimaciones con IC."""
    cmd, env = render(workload, config, target)
    reps = repeats if repeats is not None else workload.repeats
    cwd = str(Path(workload.cwd).expanduser().resolve())

    if workload.setup:
        _run_once(workload.setup, env, cwd, workload.timeout_s)

    for _ in range(workload.warmup):
        _run_once(cmd, env, cwd, workload.timeout_s)

    series: dict[str, list[float]] = {m.name: [] for m in workload.metrics}
    total = 0.0
    last_out = ""

    for i in range(reps):
        ok, out, dt = _run_once(cmd, env, cwd, workload.timeout_s)
        total += dt
        last_out = out
        if not ok:
            if workload.teardown:
                _run_once(workload.teardown, env, cwd, 60)
            return RunResult(
                config=config,
                estimates={},
                ok=False,
                error=out.strip()[-1200:],
                wall_s=total,
                command=cmd,
            )
        for m in workload.metrics:
            v = m.extract(out)
            if v is not None:
                series[m.name].append(v)
        if on_progress:
            on_progress(i + 1, reps)

    if workload.teardown:
        _run_once(workload.teardown, env, cwd, 60)

    missing = [k for k, v in series.items() if not v]
    if missing:
        return RunResult(
            config=config,
            estimates={},
            ok=False,
            error=(
                f"métricas sin coincidencias: {', '.join(missing)}. "
                f"Revisa el regex contra esta salida:\n{last_out.strip()[-800:]}"
            ),
            wall_s=total,
            command=cmd,
        )

    return RunResult(
        config=config,
        estimates={k: summarize(v) for k, v in series.items()},
        ok=True,
        wall_s=total,
        stdout_tail=last_out.strip()[-2000:],
        command=cmd,
    )


def _finalize(workload: Workload, arm: dict) -> RunResult:
    """Cierra una rama de medición: series → estimaciones, o error legible."""
    missing = [k for k, v in arm["series"].items() if not v]
    if missing:
        return RunResult(
            config=arm["config"],
            estimates={},
            ok=False,
            error=(
                f"métricas sin coincidencias: {', '.join(missing)}. "
                f"Revisa el regex contra esta salida:\n{arm['out'].strip()[-800:]}"
            ),
            wall_s=arm["wall"],
            command=arm["cmd"],
        )
    return RunResult(
        config=arm["config"],
        estimates={k: summarize(v) for k, v in arm["series"].items()},
        ok=True,
        wall_s=arm["wall"],
        stdout_tail=arm["out"].strip()[-2000:],
        command=arm["cmd"],
    )


def measure_paired(
    workload: Workload,
    config_a: dict,
    config_b: dict,
    target: ArmTarget,
    repeats: int | None = None,
    on_progress=None,
) -> tuple[RunResult, RunResult]:
    """Mide dos configuraciones intercaladas: A,B,B,A,A,B…

    Medir A entero y después B entero deja que la deriva del entorno se cuele
    dentro de la diferencia. El throttling térmico, un vecino ruidoso o un
    cambio de frecuencia son lentos comparados con una corrida, así que si
    aparecen a mitad de camino castigan sistemáticamente a la segunda
    configuración: el resultado ya no dice cuál es mejor, dice cuál se midió
    después.

    Intercalando, cualquier tendencia temporal se reparte por igual entre las
    dos ramas y lo que queda es la diferencia real. El orden se alterna en cada
    ronda para que tampoco haya un sesgo fijo dentro del par (la segunda corrida
    de una pareja arranca con las cachés ya calientes de la primera).

    Si cualquiera de las dos ramas falla, la comparación entera queda anulada:
    se devuelven ambas con ok=False, porque media pareja no compara nada.
    """
    reps = repeats if repeats is not None else workload.repeats
    cwd = str(Path(workload.cwd).expanduser().resolve())

    arms = []
    for cfg in (config_a, config_b):
        cmd, env = render(workload, cfg, target)
        arms.append(
            {
                "config": cfg,
                "cmd": cmd,
                "env": env,
                "series": {m.name: [] for m in workload.metrics},
                "wall": 0.0,
                "out": "",
            }
        )

    if workload.setup:
        _run_once(workload.setup, arms[0]["env"], cwd, workload.timeout_s)

    for arm in arms:
        for _ in range(workload.warmup):
            _run_once(arm["cmd"], arm["env"], cwd, workload.timeout_s)

    for i in range(reps):
        for idx in ((0, 1) if i % 2 == 0 else (1, 0)):
            arm = arms[idx]
            ok, out, dt = _run_once(arm["cmd"], arm["env"], cwd, workload.timeout_s)
            arm["wall"] += dt
            arm["out"] = out
            if not ok:
                if workload.teardown:
                    _run_once(workload.teardown, arm["env"], cwd, 60)
                failed = RunResult(
                    config=arm["config"],
                    estimates={},
                    ok=False,
                    error=out.strip()[-1200:],
                    wall_s=arm["wall"],
                    command=arm["cmd"],
                )
                other = RunResult(
                    config=arms[1 - idx]["config"],
                    estimates={},
                    ok=False,
                    error="anulada: la rama pareja falló a mitad de la validación",
                    wall_s=arms[1 - idx]["wall"],
                    command=arms[1 - idx]["cmd"],
                )
                return (failed, other) if idx == 0 else (other, failed)
            for m in workload.metrics:
                v = m.extract(out)
                if v is not None:
                    arm["series"][m.name].append(v)
        if on_progress:
            on_progress(i + 1, reps)

    if workload.teardown:
        _run_once(workload.teardown, arms[0]["env"], cwd, 60)

    return _finalize(workload, arms[0]), _finalize(workload, arms[1])


def describe(config: dict, workload: Workload) -> str:
    parts = []
    for ax in workload.axes:
        if ax.name in config:
            parts.append(f"{ax.name}={ax.label(config[ax.name])}")
    return " ".join(parts)

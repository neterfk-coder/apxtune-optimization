"""Definición declarativa del workload y del espacio de configuración.

Un workload es un archivo TOML: qué comando ejecutar, cómo extraer las
métricas de su salida, y qué ejes se pueden mover. apxtune no sabe nada
de llama.cpp ni de vLLM; todo el conocimiento del motor vive en el TOML,
así que añadir un motor nuevo no requiere tocar código.
"""

from __future__ import annotations

import itertools
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .isa import ArmTarget


@dataclass
class Metric:
    name: str
    regex: str
    goal: str = "max"           # max | min
    unit: str = ""
    primary: bool = False
    aggregate: str = "last"     # last | first | mean | sum

    def extract(self, text: str) -> float | None:
        vals = [float(m) for m in re.findall(self.regex, text, re.M)]
        if not vals:
            return None
        if self.aggregate == "first":
            return vals[0]
        if self.aggregate == "mean":
            return sum(vals) / len(vals)
        if self.aggregate == "sum":
            return sum(vals)
        return vals[-1]


@dataclass
class Axis:
    """Un eje del espacio de búsqueda.

    kind:
      arg      -> se sustituye en la plantilla del comando como {nombre}
      env      -> se exporta como variable de entorno
      prelude  -> ejecuta un comando antes de medir (p.ej. recompilar)
    """

    name: str
    values: list
    kind: str = "arg"
    env: str | None = None
    requires: list[str] = field(default_factory=list)   # features de ISA necesarias
    labels: dict = field(default_factory=dict)
    note: str = ""

    def applicable(self, target: ArmTarget) -> list:
        """Descarta valores que este silicio no puede ejecutar."""
        if not self.requires:
            return list(self.values)
        if target.has(*self.requires):
            return list(self.values)
        # El primer valor se toma como el seguro/por defecto.
        return [self.values[0]]

    def label(self, value) -> str:
        return str(self.labels.get(str(value), value))


@dataclass
class Workload:
    name: str
    command: str
    metrics: list[Metric]
    axes: list[Axis]
    description: str = ""
    warmup: int = 1
    repeats: int = 7
    timeout_s: int = 900
    cwd: str = "."
    env: dict = field(default_factory=dict)
    setup: str = ""
    teardown: str = ""
    defaults: dict = field(default_factory=dict)

    @property
    def primary(self) -> Metric:
        for m in self.metrics:
            if m.primary:
                return m
        return self.metrics[0]

    def base_config(self, target: ArmTarget) -> dict:
        """Configuración de partida: el valor por defecto de cada eje."""
        cfg = {}
        for ax in self.axes:
            vals = ax.applicable(target)
            cfg[ax.name] = self.defaults.get(ax.name, vals[0])
        return cfg

    def axis(self, name: str) -> Axis | None:
        return next((a for a in self.axes if a.name == name), None)

    def grid_size(self, target: ArmTarget) -> int:
        n = 1
        for ax in self.axes:
            n *= max(1, len(ax.applicable(target)))
        return n

    def full_grid(self, target: ArmTarget) -> list[dict]:
        names = [a.name for a in self.axes]
        pools = [a.applicable(target) for a in self.axes]
        return [dict(zip(names, combo)) for combo in itertools.product(*pools)]


def _expand_tokens(values, target: ArmTarget) -> list:
    """Sustituye tokens dinámicos que dependen del hardware detectado.

    Permite escribir un TOML portable: '$physical_cores' se resuelve
    distinto en un Graviton4 de 64 cores que en un Raspberry Pi 5.
    """
    big = target.big_cluster
    table = {
        "$physical_cores": target.physical_cores,
        "$logical_cpus": target.logical_cpus,
        "$half_cores": max(1, target.physical_cores // 2),
        "$quarter_cores": max(1, target.physical_cores // 4),
        # Máscara para taskset que cubre todas las CPUs: 0-(N-1), no 0-N.
        "$all_cpus": f"0-{target.logical_cpus - 1}" if target.logical_cpus > 1 else "0",
        "$big_cluster": (big.cpuset if big else f"0-{max(0, target.logical_cpus - 1)}"),
        "$big_count": (big.count if big else target.physical_cores),
        "$numa_nodes": target.numa_nodes,
    }
    out = []
    for v in values:
        if not isinstance(v, str):
            out.append(v)
        elif v in table:
            out.append(table[v])
        elif v == "$core_sweep":
            # Barrido típico: 1/4, 1/2, 3/4 y todos los cores físicos.
            p = target.physical_cores
            out.extend(sorted({max(1, p // 4), max(1, p // 2), max(1, 3 * p // 4), p}))
        elif "$" in v:
            # Token embebido en una cadena compuesta, p.ej. "0-$logical_cpus".
            # Los más largos primero para que $big_count no rompa $big_cluster.
            s = v
            for tok in sorted(table, key=len, reverse=True):
                s = s.replace(tok, str(table[tok]))
            out.append(s)
        else:
            out.append(v)
    # Deduplicar preservando orden.
    seen, uniq = set(), []
    for v in out:
        k = repr(v)
        if k not in seen:
            seen.add(k)
            uniq.append(v)
    return uniq


def load(path: str | Path, target: ArmTarget) -> Workload:
    # encoding explícito: los workloads llevan acentos y el locale del sistema
    # no siempre es UTF-8 (en Windows es cp1252, y el TOML llega corrupto).
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))

    metrics = [
        Metric(
            name=m["name"],
            regex=m["regex"],
            goal=m.get("goal", "max"),
            unit=m.get("unit", ""),
            primary=m.get("primary", False),
            aggregate=m.get("aggregate", "last"),
        )
        for m in data.get("metric", [])
    ]
    if not metrics:
        raise ValueError(f"{path}: el workload no define ninguna [[metric]]")

    axes = []
    for a in data.get("axis", []):
        axes.append(
            Axis(
                name=a["name"],
                values=_expand_tokens(a["values"], target),
                kind=a.get("kind", "arg"),
                env=a.get("env"),
                requires=a.get("requires", []),
                labels=a.get("labels", {}),
                note=a.get("note", ""),
            )
        )

    b = data.get("bench", {})
    return Workload(
        name=data.get("name", Path(path).stem),
        description=data.get("description", ""),
        command=data["command"].strip(),
        metrics=metrics,
        axes=axes,
        warmup=b.get("warmup", 1),
        repeats=b.get("repeats", 7),
        timeout_s=b.get("timeout_s", 900),
        cwd=data.get("cwd", "."),
        env=data.get("env", {}),
        setup=data.get("setup", "").strip(),
        teardown=data.get("teardown", "").strip(),
        defaults=data.get("defaults", {}),
    )

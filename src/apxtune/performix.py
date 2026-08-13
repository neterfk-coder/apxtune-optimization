"""Integración con Arm Performix.

Performix (`apx`) es el toolkit de análisis de rendimiento de Arm para
Neoverse. Aquí se usa para capturar el *porqué* de cada mejora: no basta
con que suban los tokens/s, queremos ver el instruction mix moverse hacia
i8mm/SVE y los stalls de backend caer.

El módulo degrada con elegancia: si `apx` no está instalado o el PMU no
está expuesto en la VM, apxtune sigue funcionando y el reporte simplemente
omite la sección de microarquitectura.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Recetas estándar de Performix relevantes para inferencia.
RECIPES = {
    "hotspot": "Code Hotspot — dónde se va el tiempo, a nivel de función",
    "instruction-mix": "Instruction Mix — qué instrucciones ejecuta realmente el CPU",
    "memory-access": "Memory Access — jerarquía de caché y ancho de banda",
    "microarch": "Microarchitecture Explorer — desglose top-down de stalls",
}

CANDIDATE_PATHS = [
    "apx",
    "/opt/arm/performix/bin/apx",
    "/usr/local/bin/apx",
    str(Path.home() / ".local/share/performix/bin/apx"),
]


@dataclass
class Capture:
    recipe: str
    ok: bool
    raw: dict = field(default_factory=dict)
    path: str = ""
    error: str = ""


@dataclass
class Availability:
    apx: str | None
    pmu: bool
    perf: bool
    reason: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.apx) and self.pmu


def find_apx() -> str | None:
    for c in CANDIDATE_PATHS:
        p = shutil.which(c) if "/" not in c else (c if os.access(c, os.X_OK) else None)
        if p:
            return p
    return None


def pmu_available() -> tuple[bool, str]:
    """Comprueba si los contadores de hardware están expuestos.

    En muchas VMs virtualizadas el PMU está capado. Mejor detectarlo el
    primer día que descubrirlo la noche antes de entregar.
    """
    paranoid = Path("/proc/sys/kernel/perf_event_paranoid")
    if not paranoid.exists():
        return False, "el kernel no expone perf_event (¿contenedor sin CAP_PERFMON?)"
    try:
        level = int(paranoid.read_text().strip())
    except (OSError, ValueError):
        level = 2
    if not shutil.which("perf"):
        return False, "perf no está instalado (apt install linux-tools-$(uname -r))"
    try:
        p = subprocess.run(
            ["perf", "stat", "-e", "cycles,instructions", "--", "true"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"perf stat falló: {e}"
    out = p.stderr
    if "not supported" in out or "<not supported>" in out:
        return False, "los eventos de PMU no están virtualizados en esta instancia"
    if p.returncode != 0 and level > 2:
        return False, f"perf_event_paranoid={level}, hace falta bajarlo a 1 o menos"
    return True, ""


def availability() -> Availability:
    apx = find_apx()
    pmu, reason = pmu_available()
    return Availability(apx=apx, pmu=pmu, perf=bool(shutil.which("perf")), reason=reason)


def capture(
    recipe: str,
    command: str,
    outdir: str,
    target: str = "localhost",
    duration_s: int | None = None,
    env: dict | None = None,
    timeout: int = 1800,
) -> Capture:
    """Ejecuta una receta de Performix sobre un comando y devuelve el JSON.

    La firma sigue el CLI documentado de Performix 2026.1. Si tu versión
    difiere, ajusta ARGS abajo: es el único punto de acoplamiento.
    """
    apx = find_apx()
    if not apx:
        return Capture(recipe, False, error="apx no encontrado en el PATH")

    Path(outdir).mkdir(parents=True, exist_ok=True)
    out = str(Path(outdir) / f"{recipe}.json")

    args = [apx, "record", "--recipe", recipe, "--target", target, "--output", out]
    if duration_s:
        args += ["--duration", str(duration_s)]
    args += ["--", "bash", "-lc", command]

    full_env = dict(os.environ)
    full_env.update({k: str(v) for k, v in (env or {}).items()})

    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=full_env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Capture(recipe, False, error=str(e))

    if p.returncode != 0:
        return Capture(recipe, False, error=(p.stderr or p.stdout)[-800:])

    data = {}
    if Path(out).exists():
        try:
            data = json.loads(Path(out).read_text())
        except (OSError, json.JSONDecodeError) as e:
            return Capture(recipe, False, path=out, error=f"salida no parseable: {e}")

    return Capture(recipe, True, raw=data, path=out)


def instruction_mix(cap: Capture) -> dict[str, float]:
    """Normaliza el instruction mix a fracciones por clase.

    Performix puede reportar la taxonomía con nombres distintos según
    versión, así que se buscan varias claves y se agrupa en las cuatro
    categorías que importan para inferencia.
    """
    if not cap.ok:
        return {}
    buckets = {"integer": 0.0, "simd_fp": 0.0, "matmul_int8": 0.0, "load_store": 0.0, "branch": 0.0}
    flat = _flatten(cap.raw)
    for k, v in flat.items():
        if not isinstance(v, (int, float)):
            continue
        key = k.lower()
        if any(t in key for t in ("smmla", "i8mm", "usmmla", "bfmmla", "matmul")):
            buckets["matmul_int8"] += float(v)
        elif any(t in key for t in ("asimd", "simd", "sve", "vfp", "fp_")):
            buckets["simd_fp"] += float(v)
        elif any(t in key for t in ("ld_", "st_", "load", "store", "mem_access")):
            buckets["load_store"] += float(v)
        elif "br_" in key or "branch" in key:
            buckets["branch"] += float(v)
        elif "int" in key or "dp_" in key:
            buckets["integer"] += float(v)
    total = sum(buckets.values())
    return {k: (v / total * 100 if total else 0.0) for k, v in buckets.items()}


def topdown(cap: Capture) -> dict[str, float]:
    """Extrae el desglose top-down (frontend/backend/retiring/bad speculation)."""
    if not cap.ok:
        return {}
    flat = _flatten(cap.raw)
    wanted = {
        "retiring": ("retiring",),
        "frontend_bound": ("frontend_bound", "frontend"),
        "backend_bound": ("backend_bound", "backend"),
        "bad_speculation": ("bad_speculation", "badspec", "mispredict"),
    }
    out = {}
    for name, keys in wanted.items():
        for k, v in flat.items():
            if isinstance(v, (int, float)) and any(t in k.lower() for t in keys):
                out[name] = float(v)
                break
    return out


def _flatten(obj, prefix="") -> dict:
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out

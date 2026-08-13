"""Registro de perfiles: el artefacto reutilizable.

Cada corrida produce un perfil indexado por (núcleo, cores, features).
El valor no está en tu corrida — está en que la siguiente persona con un
Neoverse-V2 arranque desde tu óptimo en vez de desde el default.

Los perfiles se versionan en el repo bajo profiles/ y se envían por PR.
"""

from __future__ import annotations

import json
import platform
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1


def build(run, workload, metric_name: str, goal: str, extras: dict | None = None) -> dict:
    t = run.target
    base = run.baseline.value(metric_name)
    best = run.best.value(metric_name)
    b_est = run.baseline.estimates.get(metric_name)
    a_est = run.best.estimates.get(metric_name)

    return {
        "schema": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile_key": t.profile_key(),
        "target": {
            "part": t.part,
            "arch": t.arch,
            "physical_cores": t.physical_cores,
            "logical_cpus": t.logical_cpus,
            "numa_nodes": t.numa_nodes,
            "features": [f for f in t.features],
            "gemm_path": t.gemm_path(),
            "kernel": t.kernel,
            "distro": platform.platform(),
        },
        "workload": {
            "name": workload.name,
            "description": workload.description,
            "metric": metric_name,
            "goal": goal,
            "repeats": workload.repeats,
        },
        "baseline": {
            "config": _jsonable(run.baseline.config),
            "median": base,
            "ci95": [b_est.lo, b_est.hi] if b_est else None,
            "rsd_pct": b_est.rsd if b_est else None,
        },
        "tuned": {
            "config": _jsonable(run.best.config),
            "median": best,
            "ci95": [a_est.lo, a_est.hi] if a_est else None,
            "rsd_pct": a_est.rsd if a_est else None,
            "command": run.best.command,
        },
        "speedup": run.speedup(metric_name, goal),
        "validation": _validation(run, metric_name),
        "attribution": [
            {
                "axis": s.axis,
                "from": str(s.from_value),
                "to": str(s.to_value),
                "pct": round(s.comparison.pct, 2),
                "p_value": round(s.comparison.p_value, 5),
                "note": s.note,
            }
            for s in run.accepted
        ],
        "rejected": [
            {
                "axis": s.axis,
                "to": str(s.to_value),
                "pct": round(s.comparison.pct, 2),
                "reason": s.comparison.reason,
            }
            for s in run.steps
            if not s.accepted
        ],
        "trials": run.trials,
        "wall_seconds": round(run.wall_s, 1),
        **(extras or {}),
    }


def _validation(run, metric_name: str) -> dict | None:
    """Procedencia del speedup: de dónde salió exactamente el número.

    Quien reutilice este perfil tiene derecho a saber si la cifra viene de una
    medición entrelazada o de restar dos números tomados con horas de
    diferencia. Sin esta clave, ambas se ven igual.
    """
    if not getattr(run, "accepted", None):
        # No hay dos configuraciones que comparar: el óptimo es el baseline.
        # Marcarlo como "sin validar" sugeriría que el 1.0 es dudoso, y no lo
        # es — es exacto por definición.
        return {"method": "not_applicable", "interleaved": False, "ok": True,
                "note": "ningún cambio aceptado: el óptimo es el baseline, "
                        "speedup = 1.0 por definición y no hay nada que validar"}

    v = getattr(run, "validation", None)
    if v is None:
        return {"method": "search_medians", "interleaved": False, "ok": False,
                "note": "sin validación final; speedup = cociente de medianas de la búsqueda"}
    if not v.ok:
        return {"method": "search_medians", "interleaved": False, "ok": False,
                "error": v.error,
                "note": "la validación final falló; speedup = cociente de medianas"}

    b = v.base.estimates.get(metric_name)
    a = v.best.estimates.get(metric_name)
    return {
        "method": "interleaved_ab",
        "interleaved": True,
        "ok": True,
        "pairs": v.repeats,
        "baseline_median": v.base.value(metric_name),
        "baseline_ci95": [b.lo, b.hi] if b else None,
        "tuned_median": v.best.value(metric_name),
        "tuned_ci95": [a.lo, a.hi] if a else None,
        "speedup": v.speedup,
        "p_value": round(v.comparison.p_value, 5),
        "alpha": v.alpha,
        "significant": v.comparison.significant,
    }


def save(profile: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def load_registry(dirpath: str | Path) -> list[dict]:
    out = []
    for f in sorted(Path(dirpath).glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    return out


def match(registry: list[dict], target) -> dict | None:
    """Busca el perfil más parecido al hardware actual.

    Prioridad: coincidencia exacta de clave, luego mismo núcleo, luego
    mismo camino de GEMM (que es lo que de verdad determina el óptimo).
    """
    key = target.profile_key()
    for p in registry:
        if p.get("profile_key") == key:
            return p
    for p in registry:
        if p.get("target", {}).get("part") == target.part:
            return p
    for p in registry:
        if p.get("target", {}).get("gemm_path") == target.gemm_path():
            return p
    return None


def to_shell(profile: dict) -> str:
    """Convierte el perfil en algo pegable: el comando ya optimizado."""
    cmd = profile.get("tuned", {}).get("command", "")
    lines = [
        "#!/usr/bin/env bash",
        f"# apxtune — perfil {profile.get('profile_key')}",
        f"# {profile['workload']['name']}: {profile['speedup']:.2f}x sobre el default",
        f"# generado {profile.get('generated_utc')}",
        "set -euo pipefail",
        "",
        cmd,
        "",
    ]
    return "\n".join(lines)


def _jsonable(cfg: dict) -> dict:
    return {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v)) for k, v in cfg.items()}

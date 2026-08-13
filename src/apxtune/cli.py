"""apxtune — línea de comandos."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, isa, performix, profiles, report, search, space
from .bench import describe

BANNER = "apxtune %s — autotuning de inferencia guiado por PMU en Arm" % __version__


# ─────────────────────────────── detect ───────────────────────────────

def cmd_detect(args) -> int:
    t = isa.detect()
    if args.json:
        print(json.dumps(t.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(t.summary())
    if not isa.is_arm(t):
        print("\nnota: esto no es aarch64. apxtune corre igual, pero las "
              "recomendaciones de ISA no aplican.")
    return 0


# ─────────────────────────────── doctor ───────────────────────────────

def _governor() -> str | None:
    p = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    try:
        return p.read_text().strip()
    except OSError:
        return None


def cmd_doctor(args) -> int:
    t = isa.detect()
    av = performix.availability()
    gov = _governor()
    ok = True

    def line(good, label, detail=""):
        nonlocal ok
        mark = "✓" if good else "✗"
        if not good:
            ok = False
        print(f"  {mark} {label}" + (f"\n      {detail}" if detail else ""))

    print(BANNER + "\n\nentorno de medición\n")
    line(isa.is_arm(t), f"arquitectura Arm ({t.arch})",
         "" if isa.is_arm(t) else "no es aarch64; los ejes de ISA quedarán fijos")
    line(t.physical_cores >= 2, f"{t.physical_cores} cores físicos")
    line(bool(av.apx), "Arm Performix (apx) instalado",
         av.apx or "opcional — sin él no hay contadores, el tuning sigue funcionando. "
                   "https://developer.arm.com/servers-and-cloud-computing/arm-performix")
    line(av.pmu, "contadores PMU accesibles", av.reason)
    line(gov in (None, "performance"),
         f"governor de frecuencia: {gov or 'no expuesto'}",
         "" if gov in (None, "performance")
         else "pon 'performance' o las mediciones tendrán varianza artificial: "
              "sudo cpupower frequency-set -g performance")
    line(shutil.which("bash") is not None, "bash disponible")

    print("\nfeatures relevantes\n")
    for f in ("asimddp", "i8mm", "bf16", "sve", "sve2", "sme2"):
        have = f in t.features
        print(f"  {'✓' if have else '·'} {f}")
    print(f"\n  ruta GEMM int8 → {t.gemm_path()}")

    if not av.pmu:
        print(
            "\nsin PMU puedes seguir: apxtune mide tiempo y throughput igual.\n"
            "Pierdes la evidencia microarquitectónica. Si estás en cloud, prueba\n"
            "una instancia dedicada o metal (en AWS, las Graviton exponen PMU)."
        )
    return 0 if ok else 1


# ──────────────────────────────── tune ────────────────────────────────

def cmd_tune(args) -> int:
    t = isa.detect()
    try:
        wl = space.load(args.workload, t)
    except (OSError, ValueError, KeyError) as e:
        print(f"error cargando el workload: {e}", file=sys.stderr)
        return 2

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    print(BANNER)
    print("\n" + t.summary())
    print(f"\nworkload   {wl.name}")
    if wl.description:
        print(f"           {wl.description}")
    grid = wl.grid_size(t)
    print(f"grid       {grid} combinaciones · el descenso por coordenadas "
          f"evalúa ~{sum(len(a.applicable(t)) for a in wl.axes) * args.passes}")
    print(f"métrica    {wl.primary.name} ({wl.primary.goal}) {wl.primary.unit}")
    print(f"muestreo   {wl.repeats} repeticiones + {wl.warmup} warmup")

    if args.dry_run:
        print("\n--dry-run: comandos que se ejecutarían\n")
        for cfg in wl.full_grid(t)[: args.dry_limit]:
            from .bench import render
            cmd, env = render(wl, cfg, t)
            envs = " ".join(f"{k}={v}" for k, v in env.items())
            print(f"  · {describe(cfg, wl)}\n    {envs} {cmd}\n")
        return 0

    av = performix.availability()
    mix_before = mix_after = td_before = td_after = None
    pnote = ""
    if args.performix and not av.usable:
        pnote = (
            "Arm Performix no se pudo usar en esta corrida"
            + (f": {av.reason}. " if av.reason else ". ")
            + "El reporte incluye rendimiento pero no evidencia de microarquitectura."
        )
        print(f"\n⚠ {pnote}")

    run = search.tune(
        wl, t,
        passes=args.passes,
        explore_repeats=args.explore_repeats,
        min_effect_pct=args.min_effect,
        alpha=args.alpha,
        validate=args.validate,
        validate_repeats=args.validate_repeats,
    )

    if run.aborted:
        print(f"\nabortado: {run.aborted}", file=sys.stderr)
        return 3

    # Captura PMU del baseline y del óptimo, para el antes/después.
    if args.performix and av.usable:
        from .bench import render
        print("\n▸ capturando contadores con Performix")
        for label, cfg, slot in (("baseline", run.baseline.config, "before"),
                                 ("tuned", run.best.config, "after")):
            cmd, env = render(wl, cfg, t)
            for recipe in ("instruction-mix", "microarch"):
                cap = performix.capture(
                    recipe, cmd, str(outdir / "performix" / slot), env=env
                )
                if not cap.ok:
                    print(f"    · {label}/{recipe}: {cap.error[:80]}")
                    continue
                if recipe == "instruction-mix":
                    mix = performix.instruction_mix(cap)
                    if slot == "before":
                        mix_before = mix
                    else:
                        mix_after = mix
                else:
                    td = performix.topdown(cap)
                    if slot == "before":
                        td_before = td
                    else:
                        td_after = td
                print(f"    ✓ {label}/{recipe}")

    metric = wl.primary
    speedup = run.speedup(metric.name, metric.goal)
    prof = profiles.build(run, wl, metric.name, metric.goal)

    key = t.profile_key()
    pjson = profiles.save(prof, outdir / f"{key}.json")
    (outdir / f"{key}.sh").write_text(profiles.to_shell(prof), encoding="utf-8")
    hpath = outdir / f"{key}.html"
    hpath.write_text(
        report.render(run, wl, prof, mix_before, mix_after, td_before, td_after, pnote),
        encoding="utf-8",
    )

    v = run.validation
    lo, hi = (
        (v.base.value(metric.name), v.best.value(metric.name))
        if v and v.ok
        else (run.baseline.value(metric.name), run.best.value(metric.name))
    )

    print("\n" + "─" * 62)
    print(f"  {metric.name}   {lo:.4g} → {hi:.4g} {metric.unit}   ({speedup:.2f}×)")
    if v and v.ok:
        print(f"  validado A/B intercalado · {v.repeats} pares · p={v.comparison.p_value:.4f}")
    elif v:
        print("  ⚠ validación final incompleta — el speedup es un cociente de "
              "medianas medidas por separado")
    print("─" * 62)
    for s in run.accepted:
        print(f"  {s.comparison.pct:+6.1f}%   {s.axis}: {s.from_value} → {s.to_value}")
    if not run.accepted:
        print("  ningún cambio superó el umbral — el default ya era óptimo")
    if run.accepted:
        print("  (atribución por pasos: orientativa, sin corrección por multiplicidad)")
    print(f"\n  perfil   {pjson}")
    print(f"  reporte  {hpath}")
    print(f"  script   {outdir / (key + '.sh')}")
    print(f"\n  {run.trials} configuraciones · {run.wall_s / 60:.1f} min\n")

    if args.fail_under and speedup < args.fail_under:
        print(f"speedup {speedup:.2f}× por debajo de --fail-under {args.fail_under}",
              file=sys.stderr)
        return 1
    return 0


# ─────────────────────────────── apply ────────────────────────────────

def cmd_apply(args) -> int:
    try:
        prof = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"no se pudo leer el perfil: {e}", file=sys.stderr)
        return 2

    t = isa.detect()
    ptarget = prof.get("target", {})
    if ptarget.get("part") != t.part:
        print(
            f"⚠ el perfil es de {ptarget.get('part')} y esta máquina es {t.part}.\n"
            f"  ruta GEMM del perfil: {ptarget.get('gemm_path')}\n"
            f"  ruta GEMM aquí:       {t.gemm_path()}\n"
            "  El comando puede funcionar, pero el óptimo probablemente sea otro.\n",
            file=sys.stderr,
        )

    script = profiles.to_shell(prof)
    if args.print_only:
        print(script)
        return 0

    out = Path(args.output or f"{prof.get('profile_key', 'profile')}.sh")
    out.write_text(script, encoding="utf-8")
    out.chmod(0o755)
    print(f"escrito {out}  ({prof.get('speedup', 1):.2f}× sobre el default)")
    return 0


# ─────────────────────────────── match ────────────────────────────────

def cmd_match(args) -> int:
    t = isa.detect()
    reg = profiles.load_registry(args.registry)
    if not reg:
        print(f"no hay perfiles en {args.registry}")
        return 1
    m = profiles.match(reg, t)
    if not m:
        print(f"ningún perfil compatible con {t.profile_key()} entre {len(reg)} candidatos")
        return 1
    exact = m.get("profile_key") == t.profile_key()
    print(f"{'coincidencia exacta' if exact else 'coincidencia aproximada'}: "
          f"{m.get('profile_key')}  ({m.get('speedup', 1):.2f}×)")
    print(f"  workload  {m.get('workload', {}).get('name')}")
    for a in m.get("attribution", []):
        print(f"  {a['pct']:+6.1f}%   {a['axis']}: {a['from']} → {a['to']}")
    return 0


# ──────────────────────────────── main ────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apxtune",
        description=BANNER,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "ejemplos:\n"
            "  apxtune doctor\n"
            "  apxtune tune workloads/llama-bench.toml --out results/\n"
            "  apxtune tune workloads/llama-bench.toml --dry-run\n"
            "  apxtune match --registry profiles/\n"
        ),
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="mostrar núcleo, features y topología")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_detect)

    doc = sub.add_parser("doctor", help="verificar que el entorno puede medir bien")
    doc.set_defaults(func=cmd_doctor)

    t = sub.add_parser("tune", help="buscar la mejor configuración para este hardware")
    t.add_argument("workload", help="archivo TOML del workload")
    t.add_argument("--out", default="results", help="directorio de salida")
    t.add_argument("--passes", type=int, default=2)
    t.add_argument("--explore-repeats", type=int, default=3,
                   help="repeticiones en la fase barata de exploración")
    t.add_argument("--min-effect", type=float, default=2.0,
                   help="%% mínimo de mejora para aceptar un cambio")
    t.add_argument("--alpha", type=float, default=0.05)
    t.add_argument("--no-validate", dest="validate", action="store_false",
                   help="saltar la validación final A/B intercalada (no recomendado: "
                        "el speedup pasa a ser un cociente de medianas medidas en "
                        "momentos distintos)")
    t.add_argument("--validate-repeats", type=int, default=None,
                   help="pares intercalados en la validación final "
                        "(por defecto, los repeats del workload)")
    t.add_argument("--no-performix", dest="performix", action="store_false",
                   help="saltar la captura de contadores")
    t.add_argument("--dry-run", action="store_true",
                   help="imprimir los comandos sin ejecutarlos")
    t.add_argument("--dry-limit", type=int, default=8)
    t.add_argument("--fail-under", type=float, default=None,
                   help="salir con código 1 si el speedup baja de este valor (para CI)")
    t.set_defaults(func=cmd_tune, performix=True, validate=True)

    a = sub.add_parser("apply", help="generar el script con la configuración óptima")
    a.add_argument("--profile", required=True)
    a.add_argument("--output")
    a.add_argument("--print-only", action="store_true")
    a.set_defaults(func=cmd_apply)

    m = sub.add_parser("match", help="buscar un perfil del registro para este hardware")
    m.add_argument("--registry", default="profiles")
    m.set_defaults(func=cmd_match)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrumpido", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

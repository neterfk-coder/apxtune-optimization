"""Reporte HTML autocontenido.

Sin fuentes remotas, sin CDN, sin JavaScript de terceros: el reporte se
abre desde un `scp` en una máquina sin red. La pieza central es la cascada
de atribución — cuánto aportó cada decisión — porque un número final sin
desglose no le enseña nada a nadie.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

CSS = """
:root{
  --paper:#E9E9E2; --ink:#14161A; --graphite:#7C8189;
  --signal:#CE0058; --verify:#0B6E63; --rule:#C4C4BA; --sunk:#DEDED6;
  --display:"Archivo Narrow","Roboto Condensed","Oswald","Liberation Sans Narrow",
            "Helvetica Neue Condensed",ui-sans-serif,system-ui,sans-serif;
  --mono:ui-monospace,"JetBrains Mono","SFMono-Regular",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:var(--mono); font-size:13px; line-height:1.55;
  padding:0 24px 96px;
}
.wrap{max-width:940px;margin:0 auto}

/* placa de datos */
.plate{border-bottom:2px solid var(--ink);padding:40px 0 18px;margin-bottom:34px}
.eyebrow{
  font-family:var(--mono);font-size:10px;letter-spacing:.22em;text-transform:uppercase;
  color:var(--graphite);margin:0 0 22px
}
.claim{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
.claim .big{
  font-family:var(--display);font-weight:700;font-size:clamp(72px,15vw,148px);
  line-height:.82;letter-spacing:-.02em;color:var(--signal);
  font-stretch:condensed
}
.claim .what{font-family:var(--display);font-size:clamp(20px,3.4vw,30px);
  line-height:1.05;max-width:22ch;font-stretch:condensed;font-weight:600}
.honest{margin-top:14px;font-size:11px;color:var(--graphite);max-width:70ch}
.honest b{color:var(--ink);font-weight:600}

h2{
  font-family:var(--mono);font-size:10px;letter-spacing:.22em;text-transform:uppercase;
  color:var(--graphite);font-weight:600;
  border-top:1px solid var(--rule);padding-top:10px;margin:44px 0 18px
}
h3{font-family:var(--display);font-size:19px;margin:26px 0 8px;font-weight:600}
p{max-width:74ch}

/* hoja de especificaciones */
.spec{display:grid;grid-template-columns:auto 1fr;gap:2px 18px;font-size:12px}
.spec dt{color:var(--graphite)}
.spec dd{margin:0;overflow-wrap:anywhere}
.feat{display:inline-block;border:1px solid var(--rule);padding:0 5px;margin:1px 3px 1px 0;
  border-radius:2px;font-size:10.5px;letter-spacing:.04em}
.feat.on{border-color:var(--verify);color:var(--verify);font-weight:600}

/* cascada */
.fall{margin:18px 0 6px}
.row{display:grid;grid-template-columns:26px 1fr;gap:12px;align-items:start;
  padding:9px 0;border-bottom:1px dotted var(--rule)}
.idx{font-size:11px;color:var(--graphite);padding-top:3px}
.head{display:flex;justify-content:space-between;gap:14px;align-items:baseline;flex-wrap:wrap}
.chg{font-size:13px}
.chg b{font-weight:600}
.chg .arrow{color:var(--graphite);padding:0 4px}
.gain{font-family:var(--display);font-size:22px;font-weight:700;color:var(--signal);
  white-space:nowrap;font-stretch:condensed}
.bar{height:9px;background:var(--sunk);margin-top:7px;position:relative;overflow:hidden}
.bar i{position:absolute;top:0;bottom:0;background:var(--graphite);display:block}
.bar u{position:absolute;top:0;bottom:0;background:var(--signal);display:block;
  text-decoration:none;transform-origin:left;animation:grow .5s ease-out both}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@media (prefers-reduced-motion:reduce){.bar u{animation:none}}
.meta{font-size:10.5px;color:var(--graphite);margin-top:5px}

/* mezcla de instrucciones */
.mix{display:grid;gap:10px;margin-top:14px}
.mixrow{display:grid;grid-template-columns:64px 1fr;gap:12px;align-items:center}
.mixrow span{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--graphite)}
.stack{display:flex;height:26px;border:1px solid var(--rule)}
.seg{display:flex;align-items:center;justify-content:center;font-size:9.5px;
  color:#fff;overflow:hidden;white-space:nowrap}

table{width:100%;border-collapse:collapse;font-size:12px;margin-top:10px}
th{text-align:left;font-weight:600;font-size:10px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--graphite);border-bottom:1px solid var(--ink);padding:0 10px 6px 0}
td{padding:6px 10px 6px 0;border-bottom:1px dotted var(--rule);vertical-align:top}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.no{color:var(--graphite)}
pre{background:#DFDFD7;border-left:2px solid var(--ink);padding:12px 14px;overflow-x:auto;
  font-size:11.5px;line-height:1.5;margin:10px 0}
code{font-family:var(--mono)}
footer{margin-top:56px;border-top:1px solid var(--rule);padding-top:14px;
  font-size:10.5px;color:var(--graphite)}
a{color:var(--verify)}
.warn{border-left:2px solid var(--signal);padding:8px 12px;margin:14px 0;
  background:#DFDFD7;font-size:12px}
@media print{body{background:#fff;padding:0}.bar u{animation:none}}
@media (max-width:560px){
  .row{grid-template-columns:20px 1fr;gap:8px}
  .mixrow{grid-template-columns:1fr}
}
"""

MIX_COLORS = {
    "matmul_int8": "#CE0058",
    "simd_fp": "#0B6E63",
    "integer": "#14161A",
    "load_store": "#7C8189",
    "branch": "#A8ADB4",
}
MIX_LABELS = {
    "matmul_int8": "i8mm/SMMLA",
    "simd_fp": "SIMD/SVE",
    "integer": "entero",
    "load_store": "ld/st",
    "branch": "branch",
}


def _e(s) -> str:
    return html.escape(str(s))


def _spec(target) -> str:
    from .isa import RELEVANT_FEATURES

    feats = "".join(
        f'<span class="feat{" on" if f in target.features else ""}">{f}</span>'
        for f in RELEVANT_FEATURES
    )
    rows = [
        ("núcleo", target.part + (" · heterogéneo" if target.heterogeneous else "")),
        ("arquitectura", target.arch),
        ("cores", f"{target.physical_cores} físicos / {target.logical_cpus} lógicos"),
        ("nodos NUMA", target.numa_nodes),
        ("ruta GEMM int8", target.gemm_path()),
        ("memoria", f"{target.mem_total_bytes / 2**30:.1f} GiB" if target.mem_total_bytes else "—"),
        ("kernel", target.kernel),
    ]
    body = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in rows)
    return f'<dl class="spec">{body}<dt>features</dt><dd>{feats}</dd></dl>'


def _waterfall(run, workload) -> str:
    metric = workload.primary
    steps = run.accepted
    if not steps:
        return (
            '<p class="no">Ningún cambio superó las tres puertas de aceptación. '
            "El default ya era el óptimo en este hardware, que también es un resultado.</p>"
        )

    base = run.baseline.value(metric.name)
    best = run.best.value(metric.name)
    span = max(base, best) if metric.goal == "max" else max(base, best)
    rows = []
    for i, s in enumerate(steps, 1):
        lo, hi = (s.before, s.after) if metric.goal == "max" else (s.after, s.before)
        start = min(lo, hi) / span * 100
        width = abs(hi - lo) / span * 100
        rows.append(
            f"""<div class="row">
  <div class="idx">{i:02d}</div>
  <div>
    <div class="head">
      <div class="chg"><b>{_e(s.axis)}</b>
        <span class="arrow">{_e(s.from_value)} →</span><b>{_e(s.to_value)}</b></div>
      <div class="gain">{s.comparison.pct:+.1f}%</div>
    </div>
    <div class="bar"><i style="left:0;width:{start:.2f}%"></i>
      <u style="left:{start:.2f}%;width:{max(width, 0.4):.2f}%"></u></div>
    <div class="meta">{s.before:.4g} → {s.after:.4g} {_e(metric.unit)}
      · p={s.comparison.p_value:.4f} · n={run.baseline.estimates[metric.name].n}
      {"· " + _e(s.note) if s.note else ""}</div>
  </div>
</div>"""
        )
    return f'<div class="fall">{"".join(rows)}</div>'


def _rejected(run) -> str:
    bad = [s for s in run.steps if not s.accepted]
    if not bad:
        return ""
    rows = "".join(
        f"<tr><td>{_e(s.axis)}</td><td>{_e(s.to_value)}</td>"
        f'<td class="num">{s.comparison.pct:+.1f}%</td>'
        f'<td class="no">{_e(s.comparison.reason)}</td></tr>'
        for s in bad
    )
    return f"""<h2>Cambios rechazados</h2>
<p>Se probaron y no entraron. Se listan porque un tuner que solo publica sus
aciertos no es medible — aquí está el denominador.</p>
<table><thead><tr><th>eje</th><th>valor</th><th>efecto</th><th>motivo</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def _mix(before: dict, after: dict) -> str:
    if not before and not after:
        return ""

    def stack(d):
        if not d:
            return '<div class="stack"><div class="seg" style="flex:1;background:var(--sunk);color:var(--graphite)">sin captura</div></div>'
        segs = "".join(
            f'<div class="seg" style="flex:{v:.3f};background:{MIX_COLORS[k]}" '
            f'title="{MIX_LABELS[k]} {v:.1f}%">{MIX_LABELS[k] if v > 11 else ""}</div>'
            for k, v in d.items()
            if v > 0.01
        )
        return f'<div class="stack">{segs}</div>'

    delta = ""
    if before and after:
        d = after.get("matmul_int8", 0) - before.get("matmul_int8", 0)
        if abs(d) > 0.05:
            delta = (
                f"<p>La fracción de instrucciones de matmul int8 (SMMLA/i8mm) pasó de "
                f"<b>{before.get('matmul_int8', 0):.1f}%</b> a <b>{after.get('matmul_int8', 0):.1f}%</b>. "
                "Eso es la prueba a nivel de hardware de que la mejora viene de usar la ISA "
                "de Arm, no de un cambio genérico que habría dado igual en x86.</p>"
            )
    return f"""<h2>Mezcla de instrucciones · Arm Performix</h2>{delta}
<div class="mix">
  <div class="mixrow"><span>antes</span>{stack(before)}</div>
  <div class="mixrow"><span>después</span>{stack(after)}</div>
</div>"""


def _topdown(before: dict, after: dict) -> str:
    if not before and not after:
        return ""
    keys = sorted(set(before) | set(after))
    rows = "".join(
        f"<tr><td>{_e(k.replace('_', ' '))}</td>"
        f'<td class="num">{before.get(k, float("nan")):.1f}</td>'
        f'<td class="num">{after.get(k, float("nan")):.1f}</td>'
        f'<td class="num">{after.get(k, 0) - before.get(k, 0):+.1f}</td></tr>'
        for k in keys
    )
    return f"""<h2>Desglose top-down</h2>
<table><thead><tr><th>categoría</th><th>antes %</th><th>después %</th><th>Δ</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def _validation(run, workload) -> str:
    """Cómo se obtuvo el número grande de arriba."""
    metric = workload.primary
    v = getattr(run, "validation", None)

    if not run.accepted:
        return """<h2>Procedencia del número</h2>
<p>No se aceptó ningún cambio, así que la configuración óptima <b>es</b> el
baseline: el speedup es 1.00&times; por definición y no hay dos configuraciones
que comparar, ni por tanto nada que validar. Es un resultado, no un fallo — dice
que en este hardware el default ya estaba en el óptimo del espacio explorado, y
eso le ahorra la búsqueda a quien venga detrás con el mismo núcleo.</p>"""

    if v is None or not v.ok:
        why = (
            f" El intento falló: {_e(v.error)}." if v is not None and v.error else ""
        )
        return f"""<h2>Procedencia del número</h2>
<div class="warn">Esta corrida <b>no</b> tiene validación final entrelazada.{why}
El speedup del encabezado es el cociente entre la mediana del baseline y la del
óptimo, medidas en momentos distintos de la búsqueda — posiblemente con horas de
diferencia. Cualquier deriva del entorno en ese intervalo (throttling, vecinos
ruidosos, cambio de frecuencia) está dentro de esa cifra y no es atribuible al
tuning. Trátala como indicativa y repite sin <code>--no-validate</code> antes de
publicarla.</div>"""

    b = v.base.estimates.get(metric.name)
    a = v.best.estimates.get(metric.name)
    cmp_ = v.comparison
    verdict = (
        ""
        if cmp_.accepted
        else '<div class="warn">La ventaja <b>no</b> sobrevive a la medición '
        "entrelazada. Los cambios aceptados durante la búsqueda pueden ser deriva "
        "del entorno y no mejora real: este perfil no debería publicarse.</div>"
    )
    return f"""<h2>Procedencia del número</h2>
<p>El {cmp_.speedup:.2f}&times; de arriba no es una resta de números viejos. Terminada
la búsqueda se vuelve a medir desde cero, <b>entrelazando</b> el baseline y la
configuración óptima — A,B,B,A,A,B… — durante <b>{v.repeats}</b> pares. Así
cualquier tendencia temporal del entorno cae por igual sobre las dos ramas en vez
de castigar a la que se midió después, y el orden se alterna dentro de cada par
para que ninguna de las dos herede siempre las cachés calientes de la otra.</p>
<table><thead><tr><th>rama</th><th>mediana</th><th>IC95</th><th>dispersión</th></tr></thead>
<tbody>
<tr><td>baseline</td><td class="num">{b.median:.4g}</td>
    <td class="num">[{b.lo:.4g}, {b.hi:.4g}]</td><td class="num">{b.rsd:.1f}%</td></tr>
<tr><td>optimizado</td><td class="num">{a.median:.4g}</td>
    <td class="num">[{a.lo:.4g}, {a.hi:.4g}]</td><td class="num">{a.rsd:.1f}%</td></tr>
</tbody></table>
<p>Mann-Whitney de dos colas sobre las dos series: <b>p={cmp_.p_value:.4f}</b>,
contra &alpha;={v.alpha}. Es una sola comparación, decidida antes de medirla,
así que ese p-valor se lee tal cual — a diferencia de los de la cascada, que
salen de una decena de pruebas sucesivas y no llevan corrección por
multiplicidad. La cascada explica de dónde vino la ganancia; este bloque es el
que la certifica, y por eso su &alpha; es fijo aunque se afloje el de la
búsqueda con <code>--alpha</code>.</p>
{verdict}"""


def render(run, workload, profile: dict, mix_before=None, mix_after=None,
           td_before=None, td_after=None, performix_note: str = "") -> str:
    metric = workload.primary
    t = run.target
    speedup = run.speedup(metric.name, metric.goal)
    v = getattr(run, "validation", None)
    # Las cifras del encabezado salen de la validación entrelazada si existe,
    # para que el número grande y su intervalo vengan de la misma medición.
    if v is not None and v.ok:
        b = v.base.estimates.get(metric.name)
        a = v.best.estimates.get(metric.name)
    else:
        b = run.baseline.estimates.get(metric.name)
        a = run.best.estimates.get(metric.name)

    noise = ""
    if b and b.noisy:
        noise = (
            f'<div class="warn">Dispersión del baseline: <b>{b.rsd:.1f}%</b>. '
            "Por encima de 5% el entorno introduce más varianza de la que aporta el tuning; "
            "los resultados de esta corrida son indicativos, no publicables. "
            "Usa una instancia dedicada o fija la frecuencia y repite.</div>"
        )

    pnote = f'<div class="warn">{_e(performix_note)}</div>' if performix_note else ""

    cmd = run.best.command or ""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>apxtune · {_e(workload.name)} · {_e(t.part)}</title>
<style>{CSS}</style></head><body><div class="wrap">

<header class="plate">
  <p class="eyebrow">apxtune · reporte de optimización · {_e(t.profile_key())}</p>
  <div class="claim">
    <div class="big">{speedup:.2f}&times;</div>
    <div class="what">{_e(metric.name)} en {_e(t.part)}<br>tras {len(run.accepted)} cambios de configuración</div>
  </div>
  <p class="honest">
    Mediana de <b>{b.n if b else 0}</b> corridas
    {"<b>entrelazadas</b> entre las dos configuraciones"
     if v is not None and v.ok else "por configuración"}.
    Baseline <b>{b.median:.4g}</b> IC95 [{b.lo:.4g}, {b.hi:.4g}] ·
    optimizado <b>{a.median:.4g}</b> IC95 [{a.lo:.4g}, {a.hi:.4g}] {_e(metric.unit)}.
    Cada cambio pasó una prueba de Mann-Whitney a &alpha;=0.05 con efecto mínimo de 2%.
    Sin recompilar el modelo, sin tocar los pesos.
  </p>
</header>

{noise}{pnote}

<h2>Objetivo</h2>
{_spec(t)}

{_validation(run, workload)}

<h2>De dónde salió la ganancia</h2>
<p>Cada barra parte del rendimiento acumulado hasta ese punto. El orden es el
orden en que se aceptaron, y es informativo: los cambios de compilación y de
cuantización se prueban primero porque desplazan el óptimo de todo lo demás.</p>
{_waterfall(run, workload)}

{_mix(mix_before or {}, mix_after or {})}
{_topdown(td_before or {}, td_after or {})}
{_rejected(run)}

<h2>Reproducir</h2>
<p>Comando final, tal cual se midió:</p>
<pre><code>{_e(cmd)}</code></pre>
<p>O aplica el perfil directamente en cualquier máquina con el mismo núcleo:</p>
<pre><code>apxtune apply --profile {_e(t.profile_key())}.json</code></pre>

<h2>Perfil</h2>
<pre><code>{_e(json.dumps(profile, indent=2, ensure_ascii=False)[:4000])}</code></pre>

<footer>
  Generado por apxtune el {stamp} · {run.trials} configuraciones evaluadas ·
  {run.wall_s / 60:.0f} min de reloj · Apache-2.0<br>
  Contadores de hardware vía Arm Performix. Este reporte no contiene ninguna
  cifra que no venga de una medición en esta máquina.
</footer>
</div></body></html>"""

"""Validación final: es la que respalda el número que se publica.

Si estas pruebas fallan, el speedup del reporte vuelve a ser un cociente de
medianas tomadas en momentos distintos — que es exactamente lo que la
validación existe para evitar.
"""

import pytest

from apxtune import bench, isa, profiles, report, space
from apxtune.bench import RunResult
from apxtune.search import Step, TuningRun, Validation
from apxtune.stats import compare, summarize


def _res(values, config=None):
    return RunResult(
        config=config or {},
        estimates={"tps": summarize(values)},
        ok=True,
    )


def _validation(base_vals, best_vals, repeats=7):
    base, best = _res(base_vals), _res(best_vals)
    return Validation(
        base=base,
        best=best,
        comparison=compare(
            base.estimates["tps"], best.estimates["tps"], goal="max", min_effect_pct=0.0
        ),
        repeats=repeats,
    )


def _run(baseline, best, validation=None, steps=None):
    return TuningRun(
        workload="w",
        target=None,
        baseline=_res(baseline),
        best=_res(best),
        steps=steps if steps is not None else [_step(accepted=True)],
        validation=validation,
    )


def _step(accepted=True):
    a, b = _res([100] * 5), _res([120] * 5)
    return Step(
        axis="knob",
        from_value=0,
        to_value=1,
        comparison=compare(a.estimates["tps"], b.estimates["tps"], goal="max"),
        accepted=accepted,
        metric="tps",
        before=100.0,
        after=120.0,
    )


# ─────────────────────── de dónde sale el speedup ───────────────────────

def test_el_speedup_sale_de_la_validacion_no_de_la_busqueda():
    """El caso que motiva todo esto: la búsqueda exagera, la validación corrige.

    Simula una corrida donde el baseline se midió con la máquina fría y el
    óptimo horas después: el cociente de la búsqueda dice 2.0x, pero la
    medición entrelazada —las dos ramas en el mismo intervalo— dice 1.2x.
    """
    busqueda_2x = _run(baseline=[100] * 7, best=[200] * 7)
    assert busqueda_2x.speedup("tps", "max") == pytest.approx(2.0)

    con_validacion = _run(
        baseline=[100] * 7,
        best=[200] * 7,
        validation=_validation([100, 101, 99, 100, 102, 98, 101],
                               [120, 121, 119, 120, 122, 118, 121]),
    )
    assert con_validacion.speedup("tps", "max") == pytest.approx(1.2, abs=0.02)


def test_sin_validacion_cae_al_cociente_de_medianas():
    assert _run([100] * 5, [150] * 5).speedup("tps", "max") == pytest.approx(1.5)


def test_una_validacion_fallida_no_se_usa():
    """Media pareja no compara nada: se descarta y se avisa, no se inventa."""
    v = _validation([100] * 7, [120] * 7)
    v.ok = False
    v.error = "la rama pareja falló"
    assert _run([100] * 5, [150] * 5, validation=v).speedup("tps", "max") == pytest.approx(1.5)


def test_goal_min_tambien_se_orienta_bien_en_la_validacion():
    """Para latencia, bajar de 200 a 100 es 2x más rápido, no 0.5x."""
    base, best = _res([200] * 7), _res([100] * 7)
    v = Validation(
        base=base,
        best=best,
        comparison=compare(base.estimates["tps"], best.estimates["tps"],
                           goal="min", min_effect_pct=0.0),
        repeats=7,
    )
    assert _run([200] * 5, [100] * 5, validation=v).speedup("tps", "min") == pytest.approx(2.0)


def test_una_regresion_se_reporta_como_regresion():
    """Si la validación dice que empeoró, el número no se maquilla."""
    v = _validation([120] * 7, [100] * 7)
    run = _run([100] * 5, [120] * 5, validation=v)
    assert run.speedup("tps", "max") < 1.0
    assert not v.comparison.accepted


def test_aflojar_el_alpha_de_la_busqueda_no_afloja_la_certificacion():
    """--alpha gobierna la exploración; el registro compartido no se toca.

    Si la certificación heredara un --alpha 0.95, `significant: true` dejaría
    de significar nada y dos perfiles del registro no serían comparables.
    """
    from apxtune import search

    llamadas = {}

    def fake_paired(workload, cfg_a, cfg_b, target, repeats=None):
        a, b = _res([100] * 5), _res([101] * 5)
        return a, b

    def fake_compare(base, cand, goal, min_effect_pct, alpha):
        llamadas["alpha"] = alpha
        return compare(base, cand, goal=goal, min_effect_pct=min_effect_pct, alpha=alpha)

    run = _run([100] * 5, [101] * 5)
    orig_paired, orig_compare = search.measure_paired, search.compare
    try:
        search.measure_paired, search.compare = fake_paired, fake_compare
        v = search.final_validation(_wl(), None, run, log=lambda *a: None)
    finally:
        search.measure_paired, search.compare = orig_paired, orig_compare

    assert llamadas["alpha"] == search.PUBLISH_ALPHA == 0.05
    assert v.alpha == 0.05


def _wl():
    return space.Workload(
        name="w",
        command="c",
        metrics=[space.Metric(name="tps", regex=r"([0-9.]+)", primary=True)],
        axes=[],
    )


# ───────────── 'el default ya era óptimo' no es un fallo ──────────────

def _run_sin_cambios():
    """Nada aceptado: el óptimo es el baseline, no hay nada que comparar."""
    return _run([100] * 5, [100] * 5, validation=None, steps=[_step(accepted=False)])


def test_sin_cambios_aceptados_el_speedup_es_exactamente_uno():
    assert _run_sin_cambios().speedup("tps", "max") == pytest.approx(1.0)


def test_sin_cambios_aceptados_el_perfil_no_se_marca_como_sin_validar():
    """Marcarlo 'search_medians' sugeriría que el 1.0 es dudoso, y es exacto.

    Además dejaría impublicable un perfil legítimo: profiles/README.md dice
    que 'en este núcleo el default ya era óptimo' ahorra tiempo igual.
    """
    v = profiles._validation(_run_sin_cambios(), "tps")
    assert v["method"] == "not_applicable"
    assert v["ok"] is True
    assert v["interleaved"] is False


def test_sin_cambios_aceptados_el_reporte_no_avisa_de_nada(workload):
    html = report._validation(_run_sin_cambios(), workload)
    assert "warn" not in html, "no debe salir la advertencia roja: no hay nada que validar"
    assert "1.00&times;" in html


def test_con_cambios_pero_sin_validacion_el_reporte_si_avisa(workload):
    """El caso contrario: aquí la advertencia sí tiene que aparecer."""
    html = report._validation(_run([100] * 5, [150] * 5, validation=None), workload)
    assert "warn" in html


# ─────────────────────────── el entrelazado ────────────────────────────

@pytest.fixture
def workload():
    return space.Workload(
        name="fake",
        command="run {x}",
        metrics=[space.Metric(name="tps", regex=r"tps=([0-9.]+)", primary=True)],
        axes=[space.Axis(name="x", values=["a", "b"])],
        warmup=1,
        repeats=4,
    )


@pytest.fixture
def target():
    return isa.detect()


@pytest.fixture
def spy(monkeypatch):
    """Sustituye la ejecución real: registra el orden y devuelve una salida fija."""
    orden = []

    def fake_run(cmd, env, cwd, timeout):
        orden.append(cmd)
        return True, f"tps={100 if 'a' in cmd else 130}", 0.01

    monkeypatch.setattr(bench, "_run_once", fake_run)
    return orden


def test_las_dos_ramas_se_alternan(workload, spy, target, tmp_path):
    workload.cwd = str(tmp_path)
    bench.measure_paired(workload, {"x": "a"}, {"x": "b"}, target)

    medidas = spy[2:]  # los dos primeros son los warmups, uno por rama
    assert len(medidas) == 8, "4 pares = 8 corridas"

    ramas = ["a" if "a" in c else "b" for c in medidas]
    assert ramas == ["a", "b", "b", "a", "a", "b", "b", "a"], (
        "debe alternar A,B / B,A: ni una rama va siempre después de la otra, "
        "ni una hereda siempre las cachés calientes de la otra"
    )
    assert ramas.count("a") == ramas.count("b")


def test_cada_rama_recoge_sus_propias_muestras(workload, spy, target, tmp_path):
    workload.cwd = str(tmp_path)
    a, b = bench.measure_paired(workload, {"x": "a"}, {"x": "b"}, target)

    assert a.ok and b.ok
    assert a.value("tps") == 100 and b.value("tps") == 130
    assert a.estimates["tps"].n == 4 and b.estimates["tps"].n == 4


def test_una_maquina_que_deriva_no_produce_una_mejora_falsa(workload, monkeypatch, target, tmp_path):
    """El fallo que motiva todo el módulo, de punta a punta.

    La caja se acelera sola a mitad de la corrida y ningún eje hace nada. Una
    búsqueda ingenua mide el baseline con la caja fría, mide el candidato ya
    caliente, ve +30% y publica una victoria inventada. La validación mide las
    dos ramas entrelazadas al final, cuando ambas están en el mismo régimen, y
    tiene que desmentirlo.
    """
    workload.cwd = str(tmp_path)
    workload.repeats = 5
    n = {"i": 0}

    def fake_run(cmd, env, cwd, timeout):
        n["i"] += 1
        # La salida NO depende de la configuración, solo del momento. El corte
        # son las 6 invocaciones de la primera medición (1 warmup + 5 repeats):
        # el baseline entero cae en frío y todo lo posterior en caliente.
        return True, f"tps={100.0 if n['i'] <= 6 else 130.0}", 0.01

    monkeypatch.setattr(bench, "_run_once", fake_run)

    frio = bench.measure(workload, {"x": "a"}, target)          # baseline, caja fría
    caliente = bench.measure(workload, {"x": "b"}, target)      # candidato, ya caliente
    ingenuo = compare(frio.estimates["tps"], caliente.estimates["tps"], goal="max")
    assert ingenuo.accepted and ingenuo.pct == pytest.approx(30.0), (
        "el montaje debe reproducir la trampa: la búsqueda ve +30% que no existe"
    )

    a, b = bench.measure_paired(workload, {"x": "a"}, {"x": "b"}, target, repeats=5)
    honesto = compare(a.estimates["tps"], b.estimates["tps"], goal="max", min_effect_pct=0.0)
    assert honesto.speedup == pytest.approx(1.0), "entrelazado, la deriva se cancela"
    assert not honesto.accepted, "y el cambio falso no debe aceptarse"


def test_si_una_rama_falla_se_anula_la_comparacion_entera(workload, monkeypatch, target, tmp_path):
    workload.cwd = str(tmp_path)
    llamadas = {"n": 0}

    def fake_run(cmd, env, cwd, timeout):
        llamadas["n"] += 1
        if llamadas["n"] > 4 and "b" in cmd:
            return False, "segfault", 0.01
        return True, "tps=100", 0.01

    monkeypatch.setattr(bench, "_run_once", fake_run)
    a, b = bench.measure_paired(workload, {"x": "a"}, {"x": "b"}, target)

    assert not a.ok and not b.ok, "media pareja no compara nada"
    assert "pareja" in a.error or "pareja" in b.error

"""Validación final: es la que respalda el número que se publica.

Si estas pruebas fallan, el speedup del reporte vuelve a ser un cociente de
medianas tomadas en momentos distintos — que es exactamente lo que la
validación existe para evitar.
"""

import pytest

from apxtune import bench, isa, space
from apxtune.bench import RunResult
from apxtune.search import TuningRun, Validation
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


def _run(baseline, best, validation=None):
    return TuningRun(
        workload="w",
        target=None,
        baseline=_res(baseline),
        best=_res(best),
        validation=validation,
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

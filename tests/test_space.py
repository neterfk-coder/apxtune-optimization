"""Carga del workload y expansión de tokens dependientes del hardware."""

from pathlib import Path

import pytest

from apxtune import isa, space

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def target():
    return isa.detect()


def test_carga_el_demo(target):
    wl = space.load(ROOT / "workloads/demo-synthetic.toml", target)
    assert wl.primary.name == "decode_tps"
    assert wl.primary.goal == "max"
    assert {a.name for a in wl.axes} == {"vector", "threads", "block"}


def test_carga_el_workload_de_llama(target):
    wl = space.load(ROOT / "workloads/llama-bench.toml", target)
    assert any(a.kind == "prelude" for a in wl.axes)
    assert any(a.kind == "env" for a in wl.axes)


def test_core_sweep_se_expande_al_hardware(target):
    wl = space.load(ROOT / "workloads/demo-synthetic.toml", target)
    threads = wl.axis("threads")
    assert threads is not None
    assert all(isinstance(v, int) and v >= 1 for v in threads.values)
    assert max(threads.values) == target.physical_cores


def test_un_eje_que_requiere_i8mm_se_bloquea_sin_i8mm(target):
    ax = space.Axis(name="q", values=["a", "b"], requires=["i8mm"])
    aplicables = ax.applicable(target)
    if target.has("i8mm"):
        assert aplicables == ["a", "b"]
    else:
        assert aplicables == ["a"], "sin i8mm solo debe quedar el valor seguro"


def test_extraccion_de_metrica():
    m = space.Metric(name="tg", regex=r"tg128\s*\|\s*([0-9.]+)")
    assert m.extract("| synthetic | 4 | tg128 | 12.5 ± 0.1 |") == 12.5
    assert m.extract("nada que ver") is None


def test_metrica_ausente_no_revienta():
    m = space.Metric(name="x", regex=r"zzz([0-9]+)")
    assert m.extract("") is None


def test_tokens_embebidos_en_cadenas(target):
    """'0-$logical_cpus' debe expandirse; era un bug real."""
    vals = space._expand_tokens(["$all_cpus", "taskset:$big_cluster", "$big_count"], target)
    assert all("$" not in str(v) for v in vals), vals


def test_all_cpus_es_un_rango_valido(target):
    v = space._expand_tokens(["$all_cpus"], target)[0]
    n = target.logical_cpus
    assert v == ("0" if n == 1 else f"0-{n - 1}")

"""Las puertas estadísticas son el corazón del proyecto: se prueban."""

import random

from apxtune.stats import bootstrap_ci, compare, mann_whitney_u, median, summarize


def test_median_par_e_impar():
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 3, 2]) == 2.5


def test_ic_contiene_la_mediana():
    xs = [10, 10.2, 9.8, 10.1, 9.9, 10.3, 9.7]
    lo, hi = bootstrap_ci(xs)
    assert lo <= median(xs) <= hi


def test_ic_se_estrecha_con_mas_muestras():
    rng = random.Random(7)
    pocos = [rng.gauss(100, 5) for _ in range(5)]
    muchos = [rng.gauss(100, 5) for _ in range(200)]
    a = bootstrap_ci(pocos)
    b = bootstrap_ci(muchos)
    assert (b[1] - b[0]) < (a[1] - a[0])


def test_mann_whitney_detecta_diferencia_real():
    a = [100, 101, 99, 100, 102, 98, 101]
    b = [130, 131, 129, 132, 130, 128, 131]
    assert mann_whitney_u(a, b) < 0.01


def test_tasa_de_falsos_positivos_calibrada():
    """La prueba debe equivocarse ~5% de las veces con alpha=0.05, no más.

    Una sola semilla no prueba nada: dos muestras de la misma distribución
    a veces se separan de verdad. Lo que importa es la tasa a largo plazo,
    porque de ella depende que apxtune no coleccione ruido y lo llame
    optimización.
    """
    rng = random.Random(0)
    trials, hits = 2000, 0
    for _ in range(trials):
        a = [rng.gauss(100, 3) for _ in range(12)]
        b = [rng.gauss(100, 3) for _ in range(12)]
        if mann_whitney_u(a, b) < 0.05:
            hits += 1
    assert 0.03 <= hits / trials <= 0.07, f"tasa={hits / trials:.3f}, debería rondar 0.05"


def test_potencia_frente_a_un_efecto_real():
    """Con un efecto de 10% y n=12 debe detectarlo casi siempre."""
    rng = random.Random(1)
    trials, hits = 300, 0
    for _ in range(trials):
        a = [rng.gauss(100, 3) for _ in range(12)]
        b = [rng.gauss(110, 3) for _ in range(12)]
        if mann_whitney_u(a, b) < 0.05:
            hits += 1
    assert hits / trials > 0.95


def test_rechaza_mejora_pequena_aunque_sea_significativa():
    """Una mejora de 1% real sigue sin valer la complejidad que introduce."""
    base = summarize([100.0, 100.1, 99.9, 100.0, 100.05, 99.95, 100.02])
    cand = summarize([101.0, 101.1, 100.9, 101.0, 101.05, 100.95, 101.02])
    c = compare(base, cand, goal="max", min_effect_pct=2.0)
    assert not c.accepted
    assert "umbral" in c.reason


def test_rechaza_mejora_grande_pero_ruidosa():
    base = summarize([100, 60, 140, 90, 110, 70, 130])
    cand = summarize([115, 65, 150, 95, 120, 75, 140])
    c = compare(base, cand, goal="max")
    assert not c.accepted


def test_acepta_mejora_grande_y_limpia():
    base = summarize([100, 100.5, 99.5, 100.2, 99.8, 100.1, 99.9])
    cand = summarize([150, 150.5, 149.5, 150.2, 149.8, 150.1, 149.9])
    c = compare(base, cand, goal="max")
    assert c.accepted
    assert 49 < c.pct < 51


def test_goal_min_invierte_la_direccion():
    """Para latencia, menos es mejor."""
    base = summarize([200, 201, 199, 200, 202, 198, 201])
    cand = summarize([100, 101, 99, 100, 102, 98, 101])
    c = compare(base, cand, goal="min")
    assert c.accepted
    assert c.speedup > 1.9


def test_marca_entorno_ruidoso():
    limpio = summarize([100, 100.2, 99.8, 100.1, 99.9])
    sucio = summarize([100, 140, 60, 120, 80])
    assert not limpio.noisy
    assert sucio.noisy

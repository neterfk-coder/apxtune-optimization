"""Detección de hardware. Debe funcionar (degradando) también fuera de Arm."""

from apxtune import isa


def test_detect_no_lanza():
    t = isa.detect()
    assert t.logical_cpus >= 1
    assert t.physical_cores >= 1


def test_profile_key_es_estable():
    t = isa.detect()
    assert t.profile_key() == t.profile_key()
    assert str(t.physical_cores) in t.profile_key()


def test_summary_es_texto():
    assert "cores" in isa.detect().summary()


def test_compactado_de_rangos():
    assert isa._compact_ranges([0, 1, 2, 3]) == "0-3"
    assert isa._compact_ranges([0, 1, 2, 8, 9]) == "0-2,8-9"
    assert isa._compact_ranges([5]) == "5"
    assert isa._compact_ranges([]) == ""


def test_has_requiere_todas_las_features():
    t = isa.ArmTarget(arch="aarch64", features=["i8mm", "bf16"])
    assert t.has("i8mm")
    assert t.has("i8mm", "bf16")
    assert not t.has("i8mm", "sve2")


def test_gemm_path_prioriza_lo_mejor():
    assert "SME2" in isa.ArmTarget(arch="aarch64", features=["sme2", "i8mm", "asimddp"]).gemm_path()
    assert "SMMLA" in isa.ArmTarget(arch="aarch64", features=["i8mm", "asimddp"]).gemm_path()
    assert "SDOT" in isa.ArmTarget(arch="aarch64", features=["asimddp"]).gemm_path()

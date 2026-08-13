"""Detección de arquitectura, features de ISA y topología en sistemas Arm.

Todo se lee de /proc y /sys. Sin dependencias externas, sin sudo.
"""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

# MIDR_EL1: implementer (bits 31:24) y part number (bits 15:4).
IMPLEMENTERS = {
    0x41: "Arm",
    0x42: "Broadcom",
    0x43: "Cavium",
    0x48: "HiSilicon",
    0x4E: "NVIDIA",
    0x50: "APM",
    0x51: "Qualcomm",
    0x61: "Apple",
    0xC0: "Ampere",
}

# Núcleos relevantes para inferencia. El nombre importa porque el perfil
# de tuning se guarda e indexa por él.
PARTS = {
    (0x41, 0xD0C): "Neoverse-N1",
    (0x41, 0xD40): "Neoverse-V1",
    (0x41, 0xD49): "Neoverse-N2",
    (0x41, 0xD4F): "Neoverse-V2",
    (0x41, 0xD8E): "Neoverse-N3",
    (0x41, 0xD84): "Neoverse-V3",
    (0x41, 0xD4A): "Neoverse-E1",
    (0x41, 0xD0B): "Cortex-A76",
    (0x41, 0xD41): "Cortex-A78",
    (0x41, 0xD44): "Cortex-X1",
    (0x41, 0xD47): "Cortex-A710",
    (0x41, 0xD48): "Cortex-X2",
    (0x41, 0xD4D): "Cortex-A715",
    (0x41, 0xD4E): "Cortex-X3",
    (0x41, 0xD81): "Cortex-A720",
    (0x41, 0xD82): "Cortex-X4",
    (0x41, 0xD87): "Cortex-A725",
    (0x41, 0xD85): "Cortex-X925",
    (0x41, 0xD03): "Cortex-A53",
    (0x41, 0xD08): "Cortex-A72",
    (0x41, 0xD05): "Cortex-A55",
    (0x41, 0xD46): "Cortex-A510",
    (0xC0, 0xAC3): "AmpereOne",
    (0x4E, 0x004): "NVIDIA-Grace",
}

# Features que cambian qué kernels de GEMM se pueden usar.
# El orden es el que se muestra en el reporte.
RELEVANT_FEATURES = [
    "asimd",      # NEON base
    "asimddp",    # SDOT/UDOT - int8 dot product (Armv8.2)
    "i8mm",       # SMMLA - int8 matmul (Armv8.6). Clave para prefill.
    "bf16",       # BFDOT/BFMMLA
    "sve",
    "sve2",
    "svei8mm",
    "svebf16",
    "sme",
    "sme2",
    "fphp",
    "asimdhp",
    "atomics",
    "lse128",
    "rcpc3",
]


@dataclass
class CoreCluster:
    """Un grupo de CPUs lógicas que comparten part number y frecuencia máxima."""

    part: str
    cpus: list[int]
    max_khz: int | None = None

    @property
    def count(self) -> int:
        return len(self.cpus)

    @property
    def cpuset(self) -> str:
        """Rango compacto apto para taskset -c, p.ej. '0-3,8-11'."""
        return _compact_ranges(self.cpus)


@dataclass
class ArmTarget:
    arch: str
    features: list[str] = field(default_factory=list)
    clusters: list[CoreCluster] = field(default_factory=list)
    logical_cpus: int = 0
    physical_cores: int = 0
    threads_per_core: int = 1
    numa_nodes: int = 1
    l3_bytes: int | None = None
    mem_total_bytes: int | None = None
    kernel: str = ""
    heterogeneous: bool = False

    def has(self, *names: str) -> bool:
        return all(n in self.features for n in names)

    @property
    def part(self) -> str:
        """Núcleo dominante: el cluster con más CPUs."""
        if not self.clusters:
            return "unknown"
        return max(self.clusters, key=lambda c: c.count).part

    @property
    def big_cluster(self) -> CoreCluster | None:
        """El cluster de mayor rendimiento (frecuencia máxima más alta)."""
        if not self.clusters:
            return None
        return max(self.clusters, key=lambda c: (c.max_khz or 0, c.count))

    def profile_key(self) -> str:
        """Identificador estable para indexar perfiles guardados."""
        tag = "+".join(f for f in ("i8mm", "sve2", "bf16", "sme2") if f in self.features)
        return f"{self.part}.{self.physical_cores}c" + (f".{tag}" if tag else "")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["part"] = self.part
        d["profile_key"] = self.profile_key()
        return d

    def summary(self) -> str:
        gemm = self.gemm_path()
        lines = [
            f"arch            {self.arch}",
            f"núcleo          {self.part}"
            + ("  (heterogéneo)" if self.heterogeneous else ""),
            f"cores           {self.physical_cores} físicos / {self.logical_cpus} lógicos"
            + (f" / {self.numa_nodes} nodos NUMA" if self.numa_nodes > 1 else ""),
            f"ruta GEMM int8  {gemm}",
            f"features        {' '.join(f for f in RELEVANT_FEATURES if f in self.features) or '—'}",
        ]
        if self.mem_total_bytes:
            lines.append(f"memoria         {self.mem_total_bytes / 2**30:.1f} GiB")
        for c in self.clusters:
            mhz = f" @ {c.max_khz / 1000:.0f} MHz" if c.max_khz else ""
            lines.append(f"cluster         {c.part} x{c.count} [{c.cpuset}]{mhz}")
        return "\n".join(lines)

    def gemm_path(self) -> str:
        """Mejor camino de multiplicación de matrices int8 disponible.

        Esto es lo que determina si vale la pena Q4_0 (que se re-empaqueta a
        kernels i8mm/SVE) frente a las K-quants.
        """
        if self.has("sme2"):
            return "SME2 (outer product)"
        if self.has("svei8mm"):
            return "SVE i8mm (SMMLA vectorial)"
        if self.has("i8mm"):
            return "NEON i8mm (SMMLA)"
        if self.has("asimddp"):
            return "NEON dotprod (SDOT)"
        if self.arch not in ("aarch64", "arm64"):
            return f"n/a — {self.arch} no es Arm"
        return "NEON escalar (sin int8 acelerado)"


def _compact_ranges(nums: list[int]) -> str:
    if not nums:
        return ""
    s = sorted(set(nums))
    out, start, prev = [], s[0], s[0]
    for n in s[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = n
    out.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(out)


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    v = _read(path)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _cpu_features() -> list[str]:
    txt = _read("/proc/cpuinfo") or ""
    m = re.search(r"^Features\s*:\s*(.+)$", txt, re.M)
    return m.group(1).split() if m else []


def _midr_map() -> dict[int, str]:
    """cpu lógica -> nombre de núcleo, vía /sys .../regs/identification/midr_el1."""
    out: dict[int, str] = {}
    base = Path("/sys/devices/system/cpu")
    if not base.exists():
        return out
    for d in sorted(base.glob("cpu[0-9]*")):
        try:
            cpu = int(d.name[3:])
        except ValueError:
            continue
        raw = _read(str(d / "regs/identification/midr_el1"))
        if not raw:
            continue
        try:
            midr = int(raw, 16)
        except ValueError:
            continue
        impl = (midr >> 24) & 0xFF
        part = (midr >> 4) & 0xFFF
        out[cpu] = PARTS.get(
            (impl, part), f"{IMPLEMENTERS.get(impl, f'0x{impl:02x}')}-0x{part:03x}"
        )
    return out


def _midr_from_cpuinfo() -> dict[int, str]:
    """Fallback: /proc/cpuinfo trae implementer y part por procesador."""
    txt = _read("/proc/cpuinfo") or ""
    out: dict[int, str] = {}
    cpu = None
    impl = part = None
    for line in txt.splitlines() + [""]:
        if line.startswith("processor"):
            cpu = int(line.split(":")[1])
            impl = part = None
        elif "CPU implementer" in line:
            impl = int(line.split(":")[1], 16)
        elif "CPU part" in line:
            part = int(line.split(":")[1], 16)
        if cpu is not None and impl is not None and part is not None:
            out[cpu] = PARTS.get(
                (impl, part),
                f"{IMPLEMENTERS.get(impl, f'0x{impl:02x}')}-0x{part:03x}",
            )
            impl = part = None
    return out


def _topology() -> tuple[int, int, int]:
    """(cpus lógicas, cores físicos, hilos por core)."""
    base = Path("/sys/devices/system/cpu")
    cpus = sorted(int(d.name[3:]) for d in base.glob("cpu[0-9]*") if d.name[3:].isdigit())
    logical = len(cpus) or (os.cpu_count() or 1)
    ids: set[tuple[int, int]] = set()
    for c in cpus:
        pkg = _read_int(f"{base}/cpu{c}/topology/physical_package_id")
        core = _read_int(f"{base}/cpu{c}/topology/core_id")
        if core is not None:
            ids.add((pkg or 0, core))
    physical = len(ids) or logical
    tpc = max(1, round(logical / physical)) if physical else 1
    return logical, physical, tpc


def _numa_nodes() -> int:
    nodes = list(Path("/sys/devices/system/node").glob("node[0-9]*"))
    return max(1, len(nodes))


def _l3_bytes() -> int | None:
    for idx in (3, 2):
        for d in Path("/sys/devices/system/cpu/cpu0/cache").glob("index*"):
            if _read_int(str(d / "level")) == idx:
                size = _read(str(d / "size"))
                if size and size.upper().endswith("K"):
                    return int(size[:-1]) * 1024
                if size and size.upper().endswith("M"):
                    return int(size[:-1]) * 1024 * 1024
    return None


def _mem_total() -> int | None:
    txt = _read("/proc/meminfo") or ""
    m = re.search(r"^MemTotal:\s+(\d+) kB", txt, re.M)
    return int(m.group(1)) * 1024 if m else None


def detect() -> ArmTarget:
    """Inspecciona el sistema actual. Funciona en no-Arm (con features vacías)."""
    arch = platform.machine()
    features = _cpu_features()
    logical, physical, tpc = _topology()

    parts = _midr_map() or _midr_from_cpuinfo()
    by_part: dict[str, list[int]] = {}
    for cpu, name in parts.items():
        by_part.setdefault(name, []).append(cpu)

    clusters = []
    for name, cpus in sorted(by_part.items(), key=lambda kv: -len(kv[1])):
        freqs = [
            _read_int(f"/sys/devices/system/cpu/cpu{c}/cpufreq/cpuinfo_max_freq")
            for c in cpus
        ]
        freqs = [f for f in freqs if f]
        clusters.append(CoreCluster(part=name, cpus=sorted(cpus), max_khz=max(freqs) if freqs else None))

    # big.LITTLE también aparece como un solo part con dos frecuencias máximas.
    if len(clusters) == 1 and clusters[0].max_khz:
        distinct = {
            _read_int(f"/sys/devices/system/cpu/cpu{c}/cpufreq/cpuinfo_max_freq")
            for c in clusters[0].cpus
        }
        distinct.discard(None)
        if len(distinct) > 1:
            regrouped = {}
            for c in clusters[0].cpus:
                f = _read_int(f"/sys/devices/system/cpu/cpu{c}/cpufreq/cpuinfo_max_freq")
                regrouped.setdefault(f, []).append(c)
            clusters = [
                CoreCluster(part=clusters[0].part, cpus=sorted(cs), max_khz=f)
                for f, cs in sorted(regrouped.items(), key=lambda kv: -(kv[0] or 0))
            ]

    return ArmTarget(
        arch=arch,
        features=features,
        clusters=clusters,
        logical_cpus=logical,
        physical_cores=physical,
        threads_per_core=tpc,
        numa_nodes=_numa_nodes(),
        l3_bytes=_l3_bytes(),
        mem_total_bytes=_mem_total(),
        kernel=platform.release(),
        heterogeneous=len(clusters) > 1,
    )


def is_arm(t: ArmTarget | None = None) -> bool:
    t = t or detect()
    return t.arch in ("aarch64", "arm64")

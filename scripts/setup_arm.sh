#!/usr/bin/env bash
# Prepara una instancia Arm64 limpia (Ubuntu 22.04/24.04, Amazon Linux 2023)
# para medir en serio. Idempotente: se puede volver a correr sin romper nada.
set -euo pipefail

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

say "arquitectura"
uname -m
if [ "$(uname -m)" != "aarch64" ]; then
  echo "aviso: esto no es aarch64. El script sigue, pero no vas a medir Arm."
fi

say "paquetes base"
if command -v apt-get >/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq \
    build-essential cmake git curl python3 python3-pip python3-venv \
    libjemalloc2 google-perftools libgoogle-perftools4 \
    linux-tools-common "linux-tools-$(uname -r)" cpufrequtils numactl || \
    echo "aviso: algún paquete opcional no estaba disponible; se continúa"
elif command -v dnf >/dev/null; then
  sudo dnf install -y -q gcc gcc-c++ cmake git curl python3 python3-pip \
    jemalloc gperftools-libs perf numactl || true
fi

say "governor de frecuencia"
# Sin esto, el DVFS mete varianza que se confunde con el efecto del tuning.
if command -v cpupower >/dev/null && [ -e /sys/devices/system/cpu/cpu0/cpufreq ]; then
  sudo cpupower frequency-set -g performance >/dev/null 2>&1 \
    && echo "governor = performance" \
    || echo "no se pudo fijar (normal en VMs): $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo 'no expuesto')"
else
  echo "cpufreq no expuesto — típico en instancias virtualizadas, no es un problema"
fi

say "acceso a contadores PMU"
current=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo "n/d")
echo "perf_event_paranoid = $current"
if [ "$current" != "n/d" ] && [ "$current" -gt 1 ] 2>/dev/null; then
  sudo sysctl -w kernel.perf_event_paranoid=1 >/dev/null && echo "bajado a 1 para esta sesión"
fi
if command -v perf >/dev/null; then
  perf stat -e cycles,instructions -- true 2>&1 | grep -E "cycles|instructions|not supported" || true
fi

say "transparent hugepages"
cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || echo "no expuesto"

say "apxtune"
pip install -e . --quiet 2>/dev/null || pip install -e . --break-system-packages --quiet
apxtune doctor || true

cat <<'NEXT'

Siguiente paso — verifica todo el flujo en 2 minutos, sin descargar nada:

  apxtune tune workloads/demo-synthetic.toml --out results/

Para el workload real de llama.cpp:

  git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
  mkdir -p ~/llama.cpp/models   # y coloca ahí los GGUF Q4_0 y Q4_K_M
  apxtune tune workloads/llama-bench.toml --out results/

Arm Performix (opcional, para la evidencia de microarquitectura):
  https://developer.arm.com/servers-and-cloud-computing/arm-performix
NEXT

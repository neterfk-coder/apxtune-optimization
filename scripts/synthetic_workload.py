#!/usr/bin/env python3
"""Workload sintético: producto punto int8 por bloques, con parámetros afinables.

Existe para una sola cosa: que cualquiera pueda verificar que apxtune
funciona de punta a punta —búsqueda, estadística, reporte— en pocos minutos,
sin descargar un modelo de 2 GB ni compilar llama.cpp.

Imita el comportamiento de un motor de inferencia real: el throughput depende
de la ruta de cómputo elegida, del tamaño de bloque frente a la caché, y del
número de hilos con rendimiento decreciente. Los óptimos no son triviales,
así que el tuner tiene algo real que encontrar.
"""

from __future__ import annotations

import argparse
import array
import operator
import os
import threading
import time


def make_operands(n: int, seed: int) -> tuple[array.array, array.array]:
    a = array.array("i", [(i * 7 + seed) % 127 - 64 for i in range(n)])
    b = array.array("i", [(i * 13 + seed) % 127 - 64 for i in range(n)])
    return a, b


def dot_scalar(a, b, lo: int, hi: int) -> int:
    """Ruta escalar: un elemento por iteración del intérprete."""
    s = 0
    for i in range(lo, hi):
        s += a[i] * b[i]
    return s


def dot_fused(a, b, lo: int, hi: int) -> int:
    """Ruta 'fusionada': el bucle interno baja a código nativo.

    Es el análogo del salto de un kernel escalar a uno con SDOT/SMMLA:
    el mismo cálculo, mucho menos overhead por elemento.
    """
    return sum(map(operator.mul, a[lo:hi], b[lo:hi]))


def run_pass(a, b, n: int, block: int, fused: bool) -> int:
    dot = dot_fused if fused else dot_scalar
    acc = 0
    for start in range(0, n, block):
        acc += dot(a, b, start, min(start + block, n))
    return acc


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--threads", type=int, default=1)
    p.add_argument("--block", type=int, default=256)
    p.add_argument("--vector", type=int, default=0, help="1 = ruta fusionada")
    p.add_argument("--size", type=int, default=200_000)
    p.add_argument("--iters", type=int, default=4)
    args = p.parse_args()

    cores = os.cpu_count() or 1
    threads = max(1, args.threads)
    fused = bool(args.vector)

    operands = [make_operands(args.size, i) for i in range(threads)]
    sink = [0] * threads

    def worker(idx: int) -> None:
        a, b = operands[idx]
        total = 0
        for _ in range(args.iters):
            total += run_pass(a, b, args.size, args.block, fused)
        sink[idx] = total

    t0 = time.perf_counter()
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    dt = max(time.perf_counter() - t0, 1e-9)

    elements = args.size * args.iters * threads
    rate = elements / dt
    # Techo de ancho de banda: sobre-suscribir cores no compra nada.
    if threads > cores:
        rate *= cores / threads

    print("| model | threads | test | t/s |")
    print(f"| synthetic | {threads} | pp512 | {rate / 1e4:.3f} ± 0.00 |")
    print(f"| synthetic | {threads} | tg128 | {rate / 1e5:.4f} ± 0.00 |")
    print(f"elapsed_s: {dt:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

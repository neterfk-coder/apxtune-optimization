# Contribuir

## Perfiles

La forma más útil de contribuir es una corrida en hardware que aquí no
tenemos. Ver [`profiles/README.md`](profiles/README.md).

Silicio que falta cubrir: Neoverse V1 (Graviton3), N2, N3, V3, AmpereOne,
Microsoft Cobalt, Google Axion, NVIDIA Grace, Cortex-X de móvil, Raspberry Pi 5.

## Workloads

Un workload es un TOML; no requiere tocar código Python. Si añades soporte
para vLLM, ONNX Runtime, ExecuTorch o llama.cpp en modo servidor, mándalo a
`workloads/` con una nota de qué modelo y qué versión usaste.

## Código

```bash
pip install -e ".[dev]"
python -m pytest -q
```

Tres reglas que no se negocian:

1. **Cero dependencias en tiempo de ejecución.** `apxtune` tiene que
   instalarse y correr en una instancia recién creada sin red. Si algo
   necesita una librería, va en `[dev]` o no va. Esto descarta clientes de
   API, servidores y bases de datos: el registro de perfiles son archivos
   JSON versionados en git, y esa es una decisión de diseño, no una carencia.
2. **Ningún número sin intervalo de confianza.** Cualquier ruta que reporte
   rendimiento pasa por `stats.summarize()`. Una corrida no es una medición.
3. **Ninguna comparación entre mediciones tomadas en momentos distintos.**
   Si dos configuraciones se comparan, se miden entrelazadas
   (`bench.measure_paired`). Restar dos números separados por horas de tuning
   mide la deriva del entorno tanto como el cambio.

## Ejecutar la receta de Performix

La receta de `recipes/` es un borrador contra Performix 2026.1. Si tu versión
usa otro esquema, el reporte de la validación (`apx recipe validate`) es en sí
mismo una contribución valiosa — ábrela como issue.

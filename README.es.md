# apxtune — nota en español

El README principal está en inglés porque es el idioma de la evaluación y de
la comunidad de Arm. Este archivo resume lo mismo en dos párrafos.

`apxtune` toma un workload de inferencia en una máquina Arm, busca la mejor
configuración para *ese* silicio en concreto (flags de compilación,
cuantización, hilos, afinidad, allocator) y solo acepta un cambio si supera
tres filtros: dirección correcta, efecto mínimo del 2%, y una prueba de
Mann-Whitney con α=0.05. El resultado es un perfil portable, un script listo
para ejecutar, y un reporte HTML que muestra **cuánto aportó cada decisión
por separado** — no un número final sin explicación.

El speedup que se publica no es una resta de mediciones viejas: al terminar la
búsqueda se vuelve a medir el baseline contra la configuración óptima
**entrelazando** las dos (A,B,B,A,A,B…), para que la deriva del entorno durante
horas de tuning caiga por igual sobre ambas en vez de acreditarse como mejora.

Además captura contadores de hardware con **Arm Performix** antes y después,
para poder demostrar que la mejora vino de usar la ISA de Arm (la fracción de
instrucciones SMMLA/i8mm subiendo) y no de un cambio genérico. Incluye una
receta personalizada de Performix para inferencia LLM, y un GitHub Action que
corre en los runners Arm gratuitos y bloquea regresiones en los PR.

Empezar:

```bash
pip install -e .
apxtune doctor
apxtune tune workloads/demo-synthetic.toml --out results/   # ~2 min, sin descargas
```

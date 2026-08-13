# apxtune — brief para Claude Code

Este archivo es contexto persistente del proyecto. Léelo completo antes de tocar
código. Si algo aquí contradice lo que ves en el repo, el repo manda — pero
avísame de la discrepancia antes de seguir.

---

## 1. Qué es esto y por qué existe

`apxtune` es un envío para el **Arm Create: Desafío de Optimización de IA 2026**
(Devpost, pista **Cloud AI**, cierra el 14 de agosto de 2026, 18:00 GMT-5).

Tesis del proyecto, en una frase: **Arm lanzó Performix (su toolkit de
análisis de rendimiento) en abril de 2026 y pidió públicamente que la
comunidad cree recetas nuevas — apxtune es un agente de auto-tuning que usa
Performix para optimizar inferencia de LLMs en Arm64, y entrega justo esa
receta que falta.**

No es un proyecto "corrí un benchmark en Graviton". Es un bucle cerrado:
mide → busca configuración → valida estadísticamente → solo acepta lo que de
verdad mejora → entrega un perfil reutilizable + evidencia de hardware de
por qué mejoró.

**Lee el README.md completo del repo antes de seguir** — tiene el diseño
técnico detallado. Este archivo es sobre qué falta y cómo priorizarlo, no
repite el diseño.

---

## 2. Estado actual (verificado, no asumido)

Todo lo siguiente está probado y funciona en este momento:

- `src/apxtune/` — 8 módulos, **cero dependencias en runtime**
- 39 tests pasando (`python -m pytest -q`), incluyendo calibración real de
  la tasa de falsos positivos de Mann-Whitney sobre 2000 ensayos
- El speedup publicado sale de una **validación final A/B entrelazada**
  (`search.final_validation`, `bench.measure_paired`): terminada la búsqueda
  se remide baseline vs óptimo alternando A,B,B,A… El cociente de medianas de
  la búsqueda quedó como plan B, y el perfil JSON declara en `validation.method`
  cuál de los dos se usó. Sin esto, el número del titular incluía la deriva
  térmica acumulada entre el inicio y el final de la corrida.
- `apxtune tune workloads/demo-synthetic.toml` corre de punta a punta sin
  descargar nada (~2 min) y produce perfil JSON + reporte HTML + script
- Instalación limpia verificada en un venv nuevo (`pip install -e .`)
- `apxtune detect`, `doctor`, `apply`, `match`, `--dry-run` funcionan
- El workflow de CI (`.github/workflows/arm-perf-guard.yml`) tiene YAML
  válido pero **no se ha corrido en un runner Arm real todavía**
- `recipes/llm-inference.recipe.json` es un **borrador sin validar** contra
  el esquema real de Performix — nadie lo ha corrido contra `apx`
- `workloads/llama-bench.toml` está escrito pero **nunca se ha ejecutado
  contra un llama.cpp real** — los regex de extracción de métricas están
  basados en el formato conocido de `llama-bench -o md`, pero pueden
  necesitar ajuste

Lo que NO existe todavía: ninguna medición real en hardware Arm. Todo el
número "1.77×" o "2.47×" que viste en la conversación previa viene del
workload sintético, que existe solo para probar que la tubería funciona.
**Ese número no va en el envío bajo ningún concepto.**

---

## 3. Reglas duras de la hackathon (no negociables)

Verificar cada una antes de enviar. Si falta una, el envío puede ser
descalificado sin importar la calidad técnica:

- [ ] Repo **público** en GitHub
- [ ] Licencia **Apache-2.0** (ya está en `LICENSE`) **visible en la
      sección "About" del repo** — esto se configura en la UI de GitHub,
      no basta con el archivo. Settings → About → License se detecta sola
      si el archivo se llama `LICENSE`, pero confírmalo visualmente.
- [ ] El repo contiene *todo* el código fuente, assets e instrucciones para
      que el proyecto corra — nada en Google Drive, nada "te lo mando por
      privado"
- [ ] Descripción del envío en Devpost con las tres secciones obligatorias:
      **Resumen del proyecto** (qué es y por qué debería ganar),
      **Funcionalidad/Resultado** (qué hace, cuál es el entregable final),
      **Instrucciones de configuración** (paso a paso, reproducible en un
      dispositivo Arm real)
- [ ] Video opcional pero fuertemente recomendado: **menos de 3 minutos**,
      debe mostrar el proyecto corriendo en el dispositivo real (no un
      mockup), subido a YouTube/Vimeo/Youku como público, sin música con
      copyright ni marcas de terceros
- [ ] Pista 1 y 2 (que incluye Cloud AI): el código fuente debe estar
      adjunto o enlazado a un repo open source — ya cumplido si el repo es
      público

---

## 4. Rúbrica → qué construir primero

100 puntos, cuatro categorías. Prioriza en este orden porque así paga más:

| Categoría | Puntos | Qué la mueve en este proyecto |
|---|---|---|
| Implementación técnica | 40 | Que el tuning real corra en Arm64 y produzca un speedup verdadero y bien medido |
| Factor sorpresa | 25 | El ángulo "Arm construyó el instrumento, yo construí el agente que lo toca" + la cascada de atribución + el instruction-mix antes/después |
| Impacto potencial | 20 | La receta de Performix donable + el registro de perfiles + el GitHub Action reutilizable |
| UX / DX | 15 | Un comando, doctor que diagnostica, README claro |

**Traducción a tareas: sin una corrida real en Arm64, no hay envío.** Todo
lo demás (video, pulido del README, perfiles extra) es secundario a
conseguir el número real.

---

## 5. Tareas pendientes, en orden de ejecución

### Fase A — Conseguir el hardware y el número real (bloqueante, hazlo primero)

1. Levantar una instancia Arm64 (Graviton en AWS es la opción más simple y
   barata; `c8g.2xlarge` o similar). Si el usuario ya tiene acceso a otro
   Arm64 (Mac Apple Silicon vía Docker/Linux VM, Raspberry Pi, Oracle Cloud
   Ampere free tier), usar eso primero — es gratis.
2. `bash scripts/setup_arm.sh` — instala deps, intenta fijar el governor,
   revisa acceso a PMU.
3. `apxtune doctor` — **si esto reporta que el PMU no está accesible, es el
   riesgo #1 del proyecto.** Sin PMU no hay evidencia de Performix, que es
   gran parte del factor sorpresa. Si falla en la instancia elegida, probar
   otra (metal en vez de virtualizada, o cambiar de proveedor). No seguir
   adelante sin resolver esto o decidir conscientemente que el envío no
   tendrá esa sección.
4. Verificar el pipeline sin dependencias externas:
   `apxtune tune workloads/demo-synthetic.toml --out results/`
   Confirmar que corre y que el HTML se ve bien abierto en un navegador.
5. Clonar llama.cpp, descargar **un modelo pequeño** (Llama-3.2-1B o 3B
   Instruct, cuantizado Q4_K_M y Q4_0 — dos archivos GGUF) en
   `~/llama.cpp/models/`.
6. `apxtune tune workloads/llama-bench.toml --dry-run` primero — revisar
   que los comandos generados tienen sentido antes de gastar tiempo de
   cómputo real.
7. Correr `apxtune tune workloads/llama-bench.toml --out results/` de
   verdad. **Presupuesta 1–2 h**, no los 20–60 min que se estimaron al
   principio: con los ejes actuales son ~19 mediciones por pase y ~85
   invocaciones de `llama-bench`, por dos pases, más la validación final.
   (El TOML ya usa `-r 1`; con el `-r 3` original era el triple.) Si algún
   eje falla (regex que no matchea, flag que
   `llama-bench` no reconoce en la versión instalada), **arreglar el TOML,
   no forzar el código** — el diseño es que el conocimiento del motor vive
   en el TOML.
8. Si `apx` (Performix) está instalado: correr con Performix habilitado
   (es el default) y confirmar que el reporte incluye la sección de
   instruction-mix con datos reales, no la nota de "no disponible".

**Criterio de éxito de la Fase A:** un `results/<perfil>.html` real generado
contra llama.cpp de verdad en Arm64, cuyo JSON traiga
`validation.method = "interleaved_ab"`, `validation.significant = true` y un
speedup ≥1.15×. Si el bloque "Procedencia del número" del HTML sale en rojo,
ese perfil no se publica: quiere decir que la ventaja no sobrevivió a la
medición entrelazada. Si el speedup es menor a eso,
está bien — un resultado honesto de "esto ya estaba cerca del óptimo" es
mejor que forzar un número, pero avísame para ajustar el ángulo del envío.

### Fase B — Consolidar evidencia

9. Copiar el perfil resultante a `profiles/` con su nombre real (lo genera
   `apxtune` solo, no renombrar a mano) y hacer commit — reemplaza el
   ejemplo de esquema en `profiles/_schema.json.example` con el real, sin
   borrar el ejemplo (déjalo para que otros entiendan el formato).
10. Validar `recipes/llm-inference.recipe.json` contra el `apx` instalado
    (`apx recipe validate` si el comando existe en esta versión del CLI —
    si no existe, correr la receta directamente y ver si `apx` la acepta).
    Ajustar nombres de eventos si el CLI se queja. Documentar en el
    `_schema_note` del JSON cualquier cambio de versión encontrado — eso en
    sí mismo es una contribución útil a la comunidad de Arm.
11. Correr `python -m pytest -q` una vez más tras cualquier cambio de
    código en las fases A/B.
12. Probar `.github/workflows/arm-perf-guard.yml` de verdad: hacer push a
    un repo de GitHub y confirmar que el job corre en `ubuntu-24.04-arm` y
    produce el resumen. Si el runner no está disponible en el plan del
    usuario, documentarlo como limitación conocida en vez de dejarlo sin
    probar en silencio.

### Fase C — Empaquetar el envío

13. Actualizar el README con el número real (reemplazar el bloque
    "illustrative" del inicio con la salida real, o dejarlo como ejemplo
    de forma pero añadir una sección "Resultado medido" con el número real
    y un link al HTML o al perfil en `profiles/`).
14. Escribir el texto del envío de Devpost (Resumen / Funcionalidad /
    Instrucciones de configuración) — pídemelo aparte cuando lleguen aquí,
    lo armo con el número real en mano.
15. Grabar el video de <3 min: terminal mostrando `apxtune doctor` →
    `apxtune tune` corriendo → el HTML abierto con la cascada de
    atribución → (si hay Performix) el instruction-mix antes/después.
    Grabación de pantalla real, no un mockup.
16. Checklist final de la sección 3 de este documento, uno por uno.
17. Enviar con margen — nunca el mismo día del cierre.

---

## 6. Riesgos conocidos y qué hacer si aparecen

- **PMU no accesible en la instancia elegida** → probar metal en vez de
  virtualizado, o cambiar de proveedor (Graviton en AWS suele exponerlo
  mejor que otras nubes). Si no se resuelve a tiempo, el proyecto sigue
  siendo válido sin la sección de Performix — ajustar el pitch para
  enfatizar el autotuner determinista y la metodología estadística, que no
  dependen de Performix.
- **`llama-bench` no soporta algún flag del TOML** (versiones de llama.cpp
  cambian flags con frecuencia) → editar `workloads/llama-bench.toml`, no
  el código Python. Correr `--dry-run` para depurar rápido sin gastar
  tiempo de cómputo.
- **El regex de extracción de métrica no matchea** → el harness ya lo
  reporta con claridad (`bench.py` imprime la salida real contra la que
  falló el regex). Ajustar el regex en el `[[metric]]` del TOML.
- **El speedup real es pequeño o nulo** → no inflar el número ni bajar el
  umbral de aceptación para forzar una victoria falsa. Un resultado
  "el default ya era casi óptimo en este núcleo, esto es lo que se ganó
  con seguridad estadística" es un envío honesto y sigue siendo válido —
  avísame y ajustamos el ángulo (poner más peso en DX/Impacto/Sorpresa que
  en el speedup crudo).
- **Falta de tiempo antes del cierre** → cortar alcance así, en este
  orden: (1) el video es lo primero que se recorta si hace falta, (2) un
  solo workload real es suficiente, no hace falta cubrir dos motores de
  inferencia, (3) nunca recortar la Fase A — sin número real no hay envío.

---

## 7. Qué NO hacer (control de alcance)

- No añadir un segundo motor de inferencia (vLLM, ONNX Runtime) a menos
  que sobre tiempo después de completar A, B y C con llama.cpp.
- No cambiar la estrategia de búsqueda (descenso por coordenadas) por
  optimización bayesiana u otra cosa más compleja — no es necesario para
  el grid real y el tiempo se gasta mejor en Fase A.
- No reescribir el reporte HTML o el CLI salvo que algo esté roto — ya
  están probados. Si algo se ve mal, es más probable que sea un problema
  de datos (perfil vacío, métrica faltante) que del generador.
- No prometer en el README nada que no se haya medido. Cualquier cifra de
  rendimiento en `README.md` tiene que venir de un `results/*.json` real
  en este repo.

---

## 8. Comandos de referencia rápida

```bash
# instalar
pip install -e ".[dev]"

# diagnóstico del entorno
apxtune doctor

# verificación end-to-end sin dependencias externas
apxtune tune workloads/demo-synthetic.toml --out results/

# el envío real
apxtune tune workloads/llama-bench.toml --dry-run          # depurar primero
apxtune tune workloads/llama-bench.toml --out results/     # correr de verdad

# tests (correr tras cualquier cambio de código)
python -m pytest -q

# aplicar un perfil guardado en otra máquina
apxtune apply --profile results/<clave>.json
```

---

## 9. Definición de "listo para enviar"

No enviar hasta que las tres cosas siguientes sean verdad a la vez:

1. Existe al menos un `results/*.html` generado contra llama.cpp real en
   una máquina Arm64 real, con al menos un cambio aceptado con p<0.05.
2. `python -m pytest -q` pasa completo.
3. Los seis ítems de la sección 3 (reglas duras) están marcados.

Si alguna falla, el proyecto no está listo, sin importar qué tan pulido se
vea el resto.

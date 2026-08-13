# Registro de perfiles

Cada archivo `*.json` de esta carpeta es el resultado de una corrida de
`apxtune tune` en un hardware concreto. El valor no está en tu corrida
individual, sino en que la siguiente persona con el mismo núcleo arranque
desde tu óptimo en lugar de desde el default.

## Usar un perfil

```bash
apxtune match --registry profiles/          # ¿hay algo para esta máquina?
apxtune apply --profile profiles/Neoverse-V2.64c.i8mm+bf16.json
```

`match` busca en tres niveles, de más a menos específico: clave exacta,
mismo núcleo, y misma ruta de GEMM int8 — que es lo que de verdad determina
la forma del óptimo.

## Contribuir el tuyo

1. `apxtune tune <workload> --out results/`
2. Revisa que `rsd_pct` del baseline sea menor a 5%. Por encima de eso el
   entorno tenía demasiado ruido y el perfil no es publicable.
3. Revisa el bloque `validation` del JSON. Para publicar hace falta
   `"method": "interleaved_ab"` y `"significant": true`. Si dice
   `"method": "search_medians"`, la validación final no llegó a correr y el
   speedup arrastra la deriva del entorno durante toda la corrida — no es
   comparable con el resto del registro. Repite sin `--no-validate`.
4. Copia `results/<clave>.json` aquí y abre un PR.

No hace falta que tu corrida haya encontrado una gran mejora. Un perfil que
dice "en este núcleo el default ya era óptimo" ahorra tiempo igual.

## Nomenclatura

`<núcleo>.<cores>c[.<features>].json` — por ejemplo
`Neoverse-V2.64c.i8mm+bf16.json`. La genera `apxtune` sola; no la edites a
mano o `match` dejará de encontrarla.

## Qué contiene

Además de la configuración ganadora, cada perfil guarda la **atribución**:
cuánto aportó cada decisión por separado, con su p-valor. Esa es la parte
que se puede leer y aprender, más allá de copiar el comando.

Y guarda la **procedencia del número** en `validation`. Conviene leer las dos
partes con criterios distintos: la atribución sale de una decena de
comparaciones sucesivas sin corrección por multiplicidad, así que es
orientativa — dice de dónde vino la ganancia, no la certifica. El `speedup` de
arriba sí está certificado: es una sola comparación, decidida antes de medirla,
con las dos configuraciones medidas entrelazadas.

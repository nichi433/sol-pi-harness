# ¿Me sirve en mis sesiones de Claude Code / Codex / OpenCode?

Respuesta corta: **a veces, de forma modesta y no gratis.** Vale la pena si tus sesiones ejecutan una y otra vez
builds/tests que imprimen más de unos pocos KB. Es una pérdida neta (pequeña) si no. Aquí se explica cómo saberlo,
con los números que tenemos y los que no.

## 1. Qué muestra realmente el paper (y qué no)

Revisado contra las tablas del paper ([detalle](./PAPER_2609.20519.md#4-benchmark-results-reported-in-arxiv260920519-re-checked-against-the-papers-tables-1-4)):

| | Resultado |
| :--- | :--- |
| Pila completa de 4 mecanismos vs el baseline Pi del paper | −44,7…−49,0 % tokens, ≈ −⅓ costo, **puntuación 93,7 % (GPT-5.6 Sol) / 94,3 % (Opus 5) del baseline** |
| Misma pila en Opus 5 vs **Claude Code nativo** | −34,6 % tokens, **−54 % costo**, puntuación 42,2 vs 43,7 (**−3,4 %**) |
| Terminal-Bench 4 | 15 tareas resueltas vs 18 de Codex y Pi; costo por tarea resuelta solo −7…−12 % |
| Mejores configuraciones *por puntuación* | mecanismos sueltos (ObservationPack en GPT, Action Fusion en Opus), ahorrando solo ≈ 6 % de tokens |

Leído con honestidad: la eficiencia es real, **cuesta unos puntos de éxito en las tareas**, la evidencia es
preliminar (una configuración congelada, una ejecución reportada, desarrollada sobre un solo modelo) y los
mecanismos se disparan menos en un modelo distinto al de desarrollo.

**Este repositorio no es ese sistema.** El paper implementa los mecanismos *dentro* del harness Pi. Aquí son un
servidor MCP + hooks externos alrededor de Claude Code / Codex / OpenCode, que no pueden hacer todo lo de Pi:

| Paper | En un port externo |
| :--- | :--- |
| Reescribe el historial: los resultados grandes se envían completos 2 solicitudes y luego se cambian por un handle | Imposible. Los resultados grandes se reemplazan **de inmediato** (más ahorro, pero el agente nunca ve el log completo y puede necesitar una consulta extra) |
| Action Fusion forma parte del esquema de la herramienta de edición | Una herramienta más que el agente debe *elegir*. El propio paper halló poco fiable el disparo solo por prompt |
| La compactación corre de forma nativa al completar un paso del plan | Solo una recomendación (`/compact`) |
| El reducer se aplica a un conjunto predefinido de comandos de build/test | Igual, con un hook que solo reescribe comandos **simples** (ver abajo) |

Por eso **no debes esperar aquí los porcentajes del paper.** Solo podemos afirmar lo que medimos.

## 2. Qué medimos (y qué no)

Medido, reproducible (`python benchmarks/run_benchmark.py`):
- En logs de build/test ≥ 4 KiB el recibo es **≈ 94–96 % más pequeño** (tokens estimados), con **21/21** líneas de
  error plantadas visibles directamente (un `tail -n 40` ingenuo conserva 7/21) y **24/24** recuperables por el handle.
- Ejecución real en Claude Code: el hook reescribió `make test` (38 KB de salida) en un recibo que conservó la línea `FAILED`.
- Los secretos se redactan en los recibos y en el log guardado; el hook nunca reescribe comandos compuestos.

**No medido:** éxito de tareas de extremo a extremo, costo total de la sesión, ni cuántos turnos extra gasta un
agente recuperando salida archivada. Eso requiere ejecuciones reales de un agente (protocolo en §5). Hasta
entonces, "−96 %" es *tamaño de la salida de herramientas*, no "−96 % de tu factura".

## 3. A dónde se va el dinero (para razonar)

En el paper ≈ 98 % de los tokens registrados son **lecturas de caché**: cada solicitud relee todo el prompt. Un
resultado de herramienta cuesta entonces `tamaño × (solicitudes posteriores)`, no solo `tamaño`. Por eso reducir
salidas grandes importa, y por eso una salida de 200 bytes no vale la pena tocarla.

Costo de tener el toolkit instalado (en cada solicitud, cacheado): esquemas MCP **≈ 735 tokens** (4 herramientas;
`SOLPI_MCP_TOOLS=all` lista además `solpi_tune`/`solpi_telemetry`), más ≈ 180 tokens de reglas `AGENTS.md` en
Codex/OpenCode. Claude Code difiere los esquemas MCP, así que su costo real es menor.

Regla práctica: en una sesión de *R* solicitudes el costo fijo es ≈ `735 × R` tokens de lectura de caché. Una salida
de 10 KB (≈ 2.500 tokens) a mitad de sesión se relee ≈ *R*/2 veces y un recibo quita ≈ 90 % →
`2.250 × R/2 ≈ 1.100 × R`. **Un log grande por sesión más o menos paga las herramientas; dos o más es ganancia.**
Las sesiones sin salidas de más de ~4 KB solo pagan el costo fijo.

## 4. Quién debería usarlo

| Tus sesiones se parecen a… | Veredicto |
| :--- | :--- |
| Ejecuciones largas, `pytest` / `cargo test` / `npm test` / `make` / `tsc` repetidos, fallos grandes (plantillas C++, muchos tests) | **Probablemente vale la pena.** Es justo lo que ataca el reducer |
| Presión de contexto (autocompactación, 100k+ tokens) | **Probablemente vale la pena**; los recibos además mantienen limpia la ventana |
| Enjambres / subagentes (paper: −27 % costo) | Vale la pena probarlo |
| Sesiones cortas, conversacionales o de leer y editar | **No vale la pena**: no hay nada ≥ 4 KB que reducir |
| Sobre todo `cat`/`grep`/lectura de archivos | **No.** Búsquedas y lecturas se saltan el reducer por diseño (solo head/tail sobre 10 KiB) |
| Encadenas comandos (`cd app && pytest`, `pytest \| tail`) | El **hook los omite a propósito** (seguridad), así que la cobertura es menor de lo que crees. Usa `solpi_run` explícitamente |
| Tareas donde perder una línea de log es caro | Cuidado: el recibo se basa en palabras clave; el handle recupera el resto pero cuesta un turno |

Por herramienta:
- **Claude Code:** el hook + `solpi_read_obs` es la parte probada. Action Fusion requiere que Claude elija
  `solpi_fuse` en vez de su propio Edit: espera poco uso. Verificado de extremo a extremo (reescritura + recibo).
- **Codex:** mismo protocolo (`hooks.json`, `config.toml`); *implementado según la documentación, no ejecutado aquí*:
  compruébalo con `codex` antes de confiar en él.
- **OpenCode:** solo MCP + `AGENTS.md` (sin API de hooks); la salida se empaqueta solo cuando el agente llama a
  `solpi_run`, así que el beneficio es menor y depende de que el modelo siga la regla.
- **AGY / Cursor:** se generan los archivos; no pude confirmar el contrato de hooks de AGY.

## 5. Compruébalo con *tu* uso (30 minutos, sin riesgo)

1. **Estima con tu historial** (lee tus transcripts de Claude Code localmente e imprime solo agregados):
   ```bash
   python3 benchmarks/analyze_sessions.py            # ~/.claude/projects
   ```
   Informa cuántos resultados de Bash son grandes, qué cubre hoy el hook frente a una regla más laxa, los tokens
   de lectura de caché ahorrados estimados y el **neto tras el costo fijo de las herramientas**. Es una *cota
   superior* (ignora autocompactación, turnos de recuperación y calidad).
2. **A/B con tareas reales** (la única forma de ver calidad y turnos extra): elige ~10 tareas representativas,
   ejecuta cada una dos veces (toolkit activado/desactivado, mismo modelo, sesiones nuevas) y compara costo total
   (`/cost`), número de solicitudes y si la tarea pasó. Según el paper, espera *menor costo y quizá una tasa de
   éxito algo menor*; si no ves bajar el costo, desinstala.
3. **Empieza ligero:** instala y quédate solo con lo que rinde: el hook y `solpi_read_obs`. Descarta
   `solpi_fuse`/`solpi_milestone` si el agente nunca los usa.

## 6. Límites conocidos

- El extractor del reducer se basa en **palabras clave** (`error`, `fail`, `panic`, `timed out`, …). Un fallo que no
  imprime ninguna cae al log crudo (fail-open) o se recupera por el handle: no se pierde, pero puede costar un turno.
- Nuestro corpus y algunas correcciones del reducer se desarrollaron juntos; solo las ejecuciones reales de
  gcc/g++/Python son datos reservados limpios (todas conservaron el 100 % de la evidencia).
- Ninguna afirmación cubre salida de herramientas no inglesa, rutas de Windows ni toolchains muy exóticos.

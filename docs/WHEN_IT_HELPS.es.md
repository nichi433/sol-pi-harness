# ¿Me sirve en mis sesiones de Claude Code / Codex / OpenCode?

Respuesta corta: **a veces, de forma modesta y no gratis**, y en un A/B con tareas cortas de Claude Code *no hizo nada* (§7). Vale la pena si tus sesiones ejecutan una y otra vez
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
`2.250 × R/2 ≈ 1.100 × R`. **Un log grande por sesión más o menos paga la instalación completa; dos o más es ganancia.**
Las sesiones sin salidas de más de ~4 KB solo pagan el costo fijo.

**Instalación ligera** (`solpi install --hook-only`): solo el hook y un servidor MCP que anuncia `solpi_read_obs` →
**≈ 143 tokens por solicitud** en vez de ≈ 735, sin skill/reglas ni aviso de compactación. El punto de equilibrio
baja a cualquier resultado de ~1,3 KB o más (`143·R ≤ 0,9 × tokens × R/2`), es decir, **un log normal de ≥ 4 KiB la
paga** (el hook solo toca resultados de ese tamaño de todos modos).

## 4. Quién debería usarlo

| Tus sesiones se parecen a… | Veredicto |
| :--- | :--- |
| Ejecuciones largas, `pytest` / `cargo test` / `npm test` / `make` / `tsc` repetidos, fallos grandes (plantillas C++, muchos tests) | **Probablemente vale la pena.** Es justo lo que ataca el reducer |
| Presión de contexto (autocompactación, 100k+ tokens) | **Probablemente vale la pena**; los recibos además mantienen limpia la ventana |
| Enjambres / subagentes (paper: −27 % costo) | Vale la pena probarlo |
| Sesiones cortas, conversacionales o de leer y editar | **No vale la pena**: no hay nada ≥ 4 KB que reducir |
| Sobre todo `cat`/`grep`/lectura de archivos | **No.** Búsquedas y lecturas se saltan el reducer por diseño (solo head/tail sobre 10 KiB) |
| Encadenas comandos (`cd app && pytest`, `PYTHONPATH=src pytest`) | Los cubre el modo `prefixed` del hook, por defecto (ver §4b). Pipes/redirecciones/`&` (`pytest \| tail`) nunca se reescriben |
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
   Informa, como porcentaje de tus tokens de lectura de caché: lo que cubre el hook de build/test (`hook_only`,
   `hook_prefixed`, `reducible`), lo que cubriría archivar cualquier resultado grande de Bash (`all_big_bash`) y el
   caso más amplio con todas las herramientas, incl. `Read`/`Grep` (`all_big_any`, con desglose por herramienta),
   cada uno con el **neto tras el costo fijo**. El ahorro se corta en la siguiente compactación detectada (una caída
   brusca de cache-read); al lado se imprime la cifra sin compactación. Sigue siendo una *cota superior*: sin turnos
   de recuperación, sin cambio de calidad, y Claude Code además limpia resultados viejos por su cuenta.
2. **A/B con tareas reales** (la única forma de ver calidad y turnos extra): elige ~10 tareas representativas,
   ejecuta cada una dos veces (toolkit activado/desactivado, mismo modelo, sesiones nuevas) y compara costo total
   (`/cost`), número de solicitudes y si la tarea pasó. Según el paper, espera *menor costo y quizá una tasa de
   éxito algo menor*; si no ves bajar el costo, desinstala.
3. **Empieza ligero:** `solpi install --target claude --hook-only` deja solo lo que respaldan los benchmarks: el
   hook y `solpi_read_obs`. Añade la instalación completa después si el agente de verdad usaría
   `solpi_fuse`/`solpi_milestone`.

## 4b. ¿Qué comandos reescribe el hook? (`hook_mode`)

Un usuario ejecutó `analyze_sessions.py` sobre 112 sesiones reales de Claude Code y reportó que la primera regla, la
más estricta, nunca se activó (0 resultados): casi todos los comandos de Claude Code empiezan con `cd <dir> &&` o `VAR=val`. Por eso hay dos
modos (`observation_pack.hook_mode` en `sol-pi.json`, o `solpi install --hook-mode`, por defecto **`prefixed`**):

| Modo | Se reescribe | Nunca se reescribe |
| :--- | :--- | :--- |
| `safe` | un solo comando simple de build/test | cualquier cosa encadenada o con variables de entorno |
| `prefixed` (defecto) | además `cd <dir> && cmd`, `export VAR=val && cmd`, `VAR=val cmd` (≤ 2 prefijos, `&&`/`;`) solo con variables **de una lista permitida** (`PYTHONPATH` relativo al proyecto, `CI`, `NODE_ENV`, `RUST_BACKTRACE`, …) | pipes, redirecciones, `&`, `$()`/backticks, `LD_PRELOAD`, `PATH`, `NODE_OPTIONS`, `PYTEST_ADDOPTS`, cualquier otra variable |

Por qué tanta cautela: la reescritura se **aprueba automáticamente** (`permissionDecision: allow`), así que nunca debe
dejar pasar algo que las reglas de permisos del comando original habrían frenado. A propósito **no existe un modo
"envolver todo"**: aprobaría también `rm -rf ~`, y `solpi run` no tiene TTY/stdin y mezcla stderr. Para otros comandos
el agente puede llamar a `solpi_run` por su cuenta. El comando envuelto además pasa `--timeout` (≥ 30 min, por encima
del timeout propio del Bash del harness) para que un build largo no muera a los 120 s por defecto de `solpi run`.

## 6. Límites conocidos

- El extractor del reducer se basa en **palabras clave** (`error`, `fail`, `panic`, `timed out`, …). Un fallo que no
  imprime ninguna cae al log crudo (fail-open) o se recupera por el handle: no se pierde, pero puede costar un turno.
- Nuestro corpus y algunas correcciones del reducer se desarrollaron juntos; solo las ejecuciones reales de
  gcc/g++/Python son datos reservados limpios (todas conservaron el 100 % de la evidencia).
- Ninguna afirmación cubre salida de herramientas no inglesa, rutas de Windows ni toolchains muy exóticos.

## 7. Resultado del A/B: el mismo agente con y sin el toolkit (medido)

`python3 benchmarks/ab/run_ab.py --out ab_results --reps 3` (código y datos crudos en `benchmarks/ab/`). Claude Code
2.1 + Sonnet 5.5, 10 tareas pequeñas que escribí yo (6 donde la salida de tests/build pesa 8–50 KB y 4 de control
con salida mínima), cada una 3 veces por condición = 60 ejecuciones reales, gasto total de API **$2,55**. La
condición B es `solpi install --hook-only` (modo `prefixed`); ninguna carga ajustes de usuario, skills ni otros MCP.

| | A (sin nada) | B (hook-only) |
| :--- | :---: | :---: |
| Tareas resueltas | **30/30** | **30/30** |
| Costo total | $0,42 | $0,43 (**+3,3 %**, ruido) |
| Tareas con salida grande, media geométrica de costo B/A | | **1,08** (por tarea 0,92 … 1,42) |
| Tareas de control, media geométrica de costo B/A | | **0,94** |
| Recibos generados / llamadas de recuperación | – | **0 / 0** |
| Comandos Bash con pipe a `tail`/`head`/`grep` | **85 %** | **79 %** |

**El toolkit no hizo nada en estas sesiones**: ni daño ni ahorro. El hook nunca se activó (0 recibos en 30
ejecuciones) porque el agente *ya recorta su propia salida*: 4 de cada 5 comandos eran
`python3 -m unittest -v 2>&1 | tail -30` o `make 2>&1 | head -50`, y el hook, por diseño, nunca reescribe comandos con
pipe. Las diferencias de costo son ruido entre ejecuciones (desviación estándar de una sola ejecución ≈ 28 %); ninguna
tarea cambió su resultado.

Qué dice y qué no dice esto:
- **Sí** muestra que en sesiones cortas de Claude Code con un modelo actual el ahorro es ≈ 0, lo contrario de los
  escenarios optimistas del §3, que suponen que las salidas grandes sin recortar llegan de verdad al contexto.
- **No** cubre sesiones largas (estas duraron 3–9 turnos, ≈ $0,04 cada una), otros agentes (Codex/GPT puede no recortar
  igual) ni tareas donde el agente necesita el log *completo*. Ahí está el origen de las ganancias del paper y no se probó.
- Todas las tareas tienen solución y se resolvieron en ambas condiciones (efecto techo), así que las diferencias de
  calidad solo aparecerían con tareas más difíciles.
- `python3 benchmarks/analyze_sessions.py` sobre *tu propio* historial sigue siendo la forma más rápida de ver si en
  tus sesiones existen salidas grandes sin recortar.
- Comprobaciones adicionales: las mismas 10 tareas con Haiku (1 ejecución cada una, $0,09 en total) también dieron
  **0 recibos** (77 % de sus comandos llevaban pipe), y en las ejecuciones con Sonnet la salida de herramientas fue solo
  **≈ 1,3 % del contexto releído** (≈ 106k tokens por ejecución, casi todo prompt de sistema y definiciones de
  herramientas). Es decir, en sesiones cortas ni un reductor perfecto ahorraría más de ≈ 1 %; la palanca, si existe,
  son las **sesiones largas donde se acumulan resultados viejos**, y un hook de Bash no llega ahí.

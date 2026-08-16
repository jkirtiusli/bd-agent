# Prompt para la sesión del Core

> Copiar todo lo que sigue (desde "CONTEXTO") en una sesión nueva sobre el repo
> `bd-core`. Es autocontenido: no necesita ver el repo del Agente.
> El contrato descrito acá es el que el Agente **ya implementa** en la rama
> `claude/farm-agent-independence-dsa8iq` (v3.0.1).

---

CONTEXTO

Tengo un sistema de captura de datos de granjas avícolas. Un Agente en Python
corre en cada granja, lee los CSV que exporta BD-Copy (Big Dutchman), los
normaliza y los empuja a este Core. Acabo de reescribir el Agente (v3) para que
sea independiente: ahora tiene una cola local en SQLite, reintentos con backoff
y un heartbeat rico. El lado del Agente está terminado y testeado.

Falta el otro lado. Hoy el Core tiene un `/ingest` y un `/v1/agente/heartbeat`
básicos. El problema central: **si una granja deja de reportar, nadie se
entera.** La detección tiene que ser por AUSENCIA de latido, y eso solo puede
vivir acá — si el Agente no corre, no hay latido que lo avise.

Quiero que implementes el lado Core. Antes de escribir código, leé el repo y
decime si algo de lo que sigue choca con lo que ya existe.

---

CONTRATO QUE EL AGENTE YA CUMPLE (no lo cambies sin avisarme)

**1) Ingesta de datos**

    POST <url configurada, hoy https://core.flowkore.com/ingest>
    Content-Type: application/json
    Authorization: Bearer <token de la granja>
    Idempotency-Key: <sha256 hex, determinístico sobre las claves del lote>

Body: un array JSON de registros (lotes de 2000 por defecto):

```json
[{
  "granja": "astillas_de_plata",
  "galpon": "Plc1_HouseA",
  "ciclo": "7",
  "metrica": "huevos",
  "fecha_dato": "2026-08-11",
  "hora_cierre": "22:00",
  "zona_horaria": "America/Argentina/Buenos_Aires",
  "capturado_en": "2026-08-12T09:00:00",
  "valor": 15000,
  "edad_dia": 200,
  "semana": 29,
  "fuente": "MANPRODUCTION_TODAYEGG.csv",
  "clave": "9f2a...c1"
}]
```

- `clave` = `sha256("granja|galpon|ciclo|metrica|fecha_dato")`. Es la identidad
  del dato: **usala como clave del UPSERT.** Es opcional por compatibilidad con
  agentes viejos; si no viene, calculala en el servidor con esa misma fórmula.
- `valor` puede ser `null`. `edad_dia`, `semana` y `hora_cierre` también.
- **`valor` es numérico, no entero.** Hoy las 12 métricas son cuentas enteras
  (huevos, aves, gramos), pero las que vienen —temperatura, humedad, peso
  promedio— traen decimales. Guardalo como `NUMERIC`/`DOUBLE`. Elegir
  `INTEGER` ahora es gratis y cambiarlo después es una migración con pérdida
  de datos ya ingeridos.
- **Dejá lugar para dato intradiario.** Hoy todas las métricas son un valor por
  día y `clave` se calcula sobre esos 5 campos. Si más adelante sumamos
  temperatura por hora, el Agente mandará un campo `hora` y lo incluirá en el
  cálculo de `clave` — para vos son registros nuevos, no un cambio de esquema.
  Guardá `hora` como columna nullable desde ahora y la clave natural como
  `(granja, galpon, ciclo, metrica, fecha_dato, hora)`. Es gratis hacerlo ahora
  y es una migración si se hace después.
- Métricas posibles hoy: `huevos`, `aves_vivas`, `alimento_acumulado`,
  `alimento_dia`, `alimento_por_ave`, `agua_acumulado`, `agua_dia`,
  `agua_por_ave`, `silo`, `peso`, `mortalidad`, `descartes`. La lista crece:
  no la hardcodees de forma que un valor nuevo haga fallar la ingesta.
- El Agente nunca manda el día en curso, solo días cerrados.

**Cómo el Agente interpreta tu respuesta** (esto define tu comportamiento):

| Respuesta | Qué hace el Agente |
|---|---|
| `200/201/202/204` | da **todo el lote** por confirmado y lo saca de su cola |
| `401/403` | corta el drenaje entero (token inválido) y avisa |
| otro `4xx` | marca ese lote como rechazado definitivo, **sigue con el resto** |
| `408/425/429/5xx` | reintenta con backoff; respeta `Retry-After` si lo mandás |
| timeout / sin red | reintenta con backoff |

**Consecuencia crítica: la ingesta de un lote tiene que ser todo-o-nada
(transaccional).** Si aceptás parcialmente y devolvés 200, el Agente borra de
su cola registros que vos no guardaste y se pierden para siempre.

**2) Heartbeat**

    POST <base>/v1/agente/heartbeat
    Authorization: Bearer <token de la granja>

```json
{
  "granja": "astillas_de_plata",
  "ok": true,
  "registros": 12,
  "mensaje": "12 enviados",
  "agente_ts": "2026-08-12T09:00:00-03:00",
  "version_agente": "3.0.1",
  "hostname": "gw-astillas",
  "zona_horaria": "America/Argentina/Buenos_Aires",
  "origen_alcanzable": true,
  "fuente_frescura_seg": 900,
  "galpones_vistos": 8,
  "disco_libre_mb": 180000,
  "pendientes_en_cola": 0,
  "confirmados_total": 141000,
  "ultimo_dato_fecha": "2026-08-11",
  "ultimo_error": null,
  "max_intentos": 0
}
```

Llega cada 5 minutos, aunque no haya datos nuevos. Si trae `"prueba": true` es
un `--diagnostico` manual: registralo pero no lo cuentes como corrida.

Guardá el latido completo (histórico acotado) y un estado actual por granja.

---

QUÉ QUIERO QUE IMPLEMENTES

**1. Ingesta idempotente (lo más importante)**
UPSERT por `clave`, dentro de una transacción por request. Reenviar lo mismo
mil veces tiene que dar el mismo resultado. Además, cachear el
`Idempotency-Key` (24 h alcanza) y devolver 200 sin reprocesar si se repite:
cubre el caso del timeout ambiguo, donde el Agente reintenta un lote que en
realidad sí entró.

**2. Un token por granja**
Hoy hay un `CORE_INGEST_TOKEN` compartido por todas las granjas. Es el agujero
de seguridad más serio: una PC de granja comprometida compromete la ingesta de
todas, y no se puede rotar el de una sola.

- Tabla de granjas + tabla de tokens guardados **hasheados**, con prefijo
  identificable, `creado_en`, `ultimo_uso`, `revocado_en`.
- El token resuelve la granja. Si un registro trae una `granja` distinta a la
  del token → **403**, no lo guardes.
- Rotación sin downtime: que convivan dos tokens válidos por granja durante la
  transición.
- Endpoint de enrolamiento (`POST /v1/granjas/enrolar`) que devuelva el token
  una sola vez, para que el script de instalación del gateway no requiera
  copiar y pegar secretos a mano.
- Dejame un camino de migración desde el token compartido actual, sin cortar la
  granja que ya está andando.

**3. Dead-man's switch — la razón de ser de todo esto**
Un job cada 10 minutos que calcule el estado de cada granja:

| Estado | Condición |
|---|---|
| `verde` | latido < 30 min **y** dato de ayer completo |
| `amarillo` | latido entre 30 min y 2 h, o `pendientes_en_cola > 0`, o `fuente_frescura_seg > 6 h`, o falta algún galpón |
| `rojo` | sin latido hace más de 2 h, o sin dato de ayer a las 08:00 |

Usá `fuente_frescura_seg` para separar dos fallas que si no se ven iguales, y
que se arreglan distinto:

    no llega ningún latido       -> el gateway se cayó (luz, internet, equipo)
    latido con frescura alta     -> el gateway está bien, BD-Copy dejó de exportar
    latido con pendientes_en_cola -> el Agente está bien, VOS estás rechazando

Que los umbrales sean configurables por granja.

**4. Alertas que lleguen al teléfono**
Bot de Telegram (gratis, y es lo más rápido de montar) + email de respaldo.
Reglas que me importan más que el canal:

- **Una alerta por incidente, no una por ciclo.** Si alerto cada 10 minutos, en
  dos semanas las ignoro y el sistema deja de servir.
- Aviso de recuperación cuando vuelve a verde.
- El texto tiene que decir **qué hacer**, no solo qué pasó. Ej: *"Astillas de
  Plata: sin latido hace 2 h. Última corrida OK 10:15. Revisar UPS y router; si
  responde por Tailscale, correr --diagnostico."*
- Un resumen diario a las 08:00 con el estado de toda la flota, aunque esté
  todo bien: es la forma de saber que el sistema de alertas sigue funcionando.

**5. Chequeo de completitud (no solo de presencia)**
"Llegaron datos" no es lo mismo que "llegaron todos". Por cada granja, guardá
cuántos galpones y qué métricas se esperan, y alertá si para la fecha de ayer
falta alguno. Un galpón que dejó de reportar hace tres días con el resto en
verde es exactamente el error que hoy no se detecta.

**6. Canal de actualización de los Agentes**

El Agente v3 se auto-actualiza **por pull**, y el canal sos vos, no GitHub. Ya
implementa el lado cliente; falta el endpoint:

    GET <base>/v1/agente/version
    Authorization: Bearer <token de la granja>
    X-Agente-Version: 3.0.1
    X-Agente-Granja: astillas_de_plata
    X-Agente-Plataforma: windows-exe

Respuesta `200`:

```json
{
  "version": "3.1.0",
  "notas": "arregla el parseo de filas largas",
  "paquetes": {
    "ejecutable":  {"url": "https://.../agente-bdcopy.exe", "sha256": "9f2a..."},
    "fuente_tar":  {"url": "https://.../v3.1.0.tar.gz",     "sha256": "1b7c..."}
  },
  "version_fijada": null
}
```

Devolvé `204` si esa granja no tiene que hacer nada. `404` también es válido
mientras no lo implementes: el Agente lo toma como "no hay canal" y sigue
normal, sin ruido en los logs.

Por qué el canal es el Core y no GitHub directo: **vos decidís qué granja
actualiza y cuándo.** Eso permite canario de verdad — le devolvés `3.1.0` a una
sola granja, mirás 24 h en el tablero (el Agente reporta `version_agente` y
`ultima_actualizacion` en cada latido), y recién ahí se lo devolvés al resto. Y
si algo sale mal, `version_fijada: "3.0.0"` revierte la flota entera **sin
entrar a ninguna granja** — el Agente aplica una versión fijada aunque sea más
vieja que la instalada.

Lo que quiero del lado tuyo:
- La versión objetivo configurable **por granja**, con un default para el resto.
- `ejecutable` es el paquete para quien corre el `.exe`; `fuente_tar` para el
  gateway Linux, que corre desde código. `X-Agente-Plataforma` te dice cuál
  necesita cada granja (`windows-exe`, `linux-fuente`).
- Los `sha256` calculados de verdad sobre los archivos publicados. El Agente
  rechaza el paquete si no coincide, y rechaza el manifiesto si no trae sha256.
- Las URLs tienen que ser HTTPS (el Agente rechaza `http://`).
- Registrá qué granja consultó y qué se le respondió: es el rastro para saber
  por qué una granja no converge.

Con eso el ciclo completo es: pusheás un tag → GitHub Actions publica el `.exe`
→ cargás versión + sha256 en el Core → la granja canario se actualiza sola esa
madrugada → mirás el tablero → abrís al resto.

**7. Vista de flota**
`GET /v1/agente/estado`: una fila por granja con semáforo, último latido, fecha
del último dato, versión del agente, pendientes en cola, galpones esperados vs
recibidos, resultado de la última actualización y último error. Es el tablero
que miro a la mañana. Si el proyecto ya tiene frontend, una página; si no, JSON
alcanza para arrancar.

---

CONSIDERACIONES

- Escala prevista: ~20 granjas × ~8 galpones × ~12 métricas × 1 registro/día.
  Es poco volumen: priorizá que sea simple y correcto sobre que sea rápido.
- La primera carga histórica de una granja son ~141k registros en lotes de
  2000. No te ahogues ahí (y no mandes 429 en esa ráfaga, o el backfill tarda
  horas).
- Zona horaria: `fecha_dato` es la fecha local de la granja, ya resuelta por el
  Agente. No la conviertas.
- Los `capturado_en` del Agente pueden venir sin offset; `agente_ts` sí lo trae.

CRITERIOS DE ACEPTACIÓN

1. Mando el mismo lote 3 veces seguidas → la base queda igual que después de la
   primera, y las respuestas 2 y 3 no reprocesan.
2. Corto el heartbeat de una granja de prueba → me llega **una** alerta a
   Telegram en menos de 2 h, con texto accionable; no me llegan 12.
3. Reconecto → me llega el aviso de recuperación y la granja vuelve a verde.
4. Uso el token de la granja A para mandar datos con `granja: "B"` → 403 y no
   se guarda nada.
5. Borro un galpón del envío de ayer → salta la alerta de completitud aunque el
   latido esté verde.
6. Simulo un 500 a mitad de un lote → nada de ese lote queda a medio guardar, y
   cuando el Agente reintenta, entra completo.

Empezá por el punto 3 (dead-man's switch + alertas). Es lo que más me falta
hoy: sin eso, todo lo demás sigue dependiendo de que yo me acuerde de mirar.

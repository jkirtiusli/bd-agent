# Independencia del Agente: Gateway por granja

**Fecha:** 2026-08-12
**Estado:** propuesta (pendiente de decisión de hardware)
**Alcance:** primera implementación en Astillas de Plata; diseño pensado para replicarse en N granjas.

---

## 1. El problema, en una frase

Hoy el Agente vive **dentro de la computadora operativa de la granja**, y esa
computadora no es un servidor: se apaga, la usa gente, se reinicia sola con las
actualizaciones de Windows, y no tenemos forma de entrar a repararla desde afuera.
El resultado es que la llegada del dato depende de que alguien en la granja haga
(o no haga) algo.

## 2. Diagnóstico: los 8 puntos por los que hoy se corta

Todos están en el código actual y son verificables:

| # | Falla | Dónde | Consecuencia |
|---|---|---|---|
| 1 | La tarea programada corre **una sola vez por día** a las 12:30 | `scripts/instalar_tarea.bat` | Si la PC está apagada a esa hora exacta, ese día no hay corrida. No hay reintento. |
| 2 | La tarea **solo corre con el usuario logueado** (limitación documentada en el propio `.bat`) | `scripts/instalar_tarea.bat` | Reinicio de Windows sin que nadie inicie sesión = agente muerto en silencio. |
| 3 | **No hay reintentos ni backoff** en la entrega | `bd_agent/destino.py:24-38` | Un corte de internet de 5 minutos justo a las 12:30 pierde la corrida entera. |
| 4 | **No hay cola local (store-and-forward)** | — | El agente no tiene memoria de qué entregó. Si el Core está caído, no guarda nada para después: depende de volver a leer el CSV completo mañana. |
| 5 | El heartbeat **solo existe si el agente corre** | `bd_agent/salud.py:31` | Justo el caso que nos importa (la PC apagada) es el caso en el que no llega ningún aviso. La detección tiene que ser por **ausencia**, del lado del Core. |
| 6 | **Reenvío total en cada corrida** (todo el histórico, ~141k registros) | `bd_agent/parser.py:103` + `destino.py:26` | Funciona hoy como "auto-reparación", pero crece sin techo y obliga al Core a ser idempotente. Si el lote 40 falla, los 39 anteriores ya entraron y la corrida se marca como error igual. |
| 7 | **Un solo token compartido** para todas las granjas | `config_ejemplo.yaml` (`CORE_INGEST_TOKEN`, "el mismo del Core") | Una PC de granja comprometida = ingest comprometido para **todas** las granjas. No se puede rotar el de una sola. |
| 8 | **Cero acceso remoto** | — | Cualquier arreglo requiere que alguien esté físicamente en la granja. |

**El punto clave para entender el resto del documento:** los CSV los produce
BD-Copy corriendo en esa PC de Windows. Si esa PC está apagada, **no se genera
dato nuevo** — eso ninguna computadora aparte lo puede inventar. Lo que sí
resuelve una computadora aparte es todo lo demás: que el dato que ya existe
llegue siempre, que nunca se pierda, que yo me entere en minutos cuando algo se
corta, y que pueda entrar a arreglarlo sin viajar.

## 3. La decisión de fondo: separar captura de entrega

```
   ANTES (todo acoplado a una sola PC)

   [PC operativa Windows]
     BD-Copy  ->  CSV  ->  Agente  ->  internet  ->  Core
                            (12:30, una vez, sin red = se perdió)


   PROPUESTO (dos responsabilidades, dos máquinas)

   [PC operativa Windows]              [Gateway dedicado, 24/7]
     BD-Copy -> CSV en carpeta  --LAN-->  Agente (cada 15 min)
     (solo genera el dato)               + cola SQLite (store-and-forward)
                                         + heartbeat cada 5 min
                                         + Tailscale (acceso remoto)
                                              |
                                              v  reintentos con backoff
                                            Core (idempotente)
                                              |
                                              v
                                       Alertas si una granja calla
```

La PC operativa vuelve a hacer una sola cosa: generar CSV. Todo lo frágil
(red, horarios, reintentos, credenciales, mantenimiento remoto) se muda a una
máquina que existe **solo para eso**, que no usa nadie, y que está prendida
siempre.

## 4. Qué comprar para esta granja

### Obligatorio

| Ítem | Recomendación | Aprox. USD | Por qué |
|---|---|---|---|
| Mini PC fanless | Intel N100, 8-16 GB RAM, SSD 256 GB NVMe | 180-250 | Sin ventilador = sin polvo de granja. x86 y SSD (no microSD, que se corrompe). Consume ~10 W. |
| UPS | 700-900 VA con AVR | 80-120 | En granja los cortes y bajones de tensión son la causa #1 de "se apagó". Alcanza para 20-40 min y para apagar prolijo. |
| Cable de red | UTP al router/switch | 10 | Ethernet, no WiFi. Elimina una clase entera de fallas intermitentes. |

**Total: ~US$ 300 por granja.**

Alternativa: Raspberry Pi 5 (8 GB) + gabinete + fuente oficial + SSD NVMe
(~US$ 170). Es válida, pero en Argentina el mini PC N100 suele ser más fácil de
conseguir, y x86 evita sorpresas con dependencias (pandas, etc.). **Recomiendo
el mini PC N100.**

### Opcional, según cuánto duela quedarse sin internet

| Ítem | Aprox. USD | Cuándo vale la pena |
|---|---|---|
| Router 4G/LTE con SIM de datos | 60-100 + ~5/mes | Si el internet de la granja se cae seguido. Con el spool local, un corte de horas ya no pierde datos — así que esto es "que llegue antes", no "que no se pierda". Yo lo dejaría para la fase 3. |
| UPS también para la PC de BD-Copy | 80-120 | Sí vale: es lo único que hace que la PC que **genera** el dato sobreviva a un corte. |

### Ajustes gratis en la PC de BD-Copy (hacer sí o sí)

Son cambios de configuración, no cuestan nada, y atacan la causa raíz de "la PC estaba apagada":

1. **BIOS → "Restore on AC Power Loss" = Power On.** Vuelve la luz, la PC arranca sola.
2. **Auto-login del usuario + BD-Copy en el arranque** (`shell:startup`). Sin esto, la PC arranca pero BD-Copy no exporta nada.
3. **Wake-on-LAN habilitado** (BIOS + placa de red). Le permite al gateway mandarle un "magic packet" y prenderla remotamente si quedó apagada.
4. **Horario activo de Windows Update** fuera del horario de exportación, y reinicio automático controlado.
5. **Carpeta de CSV compartida en solo lectura** para el usuario del gateway (ver 5.2).

## 5. Software del gateway

### 5.1 Base

- **Debian 12 estable** (o Ubuntu Server LTS). Sin escritorio.
- Usuario dedicado `bdagent`, sin sudo, sin login por contraseña.
- `unattended-upgrades` para parches de seguridad automáticos.
- `systemd-timesyncd` — el reloj correcto no es opcional: todo el modelo de datos es por fecha.

### 5.2 Cómo lee los CSV

**Opción A (recomendada): recurso compartido SMB en solo lectura.**
En la PC de BD-Copy se comparte la carpeta `...\BackUp\csv` con un usuario
dedicado y permiso de **solo lectura**. El gateway la monta con `cifs`
(`ro,noserverino` en `/etc/fstab` + credenciales `0600`). El agente nunca puede
escribir sobre el origen: si el gateway se compromete, no puede corromper los
datos de la granja.

**Opción B (respaldo, o si no hay LAN entre las dos máquinas): OneDrive.**
La ruta actual (`C:\Users\PC\OneDrive\Escritorio\BDCopy\BackUp\csv`) ya está
dentro de OneDrive, así que los CSV están replicados en la nube. El gateway
puede bajarlos con `rclone` usando una cuenta de servicio con permiso de lectura.
Sirve incluso si el gateway está fuera de la granja. Ojo: OneDrive sincroniza
solo mientras la PC esté prendida, así que no resuelve el caso "PC apagada"
mejor que la opción A.

Diseño: el agente debe soportar **ambas** vía config (`origen.tipo: smb | local | rclone`),
para no atarnos y para que otras granjas puedan tener otra topología.

### 5.3 Cómo corre

Nada de tarea diaria a una hora fija. Un `systemd` service + timer:

- `bd-agent.timer`: cada **15 minutos**, con `Persistent=true` (si el gateway
  estuvo apagado, corre apenas vuelve — es el equivalente sano de
  `StartWhenAvailable`).
- `bd-agent.service`: `Type=oneshot`, `Restart=on-failure`, `RestartSec=60`,
  `LoadCredential=` para el token (no queda en el YAML), `ProtectSystem=strict`,
  `PrivateTmp=yes`.
- `bd-agent-heartbeat.timer`: cada **5 minutos**, independiente del ciclo de
  datos. Que el latido no dependa de que haya datos nuevos.

Correr cada 15 min en vez de 1 vez por día cambia el juego: **una corrida fallida
deja de ser un día perdido y pasa a ser 15 minutos de retraso.**

### 5.4 Acceso remoto: Tailscale

- Tailscale en el gateway, en tu notebook y en tu teléfono. Red privada tipo mesh.
- **Sin abrir puertos, sin IP fija, sin port forwarding.** Todas las conexiones
  salen desde la granja hacia afuera, lo cual también es lo correcto desde
  seguridad: el gateway no expone nada a internet.
- Tailscale SSH con claves, MFA en la cuenta, y ACLs para que cada granja quede
  aislada de las demás.
- Gratis hasta 100 dispositivos / 3 usuarios: alcanza para decenas de granjas.
- Extra: desde el gateway, `wakeonlan <MAC>` prende la PC de BD-Copy si quedó apagada.

## 6. Cambios necesarios en el Agente (este repo)

Ordenados por impacto. Los tres primeros son los que realmente compran independencia.

### 6.1 Cola local con store-and-forward (`bd_agent/spool.py` — nuevo)

SQLite local (`/var/lib/bd-agent/spool.db`). Cada registro normalizado obtiene
una **clave determinística**:

```
clave = sha256(granja|galpon|ciclo|metrica|fecha_dato)
```

Tabla `pendientes(clave PK, payload JSON, creado_en, intentos, ultimo_error)` y
tabla `confirmados(clave PK, valor_hash, confirmado_en)`.

Flujo por corrida:
1. Parsear los CSV → registros.
2. Descartar los que ya están en `confirmados` **con el mismo `valor_hash`** (un dato corregido río arriba se reenvía; uno idéntico, no).
3. Encolar el resto en `pendientes`.
4. Drenar `pendientes` en lotes contra el Core; mover a `confirmados` solo lo que el Core acusa OK.

Qué gana esto:
- **Sobrevive días sin internet** sin perder un solo registro.
- El tráfico diario pasa de ~141k registros a solo lo nuevo (decenas).
- El backfill histórico deja de ser un efecto secundario accidental y pasa a ser un comando explícito (`--reenviar-desde 2026-01-01`).

### 6.2 Reintentos con backoff y entrega parcial correcta (`destino.py`)

- Backoff exponencial con jitter: 1s, 2s, 4s… tope 5 min, con límite de intentos por corrida.
- Reintentar solo lo reintentable: timeouts, 5xx, 429 (respetando `Retry-After`). Un 400/401 no se reintenta, se alerta.
- **Confirmación por lote:** cada lote que responde OK marca *sus* claves como confirmadas. Hoy, si falla el lote 40, los 39 anteriores ya entraron pero la corrida entera se reporta como error (`agente.py:29-33`) y no queda registro de qué sí pasó.
- `Idempotency-Key` por lote, para que un reintento tras un timeout ambiguo no duplique.

### 6.3 Heartbeat que sirva para diagnosticar (`salud.py`)

Hoy el latido manda `granja, ok, registros, mensaje, agente_ts`. Agregarle:

```yaml
version_agente:      # para saber qué granja quedó vieja
hostname:
ultimo_dato_fecha:   # la fecha del dato más nuevo que logró leer
pendientes_en_cola:  # tamaño del spool: si crece, el Core está rechazando
fuente_frescura_seg: # antigüedad del CSV más nuevo -> detecta "BD-Copy no exporta"
galpones_vistos:     # detecta un galpón que dejó de reportar
disco_libre_mb:
origen_alcanzable:   # el share SMB responde?
```

`fuente_frescura_seg` es la joya: permite distinguir **"el gateway está bien
pero la PC de BD-Copy murió"** de "el gateway murió". Son dos problemas
distintos con dos acciones distintas, y hoy los dos se ven igual (silencio).

### 6.4 Secretos fuera del YAML

Soportar `token_file: /etc/bd-agent/token` (permisos `0600`) o
`token_env: CORE_INGEST_TOKEN` además del `token:` en línea. Con
`LoadCredential=` de systemd, el token no queda legible ni siquiera para el
usuario del servicio fuera del proceso.

### 6.5 Ergonomía operativa

- `--diagnostico`: chequea config, alcance del origen, permisos, reloj, conectividad al Core y token, y escupe un informe. Lo primero que se corre cuando algo falla.
- Logs a `journald` con rotación (hoy: `agente.log` que crece para siempre).
- Códigos de salida distintos por clase de error (config / origen / red / auth), para que el monitoreo distinga.
- `--version` y versión en el heartbeat.

### 6.6 Los `.bat` de Windows

No se tiran: siguen sirviendo para granjas donde no haya gateway todavía, y para
correr on-demand. Pero se les agrega, en `instalar_tarea.bat`:

- `/RI 60 /DU 24:00` → repetición cada hora en vez de un disparo diario único.
- `StartWhenAvailable` (vía XML o `Set-ScheduledTask`) → recupera la corrida perdida.
- `/RU SYSTEM` → corre sin usuario logueado (mata el punto #2 del diagnóstico).

## 7. Cambios necesarios en el Core

El gateway no sirve de nada si del otro lado nadie se da cuenta de que una
granja calló. **La detección tiene que ser por ausencia, y vive en el Core.**

1. **Dead-man's switch.** Un job cada 10 min: sin heartbeat >30 min → `amarillo`; >2 h → `rojo` + alerta. Sin dato de ayer a las 08:00 → alerta.
2. **Canal de alerta que llegue al teléfono.** Bot de Telegram (gratis, 20 minutos de trabajo) + email como respaldo. Regla de oro: **una alerta por incidente, no una por ciclo**, o en dos semanas las ignorás.
3. **Ingest idempotente de verdad.** `UPSERT` por `(granja, galpon, ciclo, metrica, fecha_dato)`. Ya es necesario hoy por el reenvío total; con el spool pasa a ser el contrato explícito. Responder qué claves se aceptaron.
4. **Un token por granja**, con prefijo identificable, revocable y rotable individualmente. Guardado hasheado. El token dice de qué granja es: una granja no puede escribir datos de otra.
5. **Chequeo de completitud, no solo de presencia.** "Esperaba 8 galpones × 12 métricas para el 2026-08-11, llegaron 7 galpones" es un incidente real que un heartbeat verde no detecta.
6. **Vista de flota** (`/v1/agente/estado` ya existe): una fila por granja con semáforo, último dato, versión del agente, pendientes en cola. Es el tablero que mirás a la mañana.

## 8. Seguridad

| Frente | Decisión |
|---|---|
| Superficie de red | **Cero puertos entrantes** en la granja. Todo sale hacia afuera (Core por HTTPS, Tailscale). |
| Acceso administrativo | Tailscale SSH con claves, MFA en la cuenta, ACL por granja. Sin contraseñas. |
| Credenciales | Token por granja, en archivo `0600` o `LoadCredential` de systemd. Nunca en git (el `.gitignore` ya cubre `config.yaml`). Rotación sin redeploy. |
| Acceso al origen | Montaje SMB **read-only** con usuario dedicado. El gateway no puede escribir sobre los datos de la granja. |
| Transporte | HTTPS con verificación de certificado (hoy `urllib` ya verifica por defecto — mantenerlo así, nunca `ssl._create_unverified_context`). |
| Máquina | Sin escritorio, sin usuarios extra, `unattended-upgrades`, firewall por defecto denegando entrante. |
| Trazabilidad | El Core registra qué granja/token escribió qué y cuándo. |
| Superficie física | La granja es un lugar con gente entrando y saliendo: el gateway va en el rack/gabinete cerrado, no arriba de un escritorio. |

## 9. Escalar a N granjas

Lo que hace que la granja #2 tarde una tarde y no una semana:

1. **Una imagen, no una instalación.** Un script `provisionar.sh --granja <id> --token <t> --origen <ruta>` que deja el gateway listo: paquetes, servicio, timers, Tailscale, config. Idealmente sobre una imagen base ya armada, para clonar el SSD.
2. **Config declarativa por granja.** Todo lo específico vive en `config.yaml`: `granja`, `origen`, `zona_horaria`, token. El código es idéntico en las 20 granjas.
3. **Enrolamiento contra el Core.** El script llama a `/v1/granjas/enrolar` y recibe su token: nadie copia y pega secretos a mano.
4. **Versión reportada en el heartbeat** + canal de actualización (`git pull` de un tag firmado + `systemctl restart`, disparado por vos vía Tailscale). Primero manual y explícito; auto-update recién cuando la flota sea grande.
5. **Versionado del esquema de config**, para que un agente viejo con config nueva falle claro y no de forma rara.
6. **Un runbook por síntoma** (sección 11) que pueda ejecutar otra persona.

## 10. Plan por fases

### Fase 0 — Gratis, esta semana, sin comprar nada
- Tarea programada: `/RU SYSTEM` + repetición horaria + `StartWhenAvailable`.
- BIOS de la PC de BD-Copy: encendido automático tras corte de luz.
- Dead-man's switch + alerta a Telegram en el Core.

**Ya con esto** los modos de falla más frecuentes quedan cubiertos, y sobre todo: **te enterás**. Es el mayor retorno por hora invertida de todo el documento.

### Fase 1 — El gateway (2-3 semanas)
- Comprar mini PC + UPS + cable.
- Debian + Tailscale + share SMB read-only.
- Spool SQLite + reintentos + heartbeat enriquecido en el agente.
- Correr **en paralelo** con la PC actual durante 1 semana y comparar que ambos entreguen lo mismo.
- Apagar el agente de la PC operativa.

**Criterio de aceptación:** durante 7 días corridos, sin que nadie toque nada, el Core tiene el dato del día anterior de todos los galpones antes de las 08:00; y cuando desenchufo el gateway a propósito, me llega una alerta en menos de 2 horas y al reconectarlo se pone al día solo, sin perder un registro.

### Fase 2 — Endurecer (1-2 semanas)
- Tokens por granja + rotación.
- Chequeo de completitud por galpón/métrica.
- `--diagnostico` + runbook.
- Vista de flota.

### Fase 3 — Replicar
- Imagen + `provisionar.sh` + enrolamiento.
- Granja #2 como prueba real del proceso: si tarda más de una tarde, el proceso todavía no está listo.
- Recién ahí: LTE de respaldo donde haga falta.

### Fase 4 — Ambición
Saltear BD-Copy y leer directo de los controladores Big Dutchman
(BigFarmNet: API / base de datos / OPC-UA, según licencia). Elimina de raíz la
dependencia de una PC Windows con un usuario logueado. Vale la conversación con
Big Dutchman antes de la granja #5 — a esa escala, el costo de mantener PCs
Windows en cada granja supera al de la licencia.

## 11. Runbook (qué mirar cuando algo falla)

| Síntoma en el tablero | Causa probable | Acción |
|---|---|---|
| Sin heartbeat, granja en rojo | Gateway apagado / sin luz / sin internet | Tailscale → si no responde, llamar a la granja para revisar UPS y router |
| Heartbeat OK, `fuente_frescura_seg` alto | La PC de BD-Copy está apagada o BD-Copy no exporta | `wakeonlan` desde el gateway; si vuelve, revisar que BD-Copy arranque solo |
| Heartbeat OK, `pendientes_en_cola` creciendo | El Core rechaza (token, esquema, 5xx) | Ver `ultimo_error` del spool; los datos **no** se pierden, se drenan al arreglar |
| Falta un galpón | Nave apagada, renombrada, o carpeta que dejó de matchear `MANBD_Plc*_House*` | `--diagnostico` y revisar `parse_nombre_nave` |
| Métrica nueva sin mapear | Centinela ya lo avisa en el log | Agregar a `CANONICAS` en `bd_agent/metricas.py` |
| Todo verde pero el número está mal | Cambió el archivo fuente río arriba | Comparar contra el CSV crudo; revisar prioridad de fuentes en `CANONICAS` |

## 12. Resumen ejecutivo

- **Comprá:** mini PC fanless N100 + UPS 700-900 VA + cable de red. ~US$ 300.
- **Instalá:** Debian + Tailscale + montaje SMB read-only de la carpeta de CSV.
- **Cambiá en el agente:** cola SQLite (nunca se pierde un dato), reintentos con backoff, corrida cada 15 min en vez de 1 vez por día, heartbeat cada 5 min con estado real.
- **Cambiá en el Core:** alerta por ausencia de latido (dead-man's switch), ingest idempotente, un token por granja.
- **Hacé primero lo gratis:** `/RU SYSTEM` + repetición horaria en la tarea programada, encendido automático en el BIOS, y alerta a Telegram. Es una tarde de trabajo y cubre la mayoría de las caídas de hoy.

El resultado: la única forma de no tener el dato de un día pasa a ser que la
granja entera esté sin luz **y** sin internet durante más de 24 horas seguidas —
y aun en ese caso, cuando vuelve, el dato se recupera solo y completo.

# Agente BD-Copy

Agente que lee los CSV exportados por BD-Copy (Big Dutchman), los normaliza
a un modelo canonico y los entrega a un destino (archivo local o API).
Disenado para instalarse identico en multiples granjas.

## Como funciona (v3)

    leer CSV  ->  encolar en disco (SQLite)  ->  drenar contra el Core

La cola es lo importante: si el Core esta caido o no hay internet, los
registros quedan guardados y se entregan en la proxima corrida. **Una corrida
fallida cuesta minutos, no un dia de datos.**

Cada registro tiene una `clave` deterministica —
`sha256(granja|galpon|ciclo|metrica|fecha_dato)`— que viaja al Core y le sirve
como identidad para hacer UPSERT. Lo ya confirmado no se vuelve a mandar, salvo
que cambie el valor rio arriba.

Unica dependencia: **pyyaml**. Los CSV se leen con la stdlib.

## Instalacion

### A) Windows con el .exe (lo mas simple)

No necesita Python instalado. Un solo archivo de ~10 MB.

1. Bajar `agente-bdcopy.exe` de la ultima Release y copiarlo a `C:\farmapi\`.
2. Doble clic en `configurar.bat` — o:

       agente-bdcopy.exe --config config.yaml --configurar

   Busca sola la carpeta de BD-Copy, y si no la encuentra abre el **explorador
   de carpetas** de Windows. Antes de guardar, **lee los archivos de verdad** y
   muestra cuantos galpones, que metricas y de que fecha encontro. Si la carpeta
   no sirve, lo dice y pide otra.
3. Editar `config.yaml`: `granja` y el token.
4. Verificar:

       agente-bdcopy.exe --config config.yaml --diagnostico

5. Instalar la tarea programada (clic derecho -> **Ejecutar como administrador**):

       scripts\instalar_tarea.bat

Corre **cada hora**, como **SYSTEM** (no necesita usuario logueado) y con
`StartWhenAvailable`: si la PC estuvo apagada, recupera la corrida apenas
enciende.

> Si la carpeta de BD-Copy esta en OneDrive, marcarla como **"Conservar siempre
> en este dispositivo"**. SYSTEM no puede bajar archivos que quedaron solo en la
> nube.

### B) Gateway Linux (recomendado para produccion)

    sudo ./scripts/instalar_gateway.sh \
        --granja astillas_de_plata \
        --url https://core.flowkore.com/ingest \
        --origen /mnt/bdcopy/csv \
        --token-file /root/token-astillas.txt

Deja instalados dos timers de systemd:

| Timer | Cada | Que hace |
|---|---|---|
| `bd-agent.timer` | 15 min | ciclo de datos, con `Persistent=true` (recupera corridas perdidas) |
| `bd-agent-heartbeat.timer` | 5 min | latido al Core, aunque no haya datos nuevos |

Falta a mano, una sola vez: montar la carpeta de CSV por SMB **en solo lectura**
e instalar Tailscale (`tailscale up --ssh`). El script imprime ambos comandos.

### C) Desde el codigo

    pip install -r requirements.txt
    copy bd_agent\config_ejemplo.yaml config.yaml
    python -m bd_agent.agente --config config.yaml --configurar

## Comandos

| Comando | Para que |
|---|---|
| `--configurar` | asistente para elegir la carpeta de BD-Copy (con explorador) |
| `--once` | una corrida (lo que usan systemd y la tarea programada) |
| `--diagnostico` | revisa config, origen, frescura de los CSV, cola, Core, token y reloj |
| `--explorar` | lista que datos hay en los CSV: mapeados y **disponibles sin mapear** |
| `--solo-heartbeat` | reporta estado al Core sin leer los CSV |
| `--reenviar-desde AAAA-MM-DD` | vuelve a encolar lo ya confirmado desde esa fecha |
| `--actualizar` | busca version nueva, la verifica y se reemplaza |
| `--actualizar --revisar` | solo informa si hay version nueva |
| `--revertir` | vuelve a la version anterior guardada |
| `--version` | version del agente (tambien viaja en cada latido) |

Codigos de salida: `0` ok · `2` config · `3` origen (CSV) · `4` red/Core · `5` token.

## Auto-actualizacion (pull)

El Agente le pregunta al Core si hay version nueva y se actualiza solo. Nadie
en la granja tiene que hacer nada.

**Por que pull y no push:** mandarle el archivo a alguien para que lo ejecute
reintroduce justo la dependencia que este proyecto viene a eliminar — que una
persona en la granja haga algo. Con pull, se publica una version y la flota
converge sola.

**Por que el canal es el Core y no GitHub:** el Core ya sabe que version tiene
cada granja, asi que permite **canario de verdad** (actualizar una granja
primero) y fijar la version de una granja sin tocar nada en la granja.

Antes de reemplazar nada se verifica, en este orden:

1. la URL es HTTPS y el manifiesto declara `sha256`
2. lo bajado no supera el tope de tamano
3. el `sha256` de lo bajado coincide
4. lo bajado **arranca** y reporta la version esperada

Recien ahi se corre el ejecutable actual a `.viejo` y se pone el nuevo. Si el
nuevo no arranca **se revierte solo**. `--revertir` es el boton de panico.

Nunca baja de version, salvo que el Core mande `version_fijada` — que es como
se revierte una flota entera sin entrar a ninguna granja.

    actualizacion:
      modo: "manual"          # manual | automatica
      # version_fijada: ""    # clava esta granja en una version
      max_mb: 60

Arranca en `manual`: el timer consulta pero no instala. Se pasa a `automatica`
en **una** granja (canario), se mira 24 h en el tablero, y recien despues en el
resto. El resultado de la ultima actualizacion viaja en el latido, asi que una
granja trabada se ve desde el tablero.

En Linux corre `bd-agent-update.timer` (03:30, con una hora de dispersion); en
Windows, la tarea `AgenteBDCopy-Update`.

## El token

No hace falta que este en el `config.yaml`. Se resuelve en este orden:

1. `destino.token_file` — ruta a un archivo (`0600`). Con systemd:
   `token_file: "${CREDENTIALS_DIRECTORY}/core_token"`
2. `destino.token_env` — nombre de una variable de entorno
3. `destino.token` — en linea (para probar)

## Que reporta el latido

Ademas de `ok` / `registros` / `mensaje`: `version_agente`, `hostname`,
`pendientes_en_cola`, `ultimo_dato_fecha`, `galpones_vistos`, `disco_libre_mb`,
`ultimo_error` y **`fuente_frescura_seg`** (antiguedad del CSV mas nuevo).

Ese ultimo campo distingue dos fallas que si no se ven iguales (silencio):

    no llega ningun latido          -> el gateway se cayo
    latido con frescura alta        -> el gateway esta bien, BD-Copy no exporta

Y `galpones_vistos == 0` con la carpeta existiendo significa que la ruta apunta
al lugar equivocado — distinto de "hoy no hubo datos nuevos", y con su alerta.

La deteccion de "esta granja se murio" vive en el Core (dead-man's switch): si
el agente no corre, no hay latido que avisarlo.

## Agregar una metrica

Primero ver que hay disponible en esa granja:

    agente-bdcopy.exe --config config.yaml --explorar   (o scripts\explorar.bat)

Lista los CSV que el agente ya levanta y, sobre todo, los que traen datos y
todavia no se mapean, con sus columnas, rango de fechas y un valor de muestra.

- Si el archivo tiene columna `NUM` (la forma habitual), agregarlo es una linea
  en `bd_agent/metricas.py` -> diccionario `CANONICAS`.
- Si es un archivo "ancho" de clima (una columna por sensor y una fila por
  hora, como `MANPRODUCTION_AVG/MIN/MAX.csv`), va en el diccionario `CLIMA`
  del mismo archivo: se declara que columnas leer y como agregar el dia
  (`prom`/`min`/`max`). El parser lo reduce a un valor por dia cerrado.
  Asi se levantan hoy temperatura (promedio, minima, maxima y exterior),
  humedad, CO2, amoniaco, presion negativa y velocidad de aire.
- Si trae otra forma distinta, no alcanza con mapearlo: hay que extender el
  parser. `--explorar` lo avisa explicitamente.

## Estructura del dato normalizado

    granja, galpon, ciclo, metrica, fecha_dato, hora_cierre,
    zona_horaria, capturado_en, valor, edad_dia, semana, fuente, clave

## Desarrollo

    pip install -r requirements-dev.txt
    python -m pytest tests/ -q

### Construir el .exe

PyInstaller **no compila cruzado**: el `.exe` hay que construirlo en Windows.

- En una PC Windows: `powershell -File scripts\construir_exe.ps1`
- Sin PC Windows: pushear un tag `v*` y lo construye
  `.github/workflows/exe.yml` en un runner de GitHub, publicando el `.exe` en
  una Release.

La receta es `agente-bdcopy.spec`. Los `excludes` de ahi son los que mantienen
el ejecutable en ~10 MB: sin ellos PyInstaller empaqueta cualquier libreria
pesada que encuentre instalada en la maquina que construye.

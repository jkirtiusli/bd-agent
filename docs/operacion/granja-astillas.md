# Granja Astillas de Plata: como esta instalado

Estado al 2026-10-23, despues de mudar todo a una PC nueva.

## Flujo de datos

    amacs (MySQL 5.5, 192.168.25.200:3306, una base por galpon/ciclo)
      | LAN, BD-Copy lee las tablas PRODUCTION_* — todos los dias 22:00
      v
    PC de la granja: CSV en C:\Users\Usuario\Documents\BDCopy\BackUp\csv
      | agente-bdcopy.exe cada hora (cola local spool.db)
      v  HTTPS saliente
    Core: https://core.flowkore.com/ingest  (+ latido cada 5 min)

Nada entra a la granja: el agente solo hace conexiones salientes. El acceso
remoto es por Tailscale + SSH.

## La PC (nueva, desde 2026-10-23)

| Que | Valor |
|---|---|
| Tailscale / SSH | `pc-astillas-2` — `ssh Usuario@pc-astillas-2` |
| Windows | 11 Pro, hostname `Server`, usuario `Usuario` (admin) |
| IP hacia amacs | `192.168.25.248/24` fija (ver "IP duplicada") |
| IP LAN general | `192.168.10.252/24`, gateway `192.168.10.1` |
| Zona horaria | Argentina Standard Time |
| Agente | `C:\farmapi` (`agente-bdcopy.exe`, `config.yaml`, `spool.db`, `agente.log`) |
| BD-Copy | `C:\Program Files (x86)\Big Dutchman International GmbH\BD-Copy\` |
| Config BD-Copy | `%LOCALAPPDATA%\BD-Copy\config\` (la de fabrica quedo en `_fabrica\`) |

La PC **no es nuestra**. Entro a Tailscale como dispositivo con tag
`tag:granja` (auth key de un solo uso, `--unattended`), no con una cuenta
personal. Para sacarla: borrarla en el admin de Tailscale y borrar
`C:\ProgramData\ssh\administrators_authorized_keys`.

### Tareas programadas

| Tarea | Como corre | Que hace |
|---|---|---|
| `BDcopy` | al iniciar sesion de `Usuario` (+1 min), 3 reintentos | abre BD-Copy |
| `AgenteBDCopy` | cada hora + al arrancar, SYSTEM | `scripts\correr_agente.bat --tarea` |
| `AgenteBDCopy-Heartbeat` | cada 5 min, SYSTEM | `--solo-heartbeat` |
| `AgenteBDCopy-Update` | diaria 03:14, SYSTEM | `--actualizar --desatendido` (modo manual) |
| `DatosGranja` | diaria 02:00 (hora AR), 3 reintentos, SYSTEM | `powershell -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\datos-granja\bajar_datos_granja.ps1`: baja el paquete de datos (ver "Paquete de datos de la granja") |

`instalar_tarea.ps1` crea `AgenteBDCopy` y `-Update`; el latido se crea a
mano (ver el procedimiento de mudanza).

## Paquete de datos de la granja

Copia abierta de los datos del Core para que la granja los tenga sin
depender de nosotros. **No esta cifrada**: quien entre a la PC puede abrirla.
El backup cifrado completo queda solo en Hetzner (core_v1:
`deploy/vps-scripts/backup-storagebox.sh`) y nunca baja a esta PC.

Flujo:

    VPS (core_v1: deploy/vps-scripts/paquete-granja.sh, cron 04:20 UTC)
      | arma datos.sql.gz, csv.zip, LEEME.txt, MANIFIESTO.txt
      v  carpeta AAAA-MM-DD_HHMMSS en el Storage Box de Hetzner
    PC de la granja: tarea DatosGranja (SYSTEM) -> C:\datos-granja\<carpeta>
      SFTP puerto 23, solo lectura; las 7 carpetas mas nuevas

| Que | Valor |
|---|---|
| Script | `scripts\bajar_datos_granja.ps1` (parametros: `-Usuario`, `-Servidor`, `-Destino`, `-Clave`, `-Copias 7`) |
| Carpeta | `C:\datos-granja` (una subcarpeta por paquete, `bajada.log`) |
| Tarea | `DatosGranja`, diaria 02:00 hora AR, 3 reintentos, SYSTEM. Accion: `powershell -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\datos-granja\bajar_datos_granja.ps1` |
| Instalado en | `C:\ProgramData\datos-granja\bajar_datos_granja.ps1` (copia de `scripts\bajar_datos_granja.ps1`; hay que recopiarla si el script cambia) |
| Subcuenta | `u607407-sub1` del Storage Box, base `granja-datos`, Read-Only, SSH habilitado, "External reachability" activada |
| Clave SSH | `C:\ProgramData\datos-granja\id_ed25519` (propia de esta PC), con `known_hosts` al lado |
| Ping | URL en `C:\ProgramData\datos-granja\ping-url.txt` (una linea) |

Cada paquete baja a `_parcial` y solo pasa a su carpeta con fecha si
`MANIFIESTO.txt` es valido, trae `datos.sql.gz`, `csv.zip` y `LEEME.txt`, cada
archivo coincide en tamano y SHA256 y no sobra ningun otro. Una carpeta con
fecha es siempre un paquete verificado. Si algo falla no queda nada en una
carpeta con fecha, se escribe `FALLO` en el log y la tarea sale con 1.

Horario: 02:00 AR son las 05:00 UTC, despues del cron de las 04:20 UTC que
arma el paquete. No poner la tarea antes de eso: bajaria en silencio el
paquete del dia anterior.

Instalacion (como administrador, en la PC):

- La clave privada tiene que ser del dueno SYSTEM y solo SYSTEM y
  Administradores pueden tocarla, o ssh la rechaza al correr como SYSTEM:

      icacls C:\ProgramData\datos-granja\id_ed25519 /setowner *S-1-5-18
      icacls C:\ProgramData\datos-granja\id_ed25519 /inheritance:r /grant *S-1-5-18:F /grant *S-1-5-32-544:F

- El script crea `C:\datos-granja` pero no le pone permisos. Para que la
  granja lea los datos con su usuario (grupo Usuarios):

      icacls C:\datos-granja /grant "*S-1-5-32-545:(OI)(CI)RX"

- `ping-url.txt`: la URL del check (se le quitan espacios y la `/` final). Se
  llama a `<url>` si salio bien y a `<url>/fail` si fallo. La URL no se escribe
  en el log. Si el archivo falta o el ping falla, queda una linea en el log y
  el resultado de la tarea no cambia.

Como revisar:

    ssh Usuario@pc-astillas-2
    powershell -Command "Get-Content C:\datos-granja\bajada.log -Tail 5; Get-ScheduledTaskInfo DatosGranja | ft LastRunTime,LastTaskResult"

La ultima linea del log tiene que ser `=== OK: ...` (si fue `FALLO: ...`, dice
por que) y el ultimo resultado de la tarea 0.

Como revocar: en el panel de Hetzner, borrar la subcuenta `u607407-sub1` (o
sacarle la clave de `.ssh/authorized_keys`); en la PC, deshabilitar la tarea
(`Disable-ScheduledTask DatosGranja`) y borrar `C:\ProgramData\datos-granja`.
Lo ya bajado en `C:\datos-granja` queda hasta que se borre a mano.

## Puntos debiles conocidos

- **BD-Copy solo exporta si esta abierto**, o sea con la sesion de Windows
  iniciada. Si la PC se reinicia y nadie inicia sesion, no hay CSV nuevos. El
  agente sigue corriendo y lo delata: diagnostico con aviso de frescura, y en
  el latido `fuente_frescura_seg` alto. Remedio: inicio de sesion automatico
  (`netplwiz`, lo tiene que hacer alguien de la granja con la contraseña).
  **Pendiente al 2026-10-23.**
- **IP duplicada**: la `192.168.25.252` que traia la PC nueva la usa otro
  equipo. Windows la marco `Duplicate` y dejo de rutear a `192.168.25.0/24`.
  Sintoma: `Test-NetConnection 192.168.25.200 -Port 3306` da False. Revisar con
  `Get-NetIPAddress -InterfaceAlias Ethernet` (columna `AddressState`). De la
  `.25.97` a la `.110` estan casi todas ocupadas; de la `.242` a la `.251`
  estaban libres.
- El aviso `MS-Excel version missing` en `bd-copy.log` al arrancar es normal.

## Verificar que todo anda

    ssh Usuario@pc-astillas-2
    cd C:\farmapi
    agente-bdcopy.exe --config config.yaml --diagnostico
    powershell -Command "Get-ScheduledTask AgenteBDCopy*,BDcopy | Get-ScheduledTaskInfo | ft TaskName,LastRunTime,LastTaskResult,NextRunTime"

Log de BD-Copy: `%LOCALAPPDATA%\BD-Copy\Log\bd-copy.log` (una linea por base
copiada a las 22:00).

## PC vieja (retirada)

`ssh PC@pc-astillas` (DESKTOP-2KSJUNT, 192.168.25.96). Exportaba a OneDrive
(`C:\Users\PC\OneDrive\Escritorio\BDCopy`). BD-Copy se cayo el 2026-10-20 y no
volvio a exportar (ultimo CSV: 2026-10-19 22:02). Las 3 tareas del agente
quedaron **deshabilitadas, no borradas**; para volver atras:

    Get-ScheduledTask AgenteBDCopy* | Enable-ScheduledTask

## Procedimiento de mudanza a otra PC (como se hizo el 2026-10-23)

1. **Acceso**: por AnyDesk, PowerShell como administrador en la PC nueva:
   OpenSSH Server (`Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`,
   servicio `sshd` automatico), clave publica en
   `C:\ProgramData\ssh\administrators_authorized_keys` (permisos solo
   Administradores y SYSTEM), y Tailscale con auth key etiquetada:
   `tailscale up --authkey ... --unattended --hostname <nombre>`.
2. **Red y hora**: que llegue a `192.168.25.200:3306` y que la zona horaria
   sea Argentina (el job de las 22:00 usa la hora local).
3. **BD-Copy**: instalarlo (el instalador queda cacheado en
   `%LOCALAPPDATA%\Caphyon\Advanced Installer\{1DE4A81E-...}\BD-Copy-Setup.exe`
   de la PC vieja), cerrarlo, copiar los `*.xml` de config de la vieja y cambiar
   `destinationPath` en `job-config.xml` a la carpeta nueva. Conservar el BOM
   UTF-8 del XML. Crear la tarea `BDcopy` al iniciar sesion.
4. **Agente**: copiar `agente-bdcopy.exe`, `config.yaml`, `scripts\` y
   **`spool.db`** (sin la cola reenvia todo el historico; verificar hash).
   Corregir `ruta_csv`. Ojo: `Set-Content -Encoding UTF8` de PowerShell 5 pone
   BOM; reescribir sin BOM. `--diagnostico`, `instalar_tarea.ps1`, y el latido:

       $a = New-ScheduledTaskAction -Execute 'C:\farmapi\agente-bdcopy.exe' -Argument '--config C:\farmapi\config.yaml --solo-heartbeat' -WorkingDirectory 'C:\farmapi'
       $t = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)
       $p = New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest
       Register-ScheduledTask -TaskName AgenteBDCopy-Heartbeat -Action $a -Trigger $t -Principal $p

5. **Corte**: deshabilitar las tareas del agente en la PC vieja, para que no
   haya dos latidos de la misma granja.
6. **Limpiar** las copias de `config.yaml` (tiene el token) de donde se hayan
   usado como transito.

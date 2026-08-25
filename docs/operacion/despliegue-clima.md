# Runbook: despliegue del clima horario en una granja

Estado al 2026-08-25 (granja **astillas_de_plata**, host SSH `PC@pc-astillas`,
instalacion en `C:\farmapi`): el exe con clima horario ya esta instalado y
`--explorar` muestra AVG/MIN/MAX mapeados. `clima_desde` quedo en `2026-04-20`.
**Pendiente antes de la primera corrida con clima: que el Core tenga la columna
`hora`** (ver "Orden de despliegue").

## Orden de despliegue (importa)

1. **Primero el Core**: columna `hora` nullable y clave natural
   `(granja, galpon, ciclo, metrica, fecha_dato, hora)`. Ver
   `docs/arquitectura/prompt-core.md`. Si el agente manda clima contra un Core
   sin `hora`, las horas de un mismo dia se pisan entre si en el UPSERT
   (quedaria solo la ultima hora). Si eso paso, se arregla en el Core y despues
   `agente-bdcopy.exe --config config.yaml --reenviar-desde AAAA-MM-DD`.
2. Despues el exe nuevo en la granja.
3. Al final, correr `clima_desde` hacia atras para traer mas historico.

## Actualizar el exe por SSH (la PC no tiene git)

1. Bajar el artifact `agente-bdcopy-exe` del build de GitHub Actions con el
   navegador. Es un ZIP: descomprimirlo (adentro esta `agente-bdcopy.exe`).
2. Desde la maquina local (NO desde la sesion SSH), en la carpeta del exe:

       scp agente-bdcopy.exe PC@pc-astillas:C:/farmapi/agente-bdcopy.exe.nuevo

   Se copia como `.nuevo` porque el exe en uso esta bloqueado por Windows y
   asi ademas queda el viejo para volver atras.
3. En la sesion SSH (CMD):

       certutil -hashfile C:\farmapi\agente-bdcopy.exe.nuevo SHA256
       cd C:\farmapi
       move agente-bdcopy.exe agente-bdcopy.exe.viejo
       move agente-bdcopy.exe.nuevo agente-bdcopy.exe
       agente-bdcopy.exe --config config.yaml --version
       agente-bdcopy.exe --config config.yaml --explorar

   `--version` no distingue builds si no cambio el numero: la prueba de que es
   el build con clima es que `--explorar` muestre `MANPRODUCTION_AVG.csv ->
   temperatura, ...` en YA MAPEADOS. Para volver atras: `move` inverso con el
   `.viejo`.

## Editar config.yaml por SSH (sin editor de texto)

La sesion SSH cae en CMD, sin editor. Opciones:

- **Ver**: `type C:\farmapi\config.yaml`
- **Agregar una linea** (OJO: `>>` DOBLE agrega; `>` simple PISA el archivo
  entero). Si el archivo no termina en salto de linea, el echo se pega a la
  ultima linea y rompe el YAML — paso el 2026-08-25; verificar con `type`
  despues de cada cambio y correr `--diagnostico`:

      echo clima_desde: 2026-04-20>> C:\farmapi\config.yaml

- **Sacar una linea exacta**:

      powershell -Command "(Get-Content C:\farmapi\config.yaml) | Where-Object { $_ -ne 'clima_desde: 2026-08-20' } | Set-Content C:\farmapi\config.yaml"

- **Reemplazar texto en una linea**:

      powershell -Command "(Get-Content C:\farmapi\config.yaml) -replace 'clima_desde: 2026-04-20','clima_desde: 2025-01-01' | Set-Content C:\farmapi\config.yaml"

- **Para ediciones grandes**, ida y vuelta con scp desde la maquina local:

      scp PC@pc-astillas:C:/farmapi/config.yaml .
      (editar local)
      scp config.yaml PC@pc-astillas:C:/farmapi/config.yaml

Siempre cerrar con:

    agente-bdcopy.exe --config config.yaml --diagnostico

## clima_desde: cuanto pesa el historico

`clima_desde` (clave de nivel superior, sin sangria, fecha AAAA-MM-DD) limita
desde que fecha se encola el clima horario. No afecta a las metricas diarias.
Un registro pesa ~420 bytes de JSON; el spool guarda cada uno dos veces
(pendientes + confirmados). En esta granja (~50 galpones, 9 metricas, 24 hs):

| clima_desde          | registros | JSON a transferir | spool.db aprox |
|----------------------|-----------|-------------------|----------------|
| (sin limite, 2021->) | ~5 M      | ~2,1 GB           | ~4,8 GB        |
| 2025-01-01           | ~1,5 M    | ~0,6 GB           | ~1,5 GB        |
| 2026-04-20 (actual)  | ~1,3 M    | ~0,6 GB           | ~1,3 GB        |
| ultimos dias         | ~40 mil   | ~17 MB            | ~40 MB         |

Correr la fecha hacia atras encola solo la diferencia: lo ya confirmado no se
reenvia. Para frenar el clima sin tocar nada mas, poner una fecha futura.

## Corrida manual y verificacion

    cd C:\farmapi
    agente-bdcopy.exe --config config.yaml --once

Es la misma corrida que dispara la tarea programada cada hora. En el log:
`N registros leidos en el origen, M nuevos a entregar` y despues cuantos se
drenaron al Core. Con el historico grande la primera corrida drena un buen
rato; si se corta (red, Core caido), lo pendiente queda en la cola y sigue en
la corrida siguiente sin perder ni duplicar. Las corridas siguientes vuelven a
ser livianas (solo el dia recien cerrado).

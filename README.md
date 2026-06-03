# Agente BD-Copy

Agente que lee los CSV exportados por BD-Copy (Big Dutchman), los normaliza
a un modelo canonico y los entrega a un destino (archivo local o API).
Disenado para instalarse identico en multiples granjas.

## Instalacion en una granja

1. Clonar el repo:
       git clone <URL_DEL_REPO> C:\farmapi\agente
       cd C:\farmapi\agente

2. Instalar dependencias:
       pip install -r requirements.txt

3. Crear la config de esta granja (NO se sube al repo):
       copy bd_agent\config_ejemplo.yaml config.yaml
   Editar config.yaml: ajustar granja, zona_horaria y ruta_csv.
   Para empujar al Core, dejar destino.modo: "http" y
   destino.url: "https://core.flowkore.com/ingest" (token = el de ingesta).

4. Probar:
       python -m bd_agent.agente --config config.yaml --once

5. (Mas adelante) instalar como servicio de Windows para corrida desatendida.

## Agregar una metrica
Editar bd_agent/metricas.py -> diccionario CANONICAS.

## Estructura del dato normalizado
granja, galpon, ciclo, metrica, fecha_dato, hora_cierre,
zona_horaria, capturado_en, valor, edad_dia, semana, fuente

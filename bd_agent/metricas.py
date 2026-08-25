# -*- coding: utf-8 -*-
"""
Lista canonica de metricas: que archivos de BD-Copy mapean a que metrica limpia.
Editar AQUI para agregar/quitar metricas. El resto del agente no se toca.

Cada metrica define una lista de 'fuentes' en orden de prioridad.
El agente usa la primera fuente que traiga datos; si esa viene vacia, prueba la siguiente.
Cuando una metrica tiene variantes que MIDEN COSAS DISTINTAS (acumulado/hoy/por_ave),
se declaran como metricas separadas para que el dashboard elija.
"""

CANONICAS = {
    # huevos
    "huevos":            ["MANPRODUCTION_TODAYEGG.csv", "MANPRODUCTION_EGGCOUNTER.csv"],
    # poblacion
    "aves_vivas":        ["MANPRODUCTION_ACTUALBIRDS.csv", "MANPRODUCTION_STOREBIRDS.csv"],
    # alimento - 3 variantes (miden distinto)
    "alimento_acumulado":["MANPRODUCTION_ACTUALFEED.csv"],
    "alimento_dia":      ["MANPRODUCTION_TODAYFEED.csv"],
    "alimento_por_ave":  ["MANPRODUCTION_TODAYBIRDFEED.csv"],
    # agua - 3 variantes
    "agua_acumulado":    ["MANPRODUCTION_ACTUALWATER.csv"],
    "agua_dia":          ["MANPRODUCTION_TODAYWATER.csv"],
    "agua_por_ave":      ["MANPRODUCTION_TODAYBIRDWATER.csv"],
    # silo (stock de comida)
    "silo":              ["MANPRODUCTION_TODAYSILO.csv"],
    # peso (vacio en algunas naves, pero preparado)
    "peso":              ["MANPRODUCTION_TODAYBIRDWEIGH.csv"],
    # mortalidad (vacio en algunas naves, pero preparado)
    "mortalidad":        ["MANPRODUCTION_ACTUALDEADSTOTAL.csv", "MANPRODUCTION_TODAYDEADSTOTAL.csv",
                          "MANPRODUCTION_ACTUALDEADS.csv", "MANPRODUCTION_TODAYDEADS.csv"],
    # descartes
    "descartes":         ["MANPRODUCTION_ACTUALCULLEDTOTAL.csv", "MANPRODUCTION_TODAYCULLEDTOTAL.csv"],
}

# Clima y ventilacion: archivos "anchos" (una columna por sensor, una fila por
# hora, sin columna NUM). El parser los reduce a un valor por dia — el modelo
# canonico es diario — combinando primero los sensores de cada fila y despues
# las filas del dia con la misma operacion ('prom', 'min' o 'max').
#
# cero_es_nulo: en estos CSV un 0.0 exacto casi siempre es "sensor ausente o
# desconectado" (un galpon con aves nunca mide 0 de temperatura, humedad o CO2);
# se descarta para no arrastrar el promedio. Para la temperatura exterior y la
# presion negativa un 0 si puede ser una medicion real, y se conserva.
_SENSORES_TEMP = [f"ROOMTEMP{i}" for i in range(1, 13)]

CLIMA = {
    # temperatura interior (12 sondas por galpon)
    "temperatura":          {"archivo": "MANPRODUCTION_AVG.csv", "columnas": _SENSORES_TEMP,
                             "agregar": "prom", "cero_es_nulo": True},
    "temperatura_min":      {"archivo": "MANPRODUCTION_MIN.csv", "columnas": _SENSORES_TEMP,
                             "agregar": "min", "cero_es_nulo": True},
    "temperatura_max":      {"archivo": "MANPRODUCTION_MAX.csv", "columnas": _SENSORES_TEMP,
                             "agregar": "max", "cero_es_nulo": True},
    "temperatura_exterior": {"archivo": "MANPRODUCTION_AVG.csv", "columnas": ["EXTTEMP"],
                             "agregar": "prom", "cero_es_nulo": False},
    # ambiente
    "humedad":              {"archivo": "MANPRODUCTION_AVG.csv", "columnas": ["HUMIDITY_1", "HUMIDITY_2"],
                             "agregar": "prom", "cero_es_nulo": True},
    "co2":                  {"archivo": "MANPRODUCTION_AVG.csv", "columnas": ["CO2"],
                             "agregar": "prom", "cero_es_nulo": True},
    "amoniaco":             {"archivo": "MANPRODUCTION_AVG.csv", "columnas": ["NH3_1", "NH3_2"],
                             "agregar": "prom", "cero_es_nulo": True},
    # ventilacion (lo que miden los sensores de aire)
    "presion_negativa":     {"archivo": "MANPRODUCTION_AVG.csv", "columnas": ["NEGPRESSURE"],
                             "agregar": "prom", "cero_es_nulo": False},
    "velocidad_aire":       {"archivo": "MANPRODUCTION_AVG.csv", "columnas": ["AIRSPEED"],
                             "agregar": "prom", "cero_es_nulo": True},
}

# Todos los archivos que el agente "conoce" (para el centinela: lo que NO esta aqui y trae datos, se anota)
ARCHIVOS_CONOCIDOS = {arch for fuentes in CANONICAS.values() for arch in fuentes} \
                   | {spec["archivo"] for spec in CLIMA.values()}

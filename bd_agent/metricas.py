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

# Todos los archivos que el agente "conoce" (para el centinela: lo que NO esta aqui y trae datos, se anota)
ARCHIVOS_CONOCIDOS = {arch for fuentes in CANONICAS.values() for arch in fuentes}

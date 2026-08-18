# -*- coding: utf-8 -*-
"""
Cola local (store-and-forward) en SQLite.

Es lo que hace que el Agente sea independiente: si el Core esta caido o no hay
internet, los registros quedan encolados en disco y se drenan cuando vuelve.
Nunca se pierde un dato por un corte.

Dos tablas:
  pendientes  -> lo leido que todavia el Core no acuso recibo
  confirmados -> lo que el Core ya acepto (para no volver a mandarlo)

Cada registro tiene:
  clave      = sha256(granja|galpon|ciclo|metrica|fecha_dato)
               identidad del dato. Es la que el Core usa para hacer UPSERT.
  valor_hash = sha256 de los campos con contenido (valor, edad, semana, ...)
               si rio arriba corrigen un numero, cambia el hash y se reenvia;
               si el dato es identico, no se reenvia.

`capturado_en` queda FUERA del valor_hash a proposito: cambia en cada corrida y
haria que todo se reenviara siempre.
"""
import os
import json
import hashlib
import sqlite3
import datetime as dt

CAMPOS_CLAVE = ("granja", "galpon", "ciclo", "metrica", "fecha_dato")
CAMPOS_VALOR = ("valor", "edad_dia", "semana", "hora_cierre", "fuente")

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS pendientes (
    clave        TEXT PRIMARY KEY,
    valor_hash   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    fecha_dato   TEXT NOT NULL,
    creado_en    TEXT NOT NULL,
    intentos     INTEGER NOT NULL DEFAULT 0,
    ultimo_error TEXT
);
CREATE TABLE IF NOT EXISTS confirmados (
    clave         TEXT PRIMARY KEY,
    valor_hash    TEXT NOT NULL,
    payload       TEXT NOT NULL,
    fecha_dato    TEXT NOT NULL,
    confirmado_en TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pendientes_fecha ON pendientes(fecha_dato);
CREATE INDEX IF NOT EXISTS idx_confirmados_fecha ON confirmados(fecha_dato);
"""


def clave(registro):
    """Identidad del dato: misma metrica, mismo dia, misma nave -> misma clave."""
    crudo = "|".join(str(registro.get(c) if registro.get(c) is not None else "")
                     for c in CAMPOS_CLAVE)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def valor_hash(registro):
    """Huella del contenido. Cambia solo si cambio un valor real del dato."""
    crudo = json.dumps({c: registro.get(c) for c in CAMPOS_VALOR},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


class Spool:
    """Cola en disco. Usar como context manager o llamar a cerrar()."""

    def __init__(self, ruta):
        self.ruta = ruta
        carpeta = os.path.dirname(os.path.abspath(ruta))
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        self.con = sqlite3.connect(ruta)
        self.con.row_factory = sqlite3.Row
        # WAL: si el proceso muere a mitad de una corrida, la cola no se corrompe.
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=FULL")
        self.con.executescript(_ESQUEMA)
        self.con.commit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cerrar()

    def cerrar(self):
        self.con.close()

    # ---------- entrada ----------

    def encolar(self, registros, ahora=None):
        """
        Encola lo que el Core todavia no confirmo. Devuelve cuantos entraron.

        Se saltea lo ya confirmado con el mismo valor_hash: por eso una corrida
        que vuelve a leer todo el historico de CSV no genera trafico.
        """
        ahora = ahora or dt.datetime.now().astimezone().isoformat(timespec="seconds")
        nuevos = 0
        cur = self.con.cursor()
        for reg in registros:
            k, vh = clave(reg), valor_hash(reg)
            fila = cur.execute(
                "SELECT valor_hash FROM confirmados WHERE clave = ?", (k,)
            ).fetchone()
            if fila and fila["valor_hash"] == vh:
                continue  # ya entregado y sin cambios
            # El Core recibe la clave: le sirve de identidad para el UPSERT.
            payload = dict(reg, clave=k)
            cur.execute(
                """INSERT INTO pendientes
                       (clave, valor_hash, payload, fecha_dato, creado_en, intentos)
                   VALUES (?, ?, ?, ?, ?, 0)
                   ON CONFLICT(clave) DO UPDATE SET
                       payload    = excluded.payload,
                       creado_en  = excluded.creado_en,
                       -- si cambio el contenido, el contador de intentos
                       -- arranca de cero: es un dato distinto
                       intentos   = CASE WHEN pendientes.valor_hash = excluded.valor_hash
                                         THEN pendientes.intentos ELSE 0 END,
                       valor_hash = excluded.valor_hash""",
                (k, vh, json.dumps(payload, ensure_ascii=False),
                 str(reg.get("fecha_dato") or ""), ahora),
            )
            nuevos += 1
        self.con.commit()
        return nuevos

    # ---------- salida ----------

    def tomar(self, limite, offset=0):
        """
        Devuelve hasta `limite` pendientes, los mas viejos primero.

        `offset` sirve para saltear los que ya fueron rechazados en esta misma
        pasada de drenaje: siguen en la cola (para poder diagnosticarlos) pero
        no deben volver a tomarse, o el drenaje giraria en falso.
        """
        filas = self.con.execute(
            "SELECT clave, valor_hash, payload FROM pendientes "
            "ORDER BY fecha_dato, clave LIMIT ? OFFSET ?", (limite, offset)
        ).fetchall()
        return [(f["clave"], f["valor_hash"], json.loads(f["payload"])) for f in filas]

    def confirmar(self, claves, ahora=None):
        """Mueve de pendientes a confirmados. Se llama por lote acusado OK."""
        if not claves:
            return 0
        ahora = ahora or dt.datetime.now().astimezone().isoformat(timespec="seconds")
        cur = self.con.cursor()
        marcas = ",".join("?" * len(claves))
        cur.execute(
            f"""INSERT INTO confirmados (clave, valor_hash, payload, fecha_dato, confirmado_en)
                SELECT clave, valor_hash, payload, fecha_dato, ?
                  FROM pendientes WHERE clave IN ({marcas})
                ON CONFLICT(clave) DO UPDATE SET
                    valor_hash    = excluded.valor_hash,
                    payload       = excluded.payload,
                    confirmado_en = excluded.confirmado_en""",
            (ahora, *claves),
        )
        cur.execute(f"DELETE FROM pendientes WHERE clave IN ({marcas})", tuple(claves))
        self.con.commit()
        return cur.rowcount

    def registrar_error(self, claves, error):
        """Suma un intento y guarda el motivo. El dato sigue en la cola."""
        if not claves:
            return
        marcas = ",".join("?" * len(claves))
        self.con.execute(
            f"UPDATE pendientes SET intentos = intentos + 1, ultimo_error = ? "
            f"WHERE clave IN ({marcas})", (str(error)[:500], *claves),
        )
        self.con.commit()

    def reencolar_desde(self, fecha):
        """Fuerza el reenvio de todo lo confirmado con fecha_dato >= fecha."""
        cur = self.con.cursor()
        cur.execute(
            """INSERT INTO pendientes
                   (clave, valor_hash, payload, fecha_dato, creado_en, intentos)
               SELECT clave, valor_hash, payload, fecha_dato, confirmado_en, 0
                 FROM confirmados WHERE fecha_dato >= ?
               ON CONFLICT(clave) DO NOTHING""", (fecha,),
        )
        n = cur.rowcount
        cur.execute("DELETE FROM confirmados WHERE fecha_dato >= ?", (fecha,))
        self.con.commit()
        return n

    # ---------- estado ----------

    def estado(self):
        """Resumen para el heartbeat y el diagnostico."""
        cur = self.con.cursor()
        pend = cur.execute("SELECT COUNT(*) c FROM pendientes").fetchone()["c"]
        conf = cur.execute("SELECT COUNT(*) c FROM confirmados").fetchone()["c"]
        ult = cur.execute(
            "SELECT MAX(fecha_dato) f FROM confirmados"
        ).fetchone()["f"]
        err = cur.execute(
            "SELECT ultimo_error FROM pendientes WHERE ultimo_error IS NOT NULL "
            "ORDER BY intentos DESC LIMIT 1"
        ).fetchone()
        max_int = cur.execute(
            "SELECT COALESCE(MAX(intentos), 0) i FROM pendientes"
        ).fetchone()["i"]
        return {
            "pendientes_en_cola": pend,
            "confirmados_total": conf,
            "ultimo_dato_fecha": ult,
            "ultimo_error": err["ultimo_error"] if err else None,
            "max_intentos": max_int,
        }

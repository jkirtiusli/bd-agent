import logging


def test_ciclo_reporta_heartbeat(monkeypatch):
    import bd_agent.agente as agente

    # parser devuelve 2 registros, destino "entrega" ok
    monkeypatch.setattr(agente.bd_parser, "escanear",
                        lambda ruta, granja, zona: ([{"x": 1}, {"x": 2}], {}))
    monkeypatch.setattr(agente.bd_destino, "entregar",
                        lambda regs, dest: f"{len(regs)} enviados")
    reportes = []
    monkeypatch.setattr(agente.salud, "reportar",
                        lambda cfg, ok, registros, mensaje:
                        reportes.append((ok, registros, mensaje)))

    cfg = {"granja": "g", "zona_horaria": "UTC", "ruta_csv": "x",
           "destino": {"modo": "http", "url": "u", "token": "t"}}
    ok, n, msg = agente.ciclo_trabajo(cfg, logging.getLogger("t"))
    assert ok is True and n == 2 and "enviados" in msg
    assert reportes == [(True, 2, msg)]


def test_ciclo_reporta_error_si_entregar_falla(monkeypatch):
    import bd_agent.agente as agente
    monkeypatch.setattr(agente.bd_parser, "escanear",
                        lambda ruta, granja, zona: ([{"x": 1}], {}))

    def falla(regs, dest):
        raise RuntimeError("core caido")
    monkeypatch.setattr(agente.bd_destino, "entregar", falla)
    reportes = []
    monkeypatch.setattr(agente.salud, "reportar",
                        lambda cfg, ok, registros, mensaje:
                        reportes.append((ok, registros, mensaje)))
    cfg = {"granja": "g", "zona_horaria": "UTC", "ruta_csv": "x",
           "destino": {"modo": "http", "url": "u", "token": "t"}}
    ok, n, msg = agente.ciclo_trabajo(cfg, logging.getLogger("t"))
    assert ok is False and "core caido" in msg
    assert reportes and reportes[0][0] is False

# -*- coding: utf-8 -*-
"""
Punto de entrada del ejecutable (PyInstaller construye el .exe desde aca).

Equivale a `python -m bd_agent.agente`, pero como archivo suelto porque
PyInstaller necesita un script, no un modulo.
"""
import sys

from bd_agent.agente import main

if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Permite `python -m bd_agent` ademas de `python -m bd_agent.agente`."""
import sys

from bd_agent.agente import main

if __name__ == "__main__":
    sys.exit(main())

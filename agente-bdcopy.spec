# -*- mode: python ; coding: utf-8 -*-
"""
Receta de PyInstaller para el ejecutable del Agente.

    pyinstaller --clean --noconfirm agente-bdcopy.spec

Se usa un .spec en vez de la linea de comandos porque hay dos cosas que hay que
declarar si o si:

1. `datas`: config_ejemplo.yaml tiene que viajar adentro del ejecutable. El
   asistente --configurar lo usa como plantilla cuando la PC todavia no tiene
   config.yaml, y lo busca al lado del modulo (que dentro del .exe es una
   carpeta temporal, no el repo).

2. `excludes`: sin esto, PyInstaller empaqueta cualquier libreria pesada que
   encuentre instalada en la maquina que construye. El Agente solo necesita
   pyyaml y la stdlib.
"""

a = Analysis(
    ["agente_bdcopy.py"],
    pathex=[],
    binaries=[],
    datas=[("bd_agent/config_ejemplo.yaml", "bd_agent")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Nada de esto se usa. Si estan instaladas en la maquina que construye,
        # PyInstaller las mete igual y el .exe pasa de ~10 MB a ~80 MB.
        "pandas", "numpy", "matplotlib", "scipy", "PIL", "IPython",
        "pytest", "_pytest", "setuptools", "pip",
        # El selector de carpeta usa el dialogo de Windows via PowerShell;
        # tkinter solo es un respaldo y pesa ~8 MB. El asistente siempre deja
        # escribir la ruta a mano, asi que no hay callejon sin salida.
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="agente-bdcopy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX dispara falsos positivos en los antivirus
    console=True,       # es una herramienta de linea de comandos
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

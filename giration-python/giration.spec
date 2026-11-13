# -*- mode: python ; coding: utf-8 -*-
# Exécutable Windows en ligne de commande (glisser un DXF sur giration.exe) : pyinstaller giration.spec
a = Analysis(
    ["lancer_giration.py"],
    pathex=["."],
    datas=[("giration/ressources/bibliotheque-vehicules.json", "giration/ressources")],
    hiddenimports=[],
    excludes=["matplotlib", "PySide6", "tkinter", "PIL", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="giration",
    console=True,
    upx=False,
    version="version_windows.txt",
)

# -*- mode: python ; coding: utf-8 -*-
# Application de bureau Windows (carte IGN, saisie assistée), en dossier : pyinstaller --noconfirm giration_bureau_dossier.spec
# (démarrage plus rapide qu'un fichier unique, et livrable en plusieurs zips de moins de 30 Mo)

# Modules Qt non utilisés : exclus pour réduire la taille (même liste que Girabase Python)
EXCLUS = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtDBus", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtConcurrent", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtUiTools", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia",
    "tkinter", "matplotlib", "PIL", "pytest", "IPython", "scipy", "pandas",
]

# Fichiers Qt inutiles pour une application de bureau en widgets. QtNetwork, le chiffrement TLS natif de Windows
# (Schannel) et le décodeur JPEG servent à la carte IGN.
DLL_INUTILES = ("opengl32sw.dll", "qt6pdf.dll", "qt6svg.dll", "qt6opengl.dll", "qt6qml.dll",
                "qt6qmlmodels.dll", "qt6quick.dll", "qt6virtualkeyboard.dll", "d3dcompiler_47.dll")


def _garder(entree):
    dest = entree[0].replace("\\", "/").lower()
    if dest.endswith(DLL_INUTILES):
        return False
    if "/translations/" in dest and not dest.endswith("qtbase_fr.qm"):
        return False
    if "/plugins/" in dest and not any(k in dest for k in ("/platforms/qwindows", "/platforms/qoffscreen",
                                                           "/styles/", "/imageformats/qico", "/imageformats/qjpeg",
                                                           "/imageformats/qpng", "/tls/qschannelbackend")):
        return False
    return True


a = Analysis(
    ["lancer_giration_gui.py"],
    pathex=["."],
    datas=[("giration/ressources/bibliotheque-vehicules.json", "giration/ressources"),
           ("giration/ressources/giration.ico", "giration/ressources"), ("LICENSE", ".")],
    hiddenimports=[],
    excludes=EXCLUS,
    noarchive=True,          # modules en fichiers : numpy, shapely, ezdxf partent dans la partie 2 « calcul »
)
a.binaries = [b for b in a.binaries if _garder(b)]
a.datas = [d for d in a.datas if _garder(d)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Giration",
    console=False,
    upx=False,
    icon="giration/ressources/giration.ico",
    version="version_windows_gui.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Giration")

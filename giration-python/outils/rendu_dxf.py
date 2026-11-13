"""Rendu d'un DXF en PNG (contrôle visuel) avec le module de dessin d'ezdxf."""
import sys
import ezdxf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy


def rendre(src, dst, dpi=200, largeur=14):
    doc = ezdxf.readfile(src)
    fig = plt.figure(figsize=(largeur, largeur * 0.7))
    ax = fig.add_axes([0, 0, 1, 1])
    ctx = RenderContext(doc)
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE)
    Frontend(ctx, MatplotlibBackend(ax), config=cfg).draw_layout(doc.modelspace(), finalize=True)
    fig.savefig(dst, dpi=dpi)
    plt.close(fig)


if __name__ == "__main__":
    rendre(sys.argv[1], sys.argv[2])

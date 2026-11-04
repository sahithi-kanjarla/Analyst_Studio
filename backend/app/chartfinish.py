"""One finishing step for every chart picture the backend draws, so the pictures are as crisp and as readable as the charts the browser draws.

The pictures are shown at about 450 to 600 px wide. A figure drawn 7 inches wide and then shrunk to that width also shrinks its text to about
6 px. So a picture is drawn at most MAX_WIDTH inches wide (the page shows it at close to its natural size) and no text is smaller than MIN_FONT
points (11 pt is 14.7 px; shown at 0.8 scale it is still above 11 px). The picture is saved at CHART_DPI, which is 2x the page's 96 px per inch,
so it stays sharp on a high-density screen and in the PDF and the Power BI pack. Heights are capped per chart type, so a compact chart stays short.
Nothing here changes a figure: only the size of the drawing and of its text.
"""
from __future__ import annotations

import matplotlib

MAX_WIDTH = 6.0           # inches
MIN_FONT = 11.0           # points
MAX_HEIGHT = 4.6          # inches, for any chart
COMPACT_HEIGHT = 3.4      # inches, for compact types (stacked share bars, small multiples of a few bars)

RC = {"font.size": MIN_FONT, "axes.labelsize": MIN_FONT, "xtick.labelsize": MIN_FONT, "ytick.labelsize": MIN_FONT,
      "legend.fontsize": MIN_FONT, "legend.title_fontsize": MIN_FONT, "axes.titlesize": 12}
matplotlib.rcParams.update(RC)


def finish(fig, max_height: float = MAX_HEIGHT) -> None:
    """Cap the picture's width and height and lift any text below MIN_FONT. Call it just before saving."""
    w, h = fig.get_size_inches()
    if w > MAX_WIDTH:
        h = h * MAX_WIDTH / w
        w = MAX_WIDTH
    fig.set_size_inches(w, min(h, max_height))
    try:
        fig.canvas.draw()               # tick labels and legend entries exist only once drawn
    except Exception:                   # a drawing problem must never lose the chart: keep the sizes as they are
        return
    for t in fig.findobj(matplotlib.text.Text):
        if t.get_text() and t.get_fontsize() < MIN_FONT:
            t.set_fontsize(MIN_FONT)

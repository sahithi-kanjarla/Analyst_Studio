"""Bar pictures drawn by the backend: no two texts overlap at card size, n is not in the picture, and more than 8 bars carry no value labels."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from app import chartfinish  # noqa: E402
from app.tools import MAX_LABELLED_BARS, ranked_bars  # noqa: E402

NAMES = ["Wholesale partner", "Social Shop", "Web Store", "Marketplace listing", "Phone orders", "Pop-up market", "Corporate gifting", "Affiliate programme",
         "Mobile app", "Subscription box", "Retail counter", "Trade fair", "Catalogue", "Reseller"]


def _overlaps(fig):
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    boxes = [(t.get_text(), t.get_window_extent(r)) for t in fig.findobj(matplotlib.text.Text) if t.get_text().strip() and t.get_visible()]
    return [(a, b) for i, (a, ba) in enumerate(boxes) for b, bb in boxes[i + 1:] if ba.overlaps(bb) and ba.width > 0 and bb.width > 0 and a != b]


@pytest.mark.parametrize("k", [3, 5, 6, 7, 8, 9, 14])
def test_no_text_overlaps_and_no_n_in_the_picture(k):
    labels = NAMES[:k]
    values = [1000.0 * (k - i) + 7 for i in range(k)]
    fig, ax = ranked_bars(labels, values, [40 + i for i in range(k)], lambda v: f"{v:,.0f}", xlabel="Sales channel", ylabel="Average revenue")
    chartfinish.finish(fig, chartfinish.MAX_HEIGHT)
    texts = [t.get_text() for t in fig.findobj(matplotlib.text.Text)]
    assert not any("n=" in t for t in texts)
    if k > MAX_LABELLED_BARS:
        assert not any(t.strip().replace(",", "").isdigit() for t in texts if t not in [str(int(x)) for x in ax.get_xticks()]), "value labels with many bars"
    bad = _overlaps(fig)
    plt.close(fig)
    assert not bad, bad[:3]

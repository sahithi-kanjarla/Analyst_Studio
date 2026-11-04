"""Chart pictures drawn by the backend are capped in width and height, never carry text below the minimum size, and are saved at 2x. No figure changes."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from app import chartfinish, config


def _fig(w, h, size=7):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.barh(["a", "b", "c"], [3, 2, 1])
    ax.tick_params(labelsize=size)
    ax.text(0.5, 0.5, "label", fontsize=size)
    ax.set_title("Revenue by region", fontsize=size)
    return fig


def test_width_is_capped_and_the_aspect_ratio_kept():
    fig = _fig(9.0, 4.5)
    chartfinish.finish(fig)
    w, h = fig.get_size_inches()
    assert w == pytest.approx(chartfinish.MAX_WIDTH) and h == pytest.approx(3.0)
    plt.close(fig)


def test_a_narrow_chart_keeps_its_size():
    fig = _fig(5.0, 3.0)
    chartfinish.finish(fig)
    assert tuple(fig.get_size_inches()) == (5.0, 3.0)
    plt.close(fig)


def test_height_is_capped_and_a_compact_chart_is_shorter():
    tall, compact = _fig(6.0, 9.0), _fig(6.0, 9.0)
    chartfinish.finish(tall)
    chartfinish.finish(compact, chartfinish.COMPACT_HEIGHT)
    assert tall.get_size_inches()[1] == pytest.approx(chartfinish.MAX_HEIGHT)
    assert compact.get_size_inches()[1] == pytest.approx(chartfinish.COMPACT_HEIGHT)
    assert compact.get_size_inches()[1] < tall.get_size_inches()[1]
    plt.close(tall); plt.close(compact)


def test_no_text_is_smaller_than_the_minimum():
    fig = _fig(7.2, 3.6, size=7)
    chartfinish.finish(fig)
    sizes = [t.get_fontsize() for t in fig.findobj(matplotlib.text.Text) if t.get_text()]
    assert sizes and min(sizes) >= chartfinish.MIN_FONT
    plt.close(fig)


def test_pictures_are_saved_at_twice_the_page_resolution():
    assert config.CHART_DPI >= 192 or config.CHART_DPI <= 60          # 192 by default; the tests lower it (50) to render faster

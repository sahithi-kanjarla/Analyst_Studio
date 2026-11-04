"""Chart drawing shares pyplot's global state, and the runner starts one thread per run: every drawing function takes one shared lock."""
import hashlib
import threading

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from app import reportcharts, tools


def _frame(seed):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"y": rng.normal(100, 15, 300), "g": rng.choice(list("ABCD"), 300), "x": rng.normal(0, 1, 300)})


def _spec(i):
    return {"id": f"spec{i}", "kind": "bars", "title": f"Chart {i}", "data": [("a", 3.0 + i), ("b", 2.0), ("c", 1.0)], "value_label": "v", "group_label": "g"}


def _digest(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def test_four_threads_drawing_at_once_give_the_same_files_as_one_after_another(tmp_path):
    jobs = {}
    solo = tmp_path / "solo"
    solo_out = {}
    for i in range(4):
        d = solo / str(i)
        d.mkdir(parents=True)
        f = tools.group_compare(_frame(i), {"charts_dir": d}, "y", "g")
        solo_out[i] = (f["text"], _digest(d / f["chart"].split("/")[-1]))
        reportcharts.draw(_spec(i), d)
    errors, results = [], {}
    barrier = threading.Barrier(4)

    def work(i):
        try:
            d = tmp_path / "par" / str(i)
            d.mkdir(parents=True)
            barrier.wait()
            for _ in range(3):
                f = tools.group_compare(_frame(i), {"charts_dir": d}, "y", "g")
                tools.distribution(_frame(i), {"charts_dir": d}, "x")
                reportcharts.draw(_spec(i), d)
            results[i] = (f["text"], _digest(d / f["chart"].split("/")[-1]))
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert not errors, errors
    assert results == solo_out                       # no thread's picture or text was mixed with another's
    assert plt.get_fignums() == []                   # every figure was closed


def test_figures_are_closed_even_when_a_drawing_function_fails(tmp_path):
    @tools.rendering
    def boom():
        plt.subplots()
        raise ValueError("x")
    try:
        boom()
    except ValueError:
        pass
    assert plt.get_fignums() == []


def test_the_lock_is_reentrant_and_the_inner_call_does_not_close_the_outer_figure():
    @tools.rendering
    def inner():
        return plt.subplots()

    @tools.rendering
    def outer():
        fig, _ = inner()
        return plt.fignum_exists(fig.number)

    assert outer() is True and plt.get_fignums() == []

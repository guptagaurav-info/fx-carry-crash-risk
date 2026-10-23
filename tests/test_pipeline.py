import numpy as np
import pandas as pd

from fxcarry.pipeline import carry_signal, long_short, run_rule, stats


def _toy():
    idx = pd.bdate_range("2020-01-01", "2020-06-30")
    P = pd.DataFrame({"A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0}, index=idx)
    D = pd.DataFrame(0.0, index=idx, columns=P.columns)
    for c, amt in zip(P.columns, [0.40, 0.30, 0.20, 0.10]):
        D.loc[D.index[::21], c] = amt
    return P, D


def test_carry_signal_uses_only_past_distributions():
    P, D = _toy(); d = P.index[80]
    base = carry_signal(P, D, [d], list(P.columns), 91)
    D2 = D.copy(); D2.loc[D2.index > d, "D"] = 50.0          # a huge future payment must not matter
    after = carry_signal(P, D2, [d], list(P.columns), 91)
    pd.testing.assert_frame_equal(base, after)


def test_long_short_is_dollar_neutral_and_ranks_by_yield():
    sig = pd.DataFrame({"A": [0.05], "B": [0.03], "C": [0.01], "D": [0.00]}, index=[pd.Timestamp("2020-01-31")])
    w = long_short(sig, 1).iloc[0]
    assert w["A"] == 1.0 and w["D"] == -1.0 and abs(w.sum()) < 1e-12


def test_costs_reduce_returns_by_turnover():
    w = pd.DataFrame({"A": [1.0, 1.0], "B": [-1.0, -1.0]})
    R = pd.DataFrame({"A": [0.01, 0.01], "B": [0.0, 0.0]})
    r, to = run_rule(w, R, 0.001)
    assert np.isclose(r.iloc[0], 0.01 - 2 * 0.001) and np.isclose(r.iloc[1], 0.01)


def test_stats_drawdown():
    s = stats(pd.Series([0.1, -0.5, 0.1], index=pd.date_range("2020-01-31", periods=3, freq="ME")))
    assert np.isclose(s["max_drawdown"], -0.5)

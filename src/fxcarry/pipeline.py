"""FX carry on currencies an investor could actually hold: signals, rules, metrics and charts.

Every signal at a month-end uses only prices and distributions dated on or before that month-end.
The position is then held over the next month. Returns are total returns of the currency trusts
(spot move vs USD + deposit interest paid out, net of the trusts' fees).
"""
import json
from pathlib import Path

import duckdb
import matplotlib
import numpy as np
import pandas as pd
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MultipleLocator, PercentFormatter  # noqa: E402

NAVY, GOLD, GREY, RED, BLUE = "#1F3A5F", "#9A7F2E", "#8A8880", "#B5473A", "#3A6A9A"


# ---------------------------------------------------------------- data
def load(cfg):
    raw = Path(cfg["raw_dir"]); syms = list(cfg["currencies"]) + ["SPY"]
    q = "select symbol, report_date d, close from read_parquet(?) where symbol in ({})".format(",".join(f"'{s}'" for s in syms))
    p = duckdb.execute(q, [str(raw / "prices.parquet")]).df()
    p["d"] = pd.to_datetime(p["d"])
    P = p.pivot(index="d", columns="symbol", values="close").sort_index().ffill()
    q = "select symbol, report_date d, amount from read_parquet(?) where symbol in ({})".format(",".join(f"'{s}'" for s in syms))
    dv = duckdb.execute(q, [str(raw / "stock_dividend_events.parquet")]).df()
    dv["d"] = pd.to_datetime(dv["d"])
    D = (dv.pivot_table(index="d", columns="symbol", values="amount", aggfunc="sum")
         .reindex(index=P.index, columns=P.columns).fillna(0.0))
    return P, D


def daily_total_returns(P, D):
    return ((P + D) / P.shift(1) - 1).iloc[1:]


def month_end_dates(index, start):
    me = pd.Series(index, index=index).groupby(index.to_period("M")).max()
    return [pd.Timestamp(d) for d in me.values if pd.Timestamp(d) >= pd.Timestamp(start)]


# ---------------------------------------------------------------- signals
def carry_signal(P, D, dates, cols, lookback_days):
    """Annualised distribution yield known at each date: sum of distributions with ex-date in
    (date - lookback, date], times 365/lookback, divided by the price on the date."""
    out = {}
    for s in cols:
        out[s] = [D.loc[(D.index > d - pd.Timedelta(days=lookback_days)) & (D.index <= d), s].sum()
                  * 365.0 / lookback_days / P.at[d, s] for d in dates]
    return pd.DataFrame(out, index=pd.DatetimeIndex(dates))


def momentum_signal(TRI, dates, cols, months):
    M = TRI.loc[dates, cols]
    return M.shift(1) / M.shift(months) - 1


def long_short(sig, k):
    """Long the k highest, short the k lowest, equal weight; ties broken by currency code."""
    w = pd.DataFrame(0.0, index=sig.index, columns=sig.columns)
    for d, row in sig.iterrows():
        s = row.dropna()
        if len(s) < len(sig.columns):
            continue
        order = sorted(s.index, key=lambda c: (s[c], c))
        w.loc[d, order[-k:]] = 1.0 / k
        w.loc[d, order[:k]] = -1.0 / k
    return w


# ---------------------------------------------------------------- backtest
def next_month_returns(TRI, dates, cols):
    M = TRI.loc[dates, cols]
    return (M.shift(-1) / M - 1)


def run_rule(w, R, cost):
    turnover = w.diff().abs().sum(axis=1)
    turnover.iloc[0] = w.iloc[0].abs().sum()
    return (w * R).sum(axis=1) - turnover * cost, turnover


def vol_scale(w, TR, dates, target, window, cap):
    """Scale factor at each month-end from the trailing daily volatility of the current book."""
    wd = w.reindex(TR.index, method="ffill").shift(1)
    daily = (wd * TR[w.columns]).sum(axis=1)
    vol = daily.rolling(window).std() * np.sqrt(252)
    return (target / vol).clip(upper=cap).reindex(pd.DatetimeIndex(dates)).fillna(1.0)


def stats(r):
    r = r.dropna()
    ann = r.mean() * 12; vol = r.std() * np.sqrt(12)
    cum = (1 + r).cumprod(); dd = (cum / cum.cummax() - 1).min()
    return {"ann_return": ann, "ann_vol": vol, "sharpe": ann / vol if vol > 0 else float("nan"),
            "max_drawdown": dd, "worst_month": r.min(), "worst_month_date": str(r.idxmin().date()),
            "skew": r.skew(), "hit_rate": (r > 0).mean(), "months": int(len(r))}


def main(cfg_path="configs/default.yaml"):
    cfg = yaml.safe_load(open(cfg_path)); out = Path(cfg["out_dir"]); (out / "figures").mkdir(parents=True, exist_ok=True)
    cols = list(cfg["currencies"])
    P, D = load(cfg); TR = daily_total_returns(P, D); TRI = (1 + TR.fillna(0)).cumprod()
    dates = month_end_dates(P.index, cfg["start"])
    R = next_month_returns(TRI, dates, cols); spy = next_month_returns(TRI, dates, ["SPY"])["SPY"]
    car = carry_signal(P, D, dates, cols, cfg["carry_lookback_days"])
    mom = momentum_signal(TRI, dates, cols, cfg["momentum_months"])
    k = cfg["k"]; cost = cfg["cost_per_turnover"]

    wA = long_short(car, k)
    spread = car.max(axis=1) - car.min(axis=1)
    wB = wA.mul((spread >= cfg["min_spread"]).astype(float), axis=0)
    wC = wA.mul(vol_scale(wA, TR, dates, cfg["vol_target"], cfg["vol_window"], cfg["max_leverage"]), axis=0)
    wD = long_short(mom.dropna(how="all"), k).reindex(wA.index).fillna(0.0)
    wE = pd.DataFrame(1.0 / len(cols), index=wA.index, columns=cols)

    rules = {"A_carry": wA, "B_carry_spread_filter": wB, "C_carry_vol_scaled": wC,
             "D_momentum": wD, "E_short_dollar_basket": wE}
    res = {}; rets = {}
    for name, w in rules.items():
        r, to = run_rule(w, R, cost); r = r.iloc[:-1]  # last month-end has no next month yet
        r.index = pd.DatetimeIndex(dates[1:len(r) + 1])  # label by the month the return is earned
        rets[name] = r; res[name] = stats(r); res[name]["turnover_per_year"] = to.iloc[:-1].mean() * 12
    rets["SPY"] = spy.iloc[:-1].set_axis(pd.DatetimeIndex(dates[1:len(spy)])); res["SPY"] = stats(rets["SPY"])
    monthly = pd.DataFrame(rets)
    monthly.index.name = "return_month_end"; monthly.to_csv(out / "monthly_returns.csv", float_format="%.6f")

    eras = {}
    for a, b, label in cfg["eras"]:
        m = monthly.loc[a:b]
        eras[label] = {n: stats(m[n]) for n in ["A_carry", "C_carry_vol_scaled", "SPY"]}
        eras[label]["avg_yield_spread"] = float(spread.loc[pd.Timestamp(a) - pd.offsets.MonthEnd(1):b].mean())

    worst = monthly["A_carry"].nsmallest(5)
    contrib = {}
    for d in worst.index:
        i = dates.index(pd.Timestamp(d)) - 1
        contrib[str(d.date())] = {"carry_rule": float(worst[d]), "positions": {c: float(wA.iloc[i][c]) for c in cols if wA.iloc[i][c] != 0},
                                  "currency_returns": {c: float(R.iloc[i][c]) for c in cols}}

    bad = monthly["SPY"].nsmallest(int(len(monthly) * 0.1)).index
    stress = {n: float(monthly.loc[bad, n].mean()) for n in monthly.columns}
    corr = {n: float(monthly[n].corr(monthly["SPY"])) for n in monthly.columns if n != "SPY"}

    # data check: trust daily returns vs the exchange rate (quoted as foreign currency per USD)
    fx = pd.read_parquet(Path(cfg["raw_dir"]) / "exchange_rate.parquet")
    fx["d"] = pd.to_datetime(fx["report_date"]); fx["close"] = fx["close"].astype(float)
    X = fx.pivot_table(index="d", columns="symbol", values="close")
    check = {}
    for etf, sym in cfg["fx_cross_check"].items():
        usd_per_ccy = (1 / X[sym]).resample("ME").last().pct_change(fill_method=None)
        etf_m = P[etf].resample("ME").last().pct_change(fill_method=None)
        j = pd.concat([etf_m, usd_per_ccy], axis=1, sort=True).dropna().loc[cfg["start"]:]
        check[etf] = round(float(j.corr().iloc[0, 1]), 4)   # monthly: the two series close at different times of day

    # illustrative committee sizing: give each rule 1% of NAV in volatility
    nav = cfg["nav_usd"]; budget = cfg["alloc_vol_budget"]; sizing = {}
    for n in ["A_carry", "C_carry_vol_scaled"]:
        s = res[n]; notional = budget / s["ann_vol"] * nav
        sizing[n] = {"gross_notional_usd": notional, "expected_pnl_per_year_usd": s["ann_return"] * notional,
                     "worst_month_usd": s["worst_month"] * notional,
                     "oct_2008_usd": float(monthly.loc["2008-10", n].iloc[0]) * notional}

    metrics = {"sample": {"first_signal": str(dates[0].date()), "last_return_month": str(monthly.index[-1].date()),
                          "months": int(len(monthly)), "currencies": cfg["currencies"]},
               "rules": res, "eras": eras, "worst_carry_months": contrib, "spy_worst_decile_avg": stress,
               "corr_with_spy": corr, "fx_cross_check_corr": check, "sizing": sizing,
               "latest_carry_yields": {c: float(car.iloc[-1][c]) for c in cols},
               "active_share_rule_B": float((spread >= cfg["min_spread"]).mean())}
    json.dump(metrics, open(out / "metrics.json", "w"), indent=2, default=float)
    figures(cfg, out / "figures", monthly, car, eras)
    return metrics


# ---------------------------------------------------------------- charts
def _years(ax):
    ax.xaxis.set_major_locator(matplotlib.dates.YearLocator(2)); ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y"))


def figures(cfg, fd, monthly, car, eras):
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    shade = [(pd.Timestamp(a), pd.Timestamp(b), l) for a, b, l in cfg["eras"]]

    fig, ax = plt.subplots(figsize=(9, 4.6))
    for n, c, l in [("A_carry", NAVY, "Carry: long 2 highest-yield, short 2 lowest"), ("C_carry_vol_scaled", GOLD, "Carry, scaled to 8% volatility"),
                    ("D_momentum", GREY, "Momentum (12-1 month)")]:
        ax.plot((1 + monthly[n]).cumprod(), color=c, lw=1.8, label=l)
    ax.axvspan(shade[1][0], shade[1][1], color="#ECEAE4", zorder=0); ax.text(pd.Timestamp("2015-01-01"), 0.97, "Zero-rate decade", color=GREY, fontsize=9, transform=ax.get_xaxis_transform())
    ax.axhline(1, color="#BBB", lw=0.8); ax.set_ylabel("Growth of $1 (after costs)")
    ax.set_title("G6 currency carry, 2007-2026: \\$1 became \\$1.05 in nineteen years", loc="left", fontsize=12)
    _years(ax); ax.legend(frameon=False, fontsize=9, loc="lower left"); fig.tight_layout(); fig.savefig(fd / "cumulative.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.4))
    names = cfg["currencies"]; colors = [NAVY, GOLD, BLUE, GREY, RED, "#6B8E5A"]
    for (s, ccy), c in zip(names.items(), colors):
        ax.plot(car[s] * 100, color=c, lw=1.6, label=ccy)
    ax.set_ylabel("Deposit yield paid by the trust (% a year)"); ax.set_title("The carry signal: what each currency actually paid", loc="left", fontsize=12)
    _years(ax); ax.legend(frameon=False, ncol=6, fontsize=9, loc="upper right"); fig.tight_layout(); fig.savefig(fd / "carry_yields.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    labels = list(eras); x = np.arange(len(labels)); wdt = 0.36
    a = [eras[l]["A_carry"]["sharpe"] for l in labels]; c = [eras[l]["C_carry_vol_scaled"]["sharpe"] for l in labels]
    b1 = ax.bar(x - wdt / 2, a, wdt, color=NAVY, label="Carry"); b2 = ax.bar(x + wdt / 2, c, wdt, color=GOLD, label="Carry, vol-scaled")
    for bars in (b1, b2):
        for r in bars:
            ax.text(r.get_x() + r.get_width() / 2, r.get_height() + (0.02 if r.get_height() >= 0 else -0.05), f"{r.get_height():.2f}", ha="center", fontsize=9)
    ax.axhline(0, color="#999", lw=0.8); ax.set_ylim(min(min(a), min(c)) - 0.12, max(max(a), max(c)) + 0.12); ax.tick_params(axis='x', pad=14); ax.set_xticks(x); ax.set_xticklabels([f"{l}\n{cfg['eras'][i][0][:4]}-{cfg['eras'][i][1][:4]}" for i, l in enumerate(labels)])
    ax.set_ylabel("Sharpe ratio"); ax.set_title("Carry pays when rates differ, and only then", loc="left", fontsize=12)
    ax.legend(frameon=False, fontsize=9); fig.tight_layout(); fig.savefig(fd / "sharpe_by_era.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    r = monthly["A_carry"] * 100
    ax.hist(r, bins=np.arange(-15, 9, 1), color=NAVY, alpha=0.85, edgecolor="white")
    w = r.idxmin(); ax.annotate(f"{w:%b %Y}: {r.min():.1f}%", xy=(r.min(), 1), xytext=(r.min() + 1.5, 25), arrowprops=dict(arrowstyle="->", color=RED), color=RED)
    ax.xaxis.set_major_formatter(PercentFormatter(decimals=0)); ax.set_xlabel("Monthly return of the carry rule"); ax.set_ylabel("Number of months")
    ax.set_title("Small gains, rare large losses", loc="left", fontsize=12); fig.tight_layout(); fig.savefig(fd / "monthly_distribution.png", dpi=160); plt.close(fig)


if __name__ == "__main__":
    m = main()
    for n, s in m["rules"].items():
        print(f"{n:24s} ann {s['ann_return']:7.2%} vol {s['ann_vol']:6.2%} SR {s['sharpe']:5.2f} maxDD {s['max_drawdown']:7.2%} worst {s['worst_month']:7.2%} ({s['worst_month_date']})")

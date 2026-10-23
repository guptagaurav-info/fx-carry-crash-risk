# How this project works

Two diagrams:
1. **Pipeline**: every step, its source file, and the logic inside it.
2. **The trade**: what the carry book holds, when it pays, how it breaks, and how the committee sizing works.

Colour key: **blue** = external data source · **lavender** = code step · **yellow** = rule or assumption · **red** = check or gate · **grey** = output

## 1. Pipeline (what `make all` runs)

```mermaid
flowchart TD
    A["SOURCE: daily closes + distribution events<br/>FXA FXB FXC FXE FXF FXY (currency trusts) and SPY<br/>mirror: huggingface.co/datasets/defeatbeta/yahoo-finance-data"]
    B["SOURCE: daily exchange rates (AUD=X ... JPY=X)<br/>same mirror, used only for the data check"]
    I{"ingest.py<br/>sha256 matches config?"}
    A --> I
    B --> I
    I -- "no" --> X["Stop: source changed"]
    I -- "yes" --> L

    subgraph L["Build monthly returns and signals · pipeline.py"]
      L1["Daily total return per trust<br/>= price change + distribution on its ex-date"]
      L2["At each month-end: carry yield =<br/>distributions in the last 91 days × 365/91 ÷ price"]
      L3["12-1 month momentum, known at month-end"]
      L4["Next-month total return per trust,<br/>labelled to the month it is earned"]
    end

    subgraph R["Rules fixed in configs/default.yaml"]
      R1["A. Long top 2 yields, short bottom 2"]
      R2["B. A only when the spread ≥ 1 point"]
      R3["C. A scaled to 8% vol (63-day window, cap 2×)"]
      R4["D. Momentum, same structure · E. Long all six vs USD"]
      R5["Cost 10 bp per unit of turnover"]
    end

    subgraph T["Tests"]
      T1{"Look-ahead checks (unit tests):<br/>future prices cannot change<br/>the signal or the weights"}
      T2{"Data check: monthly trust returns<br/>vs inverted exchange rate, corr 0.95–0.98"}
      T3["Full sample and three rate eras<br/>2007–11 · 2012–21 · 2022–26"]
      T4["Worst months, behaviour in the S&P 500's<br/>worst 10% of months, committee sizing"]
    end

    O["reports/metrics.json · monthly_returns.csv · figures/*.png"]
    L --> R --> T --> O

    classDef src fill:#dbe8ff,stroke:#1f5fbf,color:#0b1f44
    classDef step fill:#ece8ff,stroke:#6b5fbf,color:#1d1640
    classDef rule fill:#fff4cc,stroke:#b38f00,color:#3d3000
    classDef gate fill:#ffe0e0,stroke:#c03030,color:#400000
    classDef out fill:#eeeeee,stroke:#777,color:#222
    class A,B src
    class L1,L2,L3,L4,T3,T4 step
    class R1,R2,R3,R4,R5 rule
    class I,X,T1,T2 gate
    class O out
```

## 2. The trade and the sizing decision

```mermaid
flowchart LR
    S["Month-end: rank six currencies<br/>by the deposit yield they actually pay"] --> P["Hold the two highest yielders,<br/>fund them with the two lowest"]
    P --> Q{"Is there a yield gap<br/>worth harvesting?"}
    Q -- "zero-rate decade, 2012–21:<br/>spread about 1 point" --> Z["Nothing to harvest<br/>Sharpe −0.07"]
    Q -- "rates back, 2022–26:<br/>spread about 1.8 points" --> W["Earns 3.6% a year<br/>Sharpe 0.65"]
    P --> CR["Risk-off month: high yielders fall,<br/>funders rally · Oct 2008 −14.4%"]
    W --> SZ{"Committee sizing:<br/>1% of NAV volatility budget<br/>on $1bn = about $118M gross"}
    Z --> SZ
    CR --> SZ
    SZ --> C1["Constant book: expected about $0.7M a year<br/>Oct 2008 replay −$17.1M"]
    SZ --> C2["Vol-scaled book: same expected return<br/>Oct 2008 replay −$8.9M"]
    classDef src fill:#dbe8ff,stroke:#1f5fbf,color:#0b1f44
    classDef gate fill:#ffe0e0,stroke:#c03030,color:#400000
    classDef ok fill:#e3f3e3,stroke:#3a8a3a,color:#103010
    classDef rule fill:#fff4cc,stroke:#b38f00,color:#3d3000
    class S,P src
    class Q,SZ gate
    class W,C2 ok
    class Z,CR,C1 rule
```

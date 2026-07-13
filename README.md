# Portfolio & Asset Backtest Visualizer

A lightweight, SQLite-backed web application to download historical stock/ETF data via Yahoo Finance (`yfinance`) and compare cumulative returns (Price vs. Total Return) incorporating custom Withholding Tax (WHT) rates and Return of Capital (ROC) tax drag adjustments.

> [!WARNING]
> **Financial & Valuation Disclaimer**: This repository is provided **AS-IS** without any warranties of any kind. The author(s) and contributor(s) are not responsible or liable for any financial loss, transaction accounting discrepancies, incorrect portfolio valuations, trading errors, or damages resulting from bugs or errors in this project. Use at your own risk.

---

## Features

- **Double-Component Return Analysis**: Calculates both **Price Return** (no dividends) and **Total Return** (with dividends reinvested at daily closing price).
- **Tax & ROC Adjustments**: Simulates net-dividend calculations incorporating customized foreign withholding tax and Return of Capital (ROC) exemptions.
- **ROC timing options**: Supports applying ROC refunds immediately on the ex-dividend date (`exDate`) or accumulated as cash and paid out at the end of the backtest (`endPeriod`).
- **Synchronized Timeline Rule**: Automatically aligns the backtest timeline to the latest inception/launch date among compared tickers, guaranteeing a mathematically fair CAGR and drawdown comparison.
- **Intelligent Local Cache**: Remembers previously downloaded ranges and ticker inception limits in an unversioned SQLite database, optimizing API loading times down to `<50ms` on cache hits.
- **Reverse Proxy Ready**: Supports serving under sub-paths (context paths) via the `BASE_PATH` environment variable.
- **Interactive UI**: Gorgeous responsive dark-mode interface built with Vanilla CSS and interactive, stacked **Plotly.js** charts (zooming, tooltips, legend toggling).

---

## Directory Structure

```
backtester/
├── app.py                      # Flask web application entrypoint
├── download_data.py            # SQLite download & caching manager
├── chart_performance.py        # Returns calculator & HTML report compiler
├── templates/
│   └── index.html              # Homepage form (date calculators & dropdown config)
├── config/                     # Configuration folder
│   ├── config-tdaq.json        # QQQ / QQCL / TDAQ config template
│   └── config-google.json      # GOGY / GOOGL / GOOP config template
├── data/                       # Local SQLite database (git ignored)
│   └── market_data.db
├── Dockerfile                  # Application Docker image
└── docker-compose.yaml         # Compose layout (persisted volumes & host mounting)
```

---

## Configuration Template

Add JSON templates under the `config/` directory. They will automatically populate in the homepage dropdown list.

```json
{
    "configName": "QQCL vs QQQ vs TDAQ",
    "startDate": "2024-01-01",
    "endDate": "2026-06-18",
    "compare": [
        {
            "ticker": "QQCL.TO",
            "tax": 0.15,
            "roc": 0,
            "rocTiming": "exDate"
        },
        {
            "ticker": "QQQ",
            "tax": 0.30,
            "roc": 0.90,
            "rocTiming": "exDate"
        },
        {
            "ticker": "TDAQ",
            "tax": 0.30,
            "roc": 0.90,
            "rocTiming": "endPeriod"
        }
    ]
}
```

### Compare Object Options
- `ticker`: Ticker symbol (use `.TO` suffix for TSX-listed tickers).
- `tax`: Withholding tax rate (e.g. `0.30` represents a 30% WHT).
- `roc`: Return of Capital percentage (e.g. `0.90` indicates 90% of the tax is exempt under ROC).
- `rocTiming`:
  - `"exDate"` (Default): Reinvests the ROC tax refund immediately on the ex-dividend date to buy more shares (compounding).
  - `"endPeriod"`: Holds the ROC tax refund portion as cash in the portfolio, adding it as a lump sum at the end of the period (no compounding).

---

## URL Parameter Integration

You can pre-fill the backtester homepage inputs by passing URL query parameters. This is useful when integrating with external screening or stock analysis tools that redirect users here.

### Supported Parameters
- `tickers` or `ticker`: A comma-separated list of symbols (e.g., `tickers=AAPL,MSFT,TSLA`) or multiple query keys (e.g., `tickers=AAPL&tickers=MSFT`).
- `startDate` / `startdate` / `start`: Backtest start date (`YYYY-MM-DD`).
- `endDate` / `enddate` / `end`: Backtest end date (`YYYY-MM-DD`).
- `initialInvestment` / `investment`: The initial starting capital (e.g., `15000`).
- `roc`: Return of Capital rate percentage (e.g., `85`).

### Example Integration URL
```
http://localhost:5000/?tickers=AAPL,MSFT,TSLA&startDate=2025-01-01&endDate=2026-01-01&investment=15000&roc=85
```

---

## Getting Started

### Method 1: Using Docker Compose (Recommended)

1. Build and start the container:
   ```bash
   docker compose up --build -d
   ```
2. Open your browser and go to **[http://localhost:5000](http://localhost:5000)**.
3. The configurations will dynamically mount from the host's `./config` directory, and database data will persist in `./data`.

### Method 2: Local Python Run

1. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the Flask server:
   ```bash
   python3 app.py
   ```
3. Open your browser at **[http://127.0.0.1:5000](http://127.0.0.1:5000)**.

### Reverse Proxy Sub-path (Context Path) Support
If hosting behind a reverse proxy (e.g. Nginx, Traefik) under a context path like `/backtest`, pass it via the environment variables:

- **Docker Compose**: Set `BASE_PATH=/backtest` under `environment:` or in a `.env` file.
- **Local run**: Run using `BASE_PATH=/backtest python3 app.py`.

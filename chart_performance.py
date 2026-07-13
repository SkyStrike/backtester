#!/usr/bin/env python3
import os
import json
import sqlite3
import argparse
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go

def get_ticker_currency(ticker):
    """Determine currency based on ticker suffix, defaulting to USD."""
    ticker_upper = ticker.upper()
    if ticker_upper.endswith(".TO") or ticker_upper.endswith(".V"):
        return "CAD"
    elif ticker_upper.endswith(".SI"):
        return "SGD"
    elif ticker_upper.endswith(".L"):
        return "GBP"
    elif ticker_upper.endswith(".DE") or ticker_upper.endswith(".PA") or ticker_upper.endswith(".MI") or ticker_upper.endswith(".AS"):
        return "EUR"
    return "USD"

def parse_args():
    parser = argparse.ArgumentParser(description="Calculate performance metrics and generate interactive HTML report.")
    parser.add_argument(
        "config_pos",
        type=str,
        nargs="?",
        default=None,
        help="Path to the JSON configuration file (positional)"
    )
    parser.add_argument(
        "output_pos",
        type=str,
        nargs="?",
        default=None,
        help="Path to the output HTML file (positional)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Path to the JSON configuration file (default: config.json)"
    )
    parser.add_argument(
        "--db",
        type=str,
        default="data/market_data.db",
        help="Path to the SQLite database file (default: data/market_data.db)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="performance_report.html",
        help="Path to the output static HTML file (default: performance_report.html)"
    )
    return parser.parse_args()

def load_data_and_calculate(db_path, config):
    start_date = config.get("startDate")
    end_date = config.get("endDate")
    compare_list = config.get("compare", [])
    initial_investment = float(config.get("initialInvestment", 10000.0))
    
    try:
        start_date = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y-%m-%d")
        end_date = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    
    conn = sqlite3.connect(db_path)
    ticker_data = {}
    
    # Phase 1: Load aligned DataFrames for all tickers
    for item in compare_list:
        ticker = item.get("ticker")
        if not ticker:
            continue
        tax = 0.15 if ticker.endswith(".TO") else 0.30
        roc = 0.0 if ticker.endswith(".TO") else float(config.get("roc", 0.0))
        roc_timing = "endPeriod"
        
        # Load prices
        df_prices = pd.read_sql_query(
            "SELECT date, close FROM prices WHERE ticker = ? AND date >= ? AND date <= ? ORDER BY date ASC",
            conn,
            params=(ticker, start_date, end_date)
        )
        
        if df_prices.empty:
            print(f"Warning: No price data found for {ticker} in the database. Skipping.")
            continue
            
        # Load dividends
        df_divs = pd.read_sql_query(
            "SELECT date, amount FROM dividends WHERE ticker = ? AND date >= ? AND date <= ?",
            conn,
            params=(ticker, start_date, end_date)
        )
        
        # Create aligned dataframe
        df = df_prices.set_index("date")
        df["dividend"] = 0.0
        
        for _, row in df_divs.iterrows():
            div_date = row["date"]
            div_amount = row["amount"]
            if div_date in df.index:
                df.at[div_date, "dividend"] = div_amount
            else:
                # ex-dividend date might fall on a non-trading day in database, map to nearest date in index
                future_dates = [d for d in df.index if d >= div_date]
                if future_dates:
                    target_date = min(future_dates)
                    df.at[target_date, "dividend"] += div_amount
                    
        df = df.sort_index()

        # Load currency from metadata
        cursor = conn.cursor()
        cursor.execute("SELECT currency FROM ticker_metadata WHERE ticker = ?", (ticker,))
        meta_row = cursor.fetchone()
        currency = meta_row[0] if meta_row else None
        if not currency:
            currency = get_ticker_currency(ticker)
        
        # Apply USD conversion
        if currency != "USD":
            df_rates = pd.read_sql_query(
                "SELECT date, rate FROM exchange_rates WHERE currency = ? AND date >= ? AND date <= ? ORDER BY date ASC",
                conn,
                params=(currency, start_date, end_date)
            )
            df["close_local"] = df["close"]
            df["dividend_local"] = df["dividend"]
            if not df_rates.empty:
                df_rates = df_rates.set_index("date")
                df = df.join(df_rates, how="left")
                df["rate"] = df["rate"].ffill().bfill().fillna(1.0)
                df["close"] = df["close"] * df["rate"]
                df["dividend"] = df["dividend"] * df["rate"]
            else:
                print(f"Warning: No exchange rates found in database for {currency}. Falling back to 1.0.")
                df["rate"] = 1.0
        else:
            df["close_local"] = df["close"]
            df["dividend_local"] = df["dividend"]
            df["rate"] = 1.0

        ticker_data[ticker] = {
            "df": df,
            "tax": tax,
            "roc": roc,
            "roc_timing": roc_timing
        }
        
    conn.close()
    
    if not ticker_data:
        return {}
        
    # Phase 2: Find the common start date (latest of the first available dates of all tickers)
    first_dates = []
    for ticker, data in ticker_data.items():
        df = data["df"]
        first_dates.append(df.index[0])
        
    common_start_date = max(first_dates)
    print(f"Synchronization Rule: Common start date resolved to {common_start_date} (based on ticker inception/availability dates).")
    
    results = {}
    
    # Phase 3: Slice and compute returns for each ticker starting from the common_start_date
    for ticker, data in ticker_data.items():
        df = data["df"]
        tax = data["tax"]
        roc = data["roc"]
        roc_timing = data["roc_timing"]
        
        # Slice DataFrame to start from common_start_date
        df_sliced = df[df.index >= common_start_date].copy()
        if df_sliced.empty:
            print(f"Warning: Ticker {ticker} has no data on or after common start date {common_start_date}. Skipping.")
            continue
            
        closes = df_sliced["close"].values
        divs = df_sliced["dividend"].values
        closes_local = df_sliced["close_local"].values
        dates = df_sliced.index.tolist()
        
        shares = []
        portfolio_values = []
        price_returns = []
        total_returns = []
        total_returns_without = []
        
        initial_close = closes[0]
        initial_shares = initial_investment / initial_close
        current_shares = initial_shares
        accumulated_roc_refund = 0.0
        accumulated_wht_with = 0.0
        accumulated_net_div_cash = 0.0
        
        total_gross_div = 0.0
        total_net_div = 0.0
        
        for i in range(len(closes)):
            close_p = closes[i]
            div = divs[i]
            
            # Net dividend after tax and ROC
            if div > 0.0 and i > 0:  # Skip dividend if it occurs on the very first day of the sliced period
                total_gross_div += div
                net_div_rate = div * (1.0 - tax + tax * roc)
                total_net_div += net_div_rate
                accumulated_net_div_cash += initial_shares * net_div_rate
                
                shares_held = current_shares
                
                reinvest_rate = div * (1.0 - tax)
                current_shares = current_shares * (1.0 + reinvest_rate / close_p)
                # Accumulate ROC refund cash (uninvested)
                roc_refund_received = shares_held * (div * tax * roc)
                accumulated_roc_refund += roc_refund_received
                # Accumulate withholding tax paid
                wht_received = shares_held * (div * tax)
                accumulated_wht_with += wht_received
                    
            shares.append(current_shares)
            # Portfolio value includes shares and accumulated ROC refund cash
            val = current_shares * close_p + accumulated_roc_refund
            portfolio_values.append(val)
            
            # Value without reinvestment (shares at initial amount * price + accumulated net dividends cash)
            val_without = initial_shares * close_p + accumulated_net_div_cash
            total_returns_without.append((val_without / initial_investment) - 1.0)
            
            # Price return (cumulative from start)
            price_ret = (close_p / initial_close) - 1.0
            price_returns.append(price_ret)
            
            # Total return (cumulative from start)
            total_ret = (val / initial_investment) - 1.0
            total_returns.append(total_ret)
            
        df_sliced["shares"] = shares
        df_sliced["value"] = portfolio_values
        df_sliced["price_return"] = price_returns
        df_sliced["total_return"] = total_returns
        df_sliced["total_return_without"] = total_returns_without
        
        # Calculate Drawdowns
        val_series = df_sliced["value"]
        cummax = val_series.cummax()
        drawdowns = (val_series / cummax) - 1.0
        max_dd = drawdowns.min()
        
        close_series = df_sliced["close"]
        cummax_price = close_series.cummax()
        drawdowns_price = (close_series / cummax_price) - 1.0
        max_dd_price = drawdowns_price.min()
        
        # CAGR (Annualized Return)
        first_dt = datetime.strptime(dates[0], "%Y-%m-%d")
        last_dt = datetime.strptime(dates[-1], "%Y-%m-%d")
        days = (last_dt - first_dt).days
        years = days / 365.25
        
        final_total_return = total_returns[-1]
        cagr = (1.0 + final_total_return) ** (1.0 / years) - 1.0 if years > 0 else 0.0
        
        final_price_return = price_returns[-1]
        cagr_price = (1.0 + final_price_return) ** (1.0 / years) - 1.0 if years > 0 else 0.0
        
        # Additional summary metrics scaled to initial investment
        tr_val_with = portfolio_values[-1] - initial_investment
        tr_pct_with = final_total_return
        
        total_net_div_received = initial_shares * total_net_div
        total_gross_div_received = initial_shares * total_gross_div
        
        tr_val_without = (initial_shares * closes[-1] + total_net_div_received) - initial_investment
        tr_pct_without = ((initial_shares * closes[-1] + total_net_div_received) / initial_investment) - 1.0
        
        cagr_without = (1.0 + tr_pct_without) ** (1.0 / years) - 1.0 if years > 0 else 0.0
        pr_val_scaled = initial_investment * final_price_return
        
        perf_diff_val = tr_val_with - tr_val_without
        perf_diff_pct = tr_pct_with - tr_pct_without
        
        total_val_with = portfolio_values[-1]
        total_val_without = initial_shares * closes[-1] + total_net_div_received
        
        wht_paid_without = initial_shares * total_gross_div * tax
        roc_refund_without = initial_shares * total_gross_div * tax * roc
        
        results[ticker] = {
            "df": df_sliced,
            "tax": tax,
            "roc": roc,
            "roc_timing": roc_timing,
            "total_return": final_total_return,
            "price_return": final_price_return,
            "cagr": cagr,
            "cagr_price": cagr_price,
            "max_drawdown": max_dd,
            "max_drawdown_price": max_dd_price,
            "total_gross_div": total_gross_div_received,
            "total_net_div": total_net_div_received,
            "total_roc_refund": accumulated_roc_refund,
            "start_date": dates[0],
            "end_date": dates[-1],
            "days": days,
            "initial_investment": initial_investment,
            "tr_val_with": tr_val_with,
            "tr_pct_with": tr_pct_with,
            "tr_val_without": tr_val_without,
            "tr_pct_without": tr_pct_without,
            "total_val_with": total_val_with,
            "total_val_without": total_val_without,
            "wht_paid_with": accumulated_wht_with,
            "wht_paid_without": wht_paid_without,
            "roc_refund_with": accumulated_roc_refund,
            "roc_refund_without": roc_refund_without,
            "cagr_without": cagr_without,
            "pr_val_scaled": pr_val_scaled,
            "perf_diff_val": perf_diff_val,
            "perf_diff_pct": perf_diff_pct,
            "last_close": closes[-1],
            "last_close_local": closes_local[-1],
            "first_available_date": df.index[0]
        }
        
    return results

def generate_report(results, output_path, start_date, end_date):
    # Palette of premium dark mode colors
    colors = ["#06b6d4", "#a855f7", "#f43f5e", "#10b981", "#fbbf24", "#3b82f6"]
    ticker_colors = {}
    for idx, ticker in enumerate(results.keys()):
        ticker_colors[ticker] = colors[idx % len(colors)]
        
    common_start_date = None
    shifted_by_ticker = None
    if results:
        first_ticker = list(results.keys())[0]
        common_start_date = results[first_ticker]["start_date"]
        
        # Determine if timeframe was shifted by a ticker with later inception date
        if common_start_date > start_date:
            latest_date = None
            for ticker, data in results.items():
                avail_date = data.get("first_available_date")
                if avail_date:
                    if latest_date is None or avail_date > latest_date:
                        latest_date = avail_date
                        shifted_by_ticker = ticker
        
    # --- Generate Chart 1: Total Return ---
    fig_tr = go.Figure()
    for ticker, data in results.items():
        df = data["df"]
        # Add Reinvested trace
        fig_tr.add_trace(go.Scatter(
            x=df.index,
            y=df["total_return"] * 100,
            mode="lines",
            name=f"{ticker} (Reinvested)",
            line=dict(color=ticker_colors[ticker], width=2.5),
            hovertemplate="<b>" + ticker + " (Reinvested)</b><br>Date: %{x}<br>Total Return: %{y:.2f}%<extra></extra>"
        ))
        # Add Cash Payout (Without Reinvestment) trace
        fig_tr.add_trace(go.Scatter(
            x=df.index,
            y=df["total_return_without"] * 100,
            mode="lines",
            name=f"{ticker} (Cash Payout)",
            line=dict(color=ticker_colors[ticker], width=2.0, dash="dash"),
            hovertemplate="<b>" + ticker + " (Cash Payout)</b><br>Date: %{x}<br>Total Return: %{y:.2f}%<extra></extra>"
        ))
        
    fig_tr.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=40, r=40, t=20, b=40),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#111827",
            bordercolor="rgba(255, 255, 255, 0.15)",
            font_size=14,
            font_family="Outfit",
            font_color="#f3f4f6"
        ),
        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(255, 255, 255, 0.08)",
            linecolor="rgba(255, 255, 255, 0.1)"
        ),
        yaxis=dict(
            title="Cumulative Return (%)",
            showgrid=True,
            gridcolor="rgba(255, 255, 255, 0.08)",
            linecolor="rgba(255, 255, 255, 0.1)",
            ticksuffix="%"
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )
    
    # --- Generate Chart 2: Price Return ---
    fig_pr = go.Figure()
    for ticker, data in results.items():
        df = data["df"]
        fig_pr.add_trace(go.Scatter(
            x=df.index,
            y=df["price_return"] * 100,
            mode="lines",
            name=ticker,
            line=dict(color=ticker_colors[ticker], width=2.5),
            hovertemplate="<b>" + ticker + "</b><br>Date: %{x}<br>Price Return: %{y:.2f}%<extra></extra>"
        ))
        
    fig_pr.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=40, r=40, t=20, b=40),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#111827",
            bordercolor="rgba(255, 255, 255, 0.15)",
            font_size=14,
            font_family="Outfit",
            font_color="#f3f4f6"
        ),
        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(255, 255, 255, 0.08)",
            linecolor="rgba(255, 255, 255, 0.1)"
        ),
        yaxis=dict(
            title="Cumulative Return (%)",
            showgrid=True,
            gridcolor="rgba(255, 255, 255, 0.08)",
            linecolor="rgba(255, 255, 255, 0.1)",
            ticksuffix="%"
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )
    
    # Export charts to HTML divs
    div_tr = fig_tr.to_html(full_html=False, include_plotlyjs=False, config={"responsive": True})
    div_pr = fig_pr.to_html(full_html=False, include_plotlyjs=False, config={"responsive": True})
    
    # --- Generate Chart 3: Summary Bar Chart ---
    fig_summary = go.Figure()
    tickers = list(results.keys())
    tr_with = [results[t]["total_return"] * 100 for t in tickers]
    tr_without = [results[t]["tr_pct_without"] * 100 for t in tickers]
    
    fig_summary.add_trace(go.Bar(
        x=tickers,
        y=tr_with,
        name="With Re-investment",
        marker_color="#3b82f6",
        text=[f"{val:.2f}%" for val in tr_with],
        textposition="auto",
        hovertemplate="<b>%{x}</b><br>With Re-investment: %{y:.2f}%<extra></extra>"
    ))
    fig_summary.add_trace(go.Bar(
        x=tickers,
        y=tr_without,
        name="Without Re-investment",
        marker_color="#10b981",
        text=[f"{val:.2f}%" for val in tr_without],
        textposition="auto",
        hovertemplate="<b>%{x}</b><br>Without Re-investment: %{y:.2f}%<extra></extra>"
    ))
    
    fig_summary.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=40, r=40, t=20, b=40),
        barmode="group",
        hoverlabel=dict(
            bgcolor="#111827",
            bordercolor="rgba(255, 255, 255, 0.15)",
            font_size=14,
            font_family="Outfit",
            font_color="#f3f4f6"
        ),
        xaxis=dict(
            linecolor="rgba(255, 255, 255, 0.1)"
        ),
        yaxis=dict(
            title="Total Return (%)",
            showgrid=True,
            gridcolor="rgba(255, 255, 255, 0.08)",
            linecolor="rgba(255, 255, 255, 0.1)",
            ticksuffix="%"
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )
    
    div_summary = fig_summary.to_html(full_html=False, include_plotlyjs=False, config={"responsive": True})
    
    import jinja2
    
    # Load and render external Jinja2 template
    script_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(script_dir, "templates")
    
    template_loader = jinja2.FileSystemLoader(searchpath=templates_dir)
    template_env = jinja2.Environment(loader=template_loader)
    template = template_env.get_template("report.html")
    
    # Fetch exchange rates
    try:
        import fetch_exchange_rates
        rates = fetch_exchange_rates.get_exchange_rates()
        usd_rate = rates.get("USD", 1.35)
        cad_rate = rates.get("CAD", 0.98)
        cad_to_usd = round(cad_rate / usd_rate, 4)
        usd_to_cad = round(usd_rate / cad_rate, 4)
    except Exception as e:
        print(f"Error fetching/calculating exchange rates, using defaults: {e}")
        cad_to_usd = 0.73
        usd_to_cad = 1.37
    
    rendered_html = template.render(
        results=results,
        ticker_colors=ticker_colors,
        div_tr=div_tr,
        div_pr=div_pr,
        div_summary=div_summary,
        start_date=start_date,
        end_date=end_date,
        resolved_start_date=common_start_date,
        shifted_by_ticker=shifted_by_ticker,
        cad_to_usd=cad_to_usd,
        usd_to_cad=usd_to_cad,
        generation_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    
    with open(output_path, "w") as f:
        f.write(rendered_html)
        
    print(f"\nSuccessfully generated HTML report at: {output_path}")

def main():
    args = parse_args()
    
    config_path = args.config_pos if args.config_pos else args.config
    
    if not os.path.exists(config_path):
        print(f"Error: Configuration file '{config_path}' not found.")
        return
        
    with open(config_path, "r") as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON config file: {e}")
            return
            
    start_date = config.get("startDate")
    end_date = config.get("endDate")
    
    if not start_date or not end_date:
        print("Error: 'startDate' and 'endDate' must be specified in the config.")
        return
        
    # Standardize date formats to YYYY-MM-DD
    try:
        start_date = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y-%m-%d")
        end_date = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as e:
        print(f"Error: Invalid date format in config (must be YYYY-MM-DD): {e}")
        return
        
    results = load_data_and_calculate(args.db, config)
    
    if not results:
        print("Error: No data available for any tickers to generate report.")
        return
        
    # Resolve output path: 1. Positional arg, 2. Config JSON 'outputFile', 3. Command flag / default
    config_output = config.get("outputFile")
    output_path = args.output_pos if args.output_pos else (config_output if config_output else args.output)
        
    generate_report(results, output_path, start_date, end_date)

if __name__ == "__main__":
    main()

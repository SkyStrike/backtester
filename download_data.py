#!/usr/bin/env python3
import os
import json
import sqlite3
import argparse
import math
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

def parse_args():
    parser = argparse.ArgumentParser(description="Download ticker data from Yahoo Finance and store in SQLite database.")
    parser.add_argument(
        "config_pos",
        type=str,
        nargs="?",
        default=None,
        help="Path to the JSON configuration file (positional)"
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
    return parser.parse_args()

def init_db(db_path):
    """Initialize the SQLite database and create tables if they do not exist."""
    print(f"Initializing database at: {db_path}")
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Create prices table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL NOT NULL,
            PRIMARY KEY (ticker, date)
        )
    """)
    
    # Create dividends table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dividends (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            PRIMARY KEY (ticker, date)
        )
    """)

    # Create exchange_rates table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exchange_rates (
            currency TEXT NOT NULL,
            date TEXT NOT NULL,
            rate REAL NOT NULL,
            PRIMARY KEY (currency, date)
        )
    """)

    # Create ticker_metadata table with currency column
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticker_metadata (
            ticker TEXT PRIMARY KEY,
            inception_date TEXT,
            currency TEXT
        )
    """)
    
    # Migration: check if currency column exists in ticker_metadata, if not, add it
    cursor.execute("PRAGMA table_info(ticker_metadata)")
    columns = [col[1] for col in cursor.fetchall()]
    if "currency" not in columns:
        print("Migrating: adding 'currency' column to ticker_metadata table.")
        cursor.execute("ALTER TABLE ticker_metadata ADD COLUMN currency TEXT")
    
    conn.commit()
    return conn

def is_download_needed(conn, ticker, start_date, end_date):
    """Check if the local database already contains the required range of data."""
    cursor = conn.cursor()
    
    # Ensure metadata table is initialized
    pass
    
    # Query min/max dates in database
    cursor.execute("SELECT MIN(date), MAX(date) FROM prices WHERE ticker = ?", (ticker,))
    row = cursor.fetchone()
    if not row or row[0] is None or row[1] is None:
        return True # No data at all
        
    db_min, db_max = row[0], row[1]
    
    # Query metadata for inception date
    cursor.execute("SELECT inception_date FROM ticker_metadata WHERE ticker = ?", (ticker,))
    meta_row = cursor.fetchone()
    inception_date = meta_row[0] if meta_row else None
    
    # Effective start date we care about
    effective_start = start_date
    if inception_date:
        effective_start = max(start_date, inception_date)
        
    # We need a download if the database does not cover the requested start or end dates
    if db_max < end_date or db_min > effective_start:
        return True
        
    return False

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

def download_ticker_data(conn, ticker, start_date, end_date):
    """Download prices and dividends from yfinance and store them in the database."""
    print(f"\nProcessing ticker: {ticker}")
    
    # Check if download is necessary
    if not is_download_needed(conn, ticker, start_date, end_date):
        print(f"-> Local cache hit. Complete data for {ticker} from {start_date} to {end_date} already exists. Skipping download.")
        return True
        
    print(f"-> Cache miss or out-of-date. Downloading from Yahoo Finance...")
    
    # yfinance end date is exclusive. To make it inclusive, we add 1 day to end_date.
    try:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        yf_end_date = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError as e:
        print(f"Error parsing dates for {ticker}: {e}")
        return False

    # Fetch history using auto_adjust=False to get split-adjusted but not dividend-adjusted Close prices
    ticker_obj = yf.Ticker(ticker)
    
    # Determine ticker currency
    currency = "USD"
    try:
        currency = getattr(ticker_obj.fast_info, "currency", None)
        if not currency:
            currency = ticker_obj.info.get("currency", "USD")
    except Exception:
        currency = get_ticker_currency(ticker)
    
    if not currency:
        currency = "USD"
    else:
        currency = currency.upper()

    df = ticker_obj.history(start=start_date, end=yf_end_date, auto_adjust=False)
    
    if df.empty:
        print(f"Warning: No data found for ticker {ticker} in range {start_date} to {end_date}.")
        return False
        
    cursor = conn.cursor()
    
    price_count = 0
    div_count = 0
    
    # Insert prices and dividends
    for timestamp, row in df.iterrows():
        # Format the datetime index to YYYY-MM-DD
        date_str = timestamp.strftime("%Y-%m-%d")
        
        # Save close price if available and valid
        close_val = row.get("Close")
        if pd.notna(close_val):
            try:
                close_price = float(close_val)
                if not math.isnan(close_price) and not math.isinf(close_price):
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO prices (ticker, date, close)
                        VALUES (?, ?, ?)
                        """,
                        (ticker, date_str, close_price)
                    )
                    price_count += 1
            except (ValueError, TypeError):
                pass
        
        # Save dividend if greater than zero
        div_val = row.get("Dividends")
        if pd.notna(div_val):
            try:
                dividend_amt = float(div_val)
                if not math.isnan(dividend_amt) and not math.isinf(dividend_amt) and dividend_amt > 0.0:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO dividends (ticker, date, amount)
                        VALUES (?, ?, ?)
                        """,
                        (ticker, date_str, dividend_amt)
                    )
                    div_count += 1
            except (ValueError, TypeError):
                pass
            
    # Record inception date and currency in metadata
    valid_prices = df[df["Close"].notna()] if "Close" in df.columns else df
    if not valid_prices.empty:
        first_available_date = valid_prices.index[0].strftime("%Y-%m-%d")
    elif not df.empty:
        first_available_date = df.index[0].strftime("%Y-%m-%d")
    else:
        first_available_date = start_date
    
    # Calculate if the difference is substantial (greater than 7 days)
    try:
        first_avail_dt = datetime.strptime(first_available_date, "%Y-%m-%d")
        requested_start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        if (first_avail_dt - requested_start_dt).days > 7:
            new_inception = first_available_date
        else:
            new_inception = None
    except Exception:
        new_inception = None
    
    new_currency = currency
    
    cursor.execute(
        "INSERT OR REPLACE INTO ticker_metadata (ticker, inception_date, currency) VALUES (?, ?, ?)",
        (ticker, new_inception, new_currency)
    )
        
    conn.commit()
    print(f"Successfully saved {price_count} price points and {div_count} dividend payments for {ticker}.")
    return True

def download_exchange_rates(conn, currency, start_date, end_date):
    """Download historical exchange rates from Yahoo Finance and store in database."""
    fx_ticker = f"{currency}USD=X"
    print(f"\nProcessing exchange rate: {fx_ticker}")
    
    # Check if download is necessary
    if not is_download_needed(conn, fx_ticker, start_date, end_date):
        print(f"-> Local cache hit. Complete data for {fx_ticker} already exists. Skipping download.")
        return True
        
    print(f"-> Cache miss or out-of-date. Downloading exchange rate from Yahoo Finance...")
    try:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        yf_end_date = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError as e:
        print(f"Error parsing dates for {fx_ticker}: {e}")
        return False
        
    ticker_obj = yf.Ticker(fx_ticker)
    df = ticker_obj.history(start=start_date, end=yf_end_date, auto_adjust=False)
    if df.empty:
        # Fallback to USD/currency inverse if currency/USD not found
        reverse_ticker = f"USD{currency}=X"
        print(f"Warning: {fx_ticker} empty. Trying reverse ticker {reverse_ticker}...")
        ticker_obj = yf.Ticker(reverse_ticker)
        df = ticker_obj.history(start=start_date, end=yf_end_date, auto_adjust=False)
        if df.empty:
            print(f"Error: Could not retrieve exchange rate for {currency}.")
            return False
        # Calculate reciprocal
        df["Close"] = 1.0 / df["Close"]
        
    cursor = conn.cursor()
    rate_count = 0
    for timestamp, row in df.iterrows():
        close_val = row.get("Close")
        if pd.isna(close_val):
            continue
        try:
            rate = float(close_val)
        except (ValueError, TypeError):
            continue
        if math.isnan(rate) or math.isinf(rate) or rate <= 0:
            continue
            
        date_str = timestamp.strftime("%Y-%m-%d")
        
        # Save exchange rate
        cursor.execute(
            """
            INSERT OR REPLACE INTO exchange_rates (currency, date, rate)
            VALUES (?, ?, ?)
            """,
            (currency, date_str, rate)
        )
        # Also save to prices table so yfinance caching knows it's downloaded
        cursor.execute(
            """
            INSERT OR REPLACE INTO prices (ticker, date, close)
            VALUES (?, ?, ?)
            """,
            (fx_ticker, date_str, rate)
        )
        rate_count += 1
        
    conn.commit()
    print(f"Successfully saved {rate_count} exchange rate points for {currency}.")
    return True

def download_needed_exchange_rates(conn, tickers, start_date, end_date):
    """Identify currencies needed for tickers and download their exchange rates."""
    currencies_needed = set()
    cursor = conn.cursor()
    
    for ticker in tickers:
        cursor.execute("SELECT currency FROM ticker_metadata WHERE ticker = ?", (ticker,))
        row = cursor.fetchone()
        currency = row[0] if row else None
        
        if not currency:
            currency = get_ticker_currency(ticker)
            
        if currency and currency != "USD":
            currencies_needed.add(currency)
            
    for currency in currencies_needed:
        download_exchange_rates(conn, currency, start_date, end_date)

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
    compare_list = config.get("compare", [])
    
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
        
    if not compare_list:
        print("Error: 'compare' list of tickers must not be empty.")
        return
        
    conn = init_db(args.db)
    
    success_tickers = []
    failed_tickers = []
    
    tickers = [item.get("ticker") for item in compare_list if item.get("ticker")]
    for ticker in tickers:
        success = download_ticker_data(conn, ticker, start_date, end_date)
        if success:
            success_tickers.append(ticker)
        else:
            failed_tickers.append(ticker)
            
    # Download exchange rates for non-USD tickers
    download_needed_exchange_rates(conn, tickers, start_date, end_date)
    
    conn.close()
    
    print("\n" + "="*40)
    print("Download Summary:")
    print(f"Database: {args.db}")
    print(f"Successful ({len(success_tickers)}): {', '.join(success_tickers)}")
    if failed_tickers:
        print(f"Failed ({len(failed_tickers)}): {', '.join(failed_tickers)}")
    print("="*40)

if __name__ == "__main__":
    main()

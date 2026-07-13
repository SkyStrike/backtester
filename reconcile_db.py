#!/usr/bin/env python3
import os
import sqlite3
import yfinance as yf

def reconcile_database(db_path="data/market_data.db"):
    if not os.path.exists(db_path):
        print(f"Error: Database file '{db_path}' not found.")
        return

    print(f"Connecting to database: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create ticker_metadata table if it doesn't exist yet
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticker_metadata (
            ticker TEXT PRIMARY KEY,
            inception_date TEXT,
            currency TEXT
        )
    """)
    
    # Ensure the currency column exists (migration)
    cursor.execute("PRAGMA table_info(ticker_metadata)")
    columns = [col[1] for col in cursor.fetchall()]
    if "currency" not in columns:
        print("Adding 'currency' column to ticker_metadata.")
        cursor.execute("ALTER TABLE ticker_metadata ADD COLUMN currency TEXT")
    conn.commit()

    # Get all unique asset tickers (ignoring exchange rate tickers ending in =X)
    cursor.execute("SELECT DISTINCT ticker FROM prices")
    tickers = [row[0] for row in cursor.fetchall() if not row[0].endswith("=X")]

    print(f"Found {len(tickers)} tickers to reconcile: {', '.join(tickers)}\n")

    reconciled_count = 0
    for ticker in tickers:
        print(f"Reconciling '{ticker}'...")
        ticker_obj = yf.Ticker(ticker)

        # 1. Fetch true currency
        currency = "USD"
        try:
            currency = getattr(ticker_obj.fast_info, "currency", None)
            if not currency:
                currency = ticker_obj.info.get("currency", "USD")
        except Exception as e:
            print(f"  Warning: Could not fetch currency from yfinance, using fallback. Error: {e}")
            # Fallback suffix mapping
            ticker_upper = ticker.upper()
            if ticker_upper.endswith(".TO") or ticker_upper.endswith(".V"):
                currency = "CAD"
            elif ticker_upper.endswith(".SI"):
                currency = "SGD"
            elif ticker_upper.endswith(".L"):
                currency = "GBP"
            elif ticker_upper.endswith(".DE") or ticker_upper.endswith(".PA") or ticker_upper.endswith(".MI") or ticker_upper.endswith(".AS"):
                currency = "EUR"

        if currency:
            currency = currency.upper()

        # 2. Fetch true inception date from max history
        true_inception = None
        try:
            df = ticker_obj.history(period="max")
            if not df.empty:
                true_inception = df.index[0].strftime("%Y-%m-%d")
        except Exception as e:
            print(f"  Warning: Could not fetch max history for '{ticker}'. Error: {e}")

        print(f"  -> Resolved Inception Date: {true_inception}")
        print(f"  -> Resolved Currency: {currency}")

        # Update metadata table
        cursor.execute(
            "INSERT OR REPLACE INTO ticker_metadata (ticker, inception_date, currency) VALUES (?, ?, ?)",
            (ticker, true_inception, currency)
        )
        reconciled_count += 1
        print(f"  Successfully saved metadata for '{ticker}'.\n")

    conn.commit()
    conn.close()
    print(f"Reconciliation completed successfully. Reconciled {reconciled_count} tickers.")

if __name__ == "__main__":
    reconcile_database()

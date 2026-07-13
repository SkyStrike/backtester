import requests
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.frankfurter.app"
FROM_CURRENCY = "SGD"
TO_CURRENCIES = "USD,CAD"

# Retrieve configuration from env
EXCHANGE_RATE_FILE = os.getenv("exchange_rates_file", "data/exchange_rates.json")
MAX_POLL_HOURS = int(os.getenv("exchange_rates_max_poll_hours", 6))
DECIMALS = int(os.getenv("exchange_rates_decimals", 4))

def get_latest_exchange_rates():
    """Fetches live rates from the API and transforms them into the expected format."""
    try:
        response = requests.get(
            f"{BASE_URL}/latest?from={FROM_CURRENCY}&to={TO_CURRENCIES}", 
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        if not data or "rates" not in data:
            return None

        # API returns SGD as base, so rates are 1 SGD = X USD/CAD.
        # The script calculates 1 USD = Y SGD by taking the reciprocal.
        return {
            "USD": round(1 / data["rates"]["USD"], DECIMALS),
            "CAD": round(1 / data["rates"]["CAD"], DECIMALS),
            FROM_CURRENCY: 1.0
        }
    except Exception as e:
        print(f"Error fetching exchange rates: {e}")
        return None

def get_exchange_rates():
    """
    Main entry point. Returns cached rates if they are fresh, 
    otherwise attempts to fetch live rates.
    """
    file_path = Path(EXCHANGE_RATE_FILE)
    cached_data = None

    # Try to load existing cache
    if file_path.exists():
        try:
            with open(file_path, "r") as f:
                cached_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # Check if cache is fresh
    is_fresh = False
    if cached_data and "last_updated" in cached_data:
        try:
            last_updated = datetime.fromisoformat(cached_data["last_updated"])
            if datetime.now() - last_updated < timedelta(hours=MAX_POLL_HOURS):
                is_fresh = True
        except ValueError:
            pass

    if is_fresh:
        return cached_data.get("rates", {})

    # Fetch new data
    new_rates = get_latest_exchange_rates()
    
    if new_rates:
        # Update cache with new rates and timestamp
        cache_content = {
            "last_updated": datetime.now().isoformat(),
            "rates": new_rates
        }
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(cache_content, indent=4), encoding="utf-8")
        return new_rates
    
    # Fallback to stale cache if API call fails
    if cached_data:
        print("Warning: API fetch failed. Using stale exchange rate cache.")
        return cached_data.get("rates", {})

    return {}

if __name__ == "__main__":
    print(get_exchange_rates())

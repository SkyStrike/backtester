#!/usr/bin/env python3
import os
import json
import tempfile
from datetime import datetime
from flask import Flask, render_template, request, send_file, abort, jsonify

# Import downloader and chart performance logic
import download_data
import chart_performance

class PrefixMiddleware(object):
    def __init__(self, wsgi_app, prefix=''):
        self.wsgi_app = wsgi_app
        self.prefix = prefix
        
    def __call__(self, environ, start_response):
        if self.prefix:
            environ['SCRIPT_NAME'] = self.prefix
            path_info = environ.get('PATH_INFO', '')
            if path_info.startswith(self.prefix):
                environ['PATH_INFO'] = path_info[len(self.prefix):]
        return self.wsgi_app(environ, start_response)

app = Flask(__name__)

# Apply BASE_PATH environment variable for reverse proxy support
base_path = os.environ.get("BASE_PATH", "").strip()
if base_path:
    if not base_path.startswith("/"):
        base_path = "/" + base_path
    base_path = base_path.rstrip("/")
    app.wsgi_app = PrefixMiddleware(app.wsgi_app, base_path)

# Ensure required directories exist
os.makedirs("config", exist_ok=True)
os.makedirs("data", exist_ok=True)

def get_available_configs():
    """Scan the config/ directory for JSON configuration files and read their display names."""
    configs = []
    config_dir = "config"
    if not os.path.exists(config_dir):
        return configs
        
    for filename in sorted(os.listdir(config_dir)):
        if filename.endswith(".json"):
            filepath = os.path.join(config_dir, filename)
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                    tickers = data.get("compare", [])
                    config_name = data.get("configName") or (" vs ".join(tickers) if tickers else filename)
                    configs.append({
                        "filename": filename,
                        "configName": config_name,
                        "tickers": tickers
                    })
            except Exception as e:
                app.logger.error(f"Error loading config file {filename}: {e}")
    return configs

@app.route("/")
def index():
    configs = get_available_configs()
    # Default start date is 5 years ago, default end date is today (js handles weekday rounding)
    default_start = (datetime.now() - timedelta_days(5 * 365)).strftime("%Y-%m-%d")
    return render_template("index.html", configs=configs, default_start=default_start)

# Helper function since timedelta isn't direct
def timedelta_days(days):
    from datetime import timedelta
    return timedelta(days=days)

@app.route("/generate", methods=["POST"])
def generate():
    start_date = request.form.get("startDate")
    end_date = request.form.get("endDate")
    initial_investment = request.form.get("initialInvestment", "10000")
    roc = request.form.get("roc", "0")
    tickers = request.form.getlist("tickers")
    
    if not start_date or not end_date:
        return "Error: Missing required date parameters.", 400
        
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    if not tickers:
        return "Error: Please specify at least one ticker to compare.", 400
        
    # Standardize date formats to YYYY-MM-DD
    try:
        start_date = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y-%m-%d")
        end_date = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as e:
        return f"Error: Invalid date format: {e}", 400
        
    # Build config dynamically on-the-fly
    config = {
        "startDate": start_date,
        "endDate": end_date,
        "initialInvestment": initial_investment,
        "compare": [{"ticker": t} for t in tickers]
    }
    try:
        config["roc"] = float(roc) / 100.0
    except ValueError:
        config["roc"] = 0.0
    
    # 1. Download/Update ticker data in sqlite
    try:
        conn = download_data.init_db("data/market_data.db")
        tickers_list = []
        for item in config.get("compare", []):
            ticker = item.get("ticker")
            if ticker:
                download_data.download_ticker_data(conn, ticker, start_date, end_date)
                tickers_list.append(ticker)
        # Download exchange rates for non-USD tickers
        download_data.download_needed_exchange_rates(conn, tickers_list, start_date, end_date)
        conn.close()
    except Exception as e:
        return f"Database/Download Error: {e}", 500
        
    # 2. Run calculations
    try:
        results = chart_performance.load_data_and_calculate("data/market_data.db", config)
    except Exception as e:
        return f"Calculation Error: {e}", 500
        
    if not results:
        return "Error: No price data available in the database for the selected dates/tickers.", 400
        
    # 3. Generate HTML report in a temporary, non-persistent path
    clean_filename = "_".join(tickers)
    output_path = os.path.join(tempfile.gettempdir(), f"report_{clean_filename}.html")
    
    try:
        chart_performance.generate_report(results, output_path, start_date, end_date)
    except Exception as e:
        return f"Report Generation Error: {e}", 500
        
    # 4. Return file directly to be opened in a new tab/window
    if os.path.exists(output_path):
        return send_file(output_path)
    else:
        return "Error: Report file could not be generated.", 500

@app.route("/api/configs", methods=["GET", "POST"])
def api_configs():
    config_dir = "config"
    if not os.path.exists(config_dir):
        os.makedirs(config_dir, exist_ok=True)

    if request.method == "GET":
        configs = []
        for filename in sorted(os.listdir(config_dir)):
            if filename.endswith(".json"):
                filepath = os.path.join(config_dir, filename)
                try:
                    with open(filepath, "r") as f:
                        data = json.load(f)
                        configs.append({
                            "filename": filename,
                            "data": data
                        })
                except Exception as e:
                    app.logger.error(f"Error loading config {filename}: {e}")
        return jsonify(configs)

    elif request.method == "POST":
        req_data = request.get_json()
        if not req_data:
            return "Invalid JSON data", 400
        
        filename = req_data.get("filename")
        config_data = req_data.get("data")
        
        if not filename or not config_data:
            return "Missing filename or config data", 400
            
        filename = os.path.basename(filename)
        if not filename.endswith(".json"):
            filename += ".json"
            
        filepath = os.path.join(config_dir, filename)
        try:
            with open(filepath, "w") as f:
                json.dump(config_data, f, indent=4)
            return jsonify({"success": True, "filename": filename})
        except Exception as e:
            return f"Error saving file: {e}", 500

@app.route("/api/configs/<filename>", methods=["DELETE"])
def api_delete_config(filename):
    config_dir = "config"
    filename = os.path.basename(filename)
    if not filename.endswith(".json"):
        filename += ".json"
    filepath = os.path.join(config_dir, filename)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            return jsonify({"success": True})
        except Exception as e:
            return f"Error deleting file: {e}", 500
    return "File not found", 404

if __name__ == "__main__":
    from flask import jsonify
    app.run(host="0.0.0.0", port=5000, debug=True)

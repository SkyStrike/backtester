#!/bin/bash

config=./config/config-google.json
output=./output/google.html

# 1. Download/update historical data in database
# python3 download_data.py ${config}

# 2. Recompute performance and generate the dashboard
python3 chart_performance.py ${config} ${output}
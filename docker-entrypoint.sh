#!/bin/sh
# /app/data is a persistent disk. First boot (empty disk) downloads the nflverse data and builds the model; later boots start straight away.
set -e
mkdir -p data
if [ ! -f data/games.csv ] || [ ! -f data/model.json ]; then
  echo "first boot: fetching data and building the model (several minutes)"
  python fetch_data.py
  python build_model.py
fi
exec python -m uvicorn server:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1

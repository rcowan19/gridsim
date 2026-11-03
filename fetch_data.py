"""Download nflverse data into data/. Re-run to refresh weekly:  python fetch_data.py"""
import os, sys, urllib.request
BASE = "https://github.com/nflverse/nflverse-data/releases/download"
FILES = ["pbp/play_by_play_2026", "pbp/play_by_play_2025", "rosters/roster_2026", "rosters/roster_2025",
         "snap_counts/snap_counts_2026", "injuries/injuries_2026"]
os.makedirs("data", exist_ok=True)
for f in FILES:
    kind, name = f.split("/")
    out = "data/" + name.replace("play_by_play_", "pbp_") + ".parquet"
    try:
        urllib.request.urlretrieve(f"{BASE}/{f}.parquet", out)
        print("ok  ", out, os.path.getsize(out) // 1024, "KB")
    except Exception as e:
        print("FAIL", f, e, file=sys.stderr)

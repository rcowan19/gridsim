"""Download nflverse data into data/. The server re-runs this every GRIDSIM_REFRESH_HOURS (default 6); by hand:  python fetch_data.py
Each file is downloaded next to its destination and swapped in only when complete, so a dropped connection never leaves a half-written file."""
import os, sys, urllib.request
BASE = "https://github.com/nflverse/nflverse-data/releases/download"
FILES = ["pbp/play_by_play_2026", "pbp/play_by_play_2025", "rosters/roster_2026", "rosters/roster_2025",
         "snap_counts/snap_counts_2026", "injuries/injuries_2026", "depth_charts/depth_charts_2026"]
os.makedirs("data", exist_ok=True)


def get(url, out):
    urllib.request.urlretrieve(url, out + ".part")
    os.replace(out + ".part", out)


try:   # schedule + betting lines
    get("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv", "data/games.csv")
    print("ok   data/games.csv")
except Exception as e:
    print("FAIL games.csv", e, file=sys.stderr)
for f in FILES:
    kind, name = f.split("/")
    out = "data/" + name.replace("play_by_play_", "pbp_") + ".parquet"
    try:
        get(f"{BASE}/{f}.parquet", out)
        print("ok  ", out, os.path.getsize(out) // 1024, "KB")
    except Exception as e:
        print("FAIL", f, e, file=sys.stderr)

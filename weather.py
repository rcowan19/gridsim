"""Kickoff weather forecasts from Open-Meteo (free, no key). Only used for outdoor games; fails soft (returns None)."""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# home-team stadium coordinates (lat, lon)
LOC = {"ARI": (33.528, -112.263), "ATL": (33.755, -84.401), "BAL": (39.278, -76.623), "BUF": (42.774, -78.787), "CAR": (35.226, -80.853),
       "CHI": (41.862, -87.617), "CIN": (39.095, -84.516), "CLE": (41.506, -81.699), "DAL": (32.748, -97.093), "DEN": (39.744, -105.020),
       "DET": (42.340, -83.046), "GB": (44.501, -88.062), "HOU": (29.685, -95.411), "IND": (39.760, -86.164), "JAX": (30.324, -81.637),
       "KC": (39.049, -94.484), "LA": (33.953, -118.339), "LAC": (33.953, -118.339), "LV": (36.091, -115.184), "MIA": (25.958, -80.239),
       "MIN": (44.974, -93.258), "NE": (42.091, -71.264), "NO": (29.951, -90.081), "NYG": (40.813, -74.074), "NYJ": (40.813, -74.074),
       "PHI": (39.901, -75.168), "PIT": (40.447, -80.016), "SEA": (47.595, -122.332), "SF": (37.403, -121.970), "TB": (27.976, -82.503),
       "TEN": (36.166, -86.771), "WAS": (38.908, -76.864)}
WIND_SCALE = 0.85       # forecast wind is at 10 m; stadium gauges read lower
_CACHE = {}


def forecast(game, ttl=3 * 3600):
    """game: schedule dict. Returns {'temp': F, 'wind': mph} or None."""
    if game.get("neutral") or game.get("roof") in ("dome", "closed") or game["home"] not in LOC:
        return None
    key = game["id"]
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    mock = os.environ.get("GRIDSIM_WEATHER_MOCK")
    try:
        lat, lon = LOC[game["home"]]
        if mock:
            data = json.load(open(mock))
        else:
            q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "hourly": "temperature_2m,wind_speed_10m", "temperature_unit": "fahrenheit",
                                        "wind_speed_unit": "mph", "timezone": "auto", "start_date": game["day"], "end_date": game["day"]})
            with urllib.request.urlopen(urllib.request.Request("https://api.open-meteo.com/v1/forecast?" + q, headers={"User-Agent": "GridSim/1.0"}), timeout=8) as r:
                data = json.loads(r.read().decode())
        tz = ZoneInfo(data.get("timezone", "America/New_York"))
        ko = datetime.fromisoformat(game["day"] + "T" + game["time"]).replace(tzinfo=ZoneInfo("America/New_York")).astimezone(tz)
        times = data["hourly"]["time"]
        idx = [i for i, t in enumerate(times) if ko.replace(minute=0, second=0, microsecond=0, tzinfo=None) <= datetime.fromisoformat(t) < ko.replace(tzinfo=None) + timedelta(hours=3)]
        if not idx:
            raise ValueError("kickoff hour not in forecast")
        temp = sum(data["hourly"]["temperature_2m"][i] for i in idx) / len(idx)
        wind = sum(data["hourly"]["wind_speed_10m"][i] for i in idx) / len(idx) * WIND_SCALE
        out = {"temp": round(temp), "wind": round(wind)}
    except Exception:
        out = None
    _CACHE[key] = (time.time(), out)
    return out


def for_week(games):
    return {g["id"]: forecast(g) for g in games if not g.get("played")}

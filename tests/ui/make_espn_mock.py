"""Build a synthetic ESPN payload from GridSim's own data so My-league screens can be exercised offline.
   python tests/ui/make_espn_mock.py /tmp/espn_mock.json ; then start the server with GRIDSIM_ESPN_MOCK=/tmp/espn_mock.json"""
import json, sys, urllib.request, pandas as pd
out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/espn_mock.json"
req = urllib.request.Request("http://localhost:8000/api/slate", data=json.dumps({"week": 9, "n": 200, "weather": False, "ov": {}}).encode(), headers={"Content-Type": "application/json"})
rows = json.load(urllib.request.urlopen(req, timeout=120))["rows"]
R = pd.read_parquet("data/roster_2026.parquet").drop_duplicates("gsis_id").set_index("gsis_id")
PRO = {"ATL": 1, "BUF": 2, "CHI": 3, "CIN": 4, "CLE": 5, "DAL": 6, "DEN": 7, "DET": 8, "GB": 9, "TEN": 10, "IND": 11, "KC": 12, "LV": 13, "LA": 14, "MIA": 15, "MIN": 16, "NE": 17, "NO": 18, "NYG": 19, "NYJ": 20, "PHI": 21, "ARI": 22, "PIT": 23, "LAC": 24, "SF": 25, "SEA": 26, "TB": 27, "WAS": 28, "CAR": 29, "JAX": 30, "BAL": 33, "HOU": 34}
POSID = {"QB": 1, "RB": 2, "WR": 3, "TE": 4}
pool = sorted([r for r in rows if r["pos"] in POSID and r["id"] in R.index], key=lambda r: -r["proj"])[:150]
names = ["Roan's Rowdy Team", "Gridiron Gang", "Fourth & Long", "Hail Mary Heroes", "Blitz Krieg", "Red Zone Rascals", "Sack Attack", "Pocket Presence", "Bye Week Bandits", "Flex Appeal", "Waiver Wire Wizards", "Dico Dynasty"]
T = [dict(id=i + 1, name=n, abbrev=n[:4].upper(), record={"overall": {"wins": 5 - i % 4, "losses": 3 + i % 4, "ties": 0}}, roster={"entries": []}, waiverRank=i + 1) for i, n in enumerate(names)]
order = list(range(12)) + list(range(11, -1, -1))
for k, r in enumerate(pool[:12 * 12]):
    t = T[order[k % 24]]; n = len(t["roster"]["entries"])
    pos = r["pos"]; cnt = {p: sum(1 for e in t["roster"]["entries"] if e["playerPoolEntry"]["player"]["defaultPositionId"] == POSID[p]) for p in POSID}
    slot = 20
    if pos == "QB" and cnt["QB"] == 0: slot = 0
    elif pos == "RB" and cnt["RB"] < 2: slot = 2
    elif pos == "WR" and cnt["WR"] < 2: slot = 4
    elif pos == "TE" and cnt["TE"] == 0: slot = 6
    elif pos in ("RB", "WR", "TE") and not any(e["lineupSlotId"] == 23 for e in t["roster"]["entries"]): slot = 23
    t["roster"]["entries"].append({"lineupSlotId": slot, "playerPoolEntry": {"player": {"id": 100000 + k, "fullName": R.loc[r["id"], "full_name"], "defaultPositionId": POSID[pos], "proTeamId": PRO.get(r["team"], 0), "injuryStatus": "ACTIVE"}}})
sched = []
for w in range(1, 15):
    rot = list(range(12)); rot = [rot[0]] + rot[1:][(w - 1) % 11:] + rot[1:][:(w - 1) % 11]
    for i in range(6): sched.append({"matchupPeriodId": w, "home": {"teamId": rot[i] + 1}, "away": {"teamId": rot[11 - i] + 1}})
items = [{"statId": s, "points": p} for s, p in [(3, .04), (4, 4), (20, -2), (24, .1), (25, 6), (42, .1), (43, 6), (53, 1)]]
raw = {"seasonId": 2026, "teams": T, "schedule": sched, "status": {"currentMatchupPeriod": 9}, "scoringPeriodId": 9,
       "settings": {"name": "Dico Fantasy2 (fixture)", "rosterSettings": {"lineupSlotCounts": {"0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "20": 6}},
                    "scoringSettings": {"scoringItems": items}, "scheduleSettings": {"matchupPeriodCount": 14, "playoffTeamCount": 6, "playoffMatchupPeriodLength": 1},
                    "acquisitionSettings": {"isUsingAcquisitionBudget": True, "acquisitionBudget": 100, "waiverProcessDays": 2}}}
json.dump(raw, open(out, "w")); print("wrote", out, len(pool), "players")

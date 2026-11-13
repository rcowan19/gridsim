"""python -m tests.ui.make_sleeper_mock [/tmp/sleeper_mock.json] -> a Sleeper league for UI tests (start the server with GRIDSIM_SLEEPER_MOCK pointing at it)."""
import json, sys
from tests.fixtures import sleeper as fx

out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/sleeper_mock.json"
raw = fx.raw(); raw["matchups"] = {str(k): v for k, v in raw["matchups"].items()}
json.dump({"raw": raw, "players": fx.PLAYERS, "live": fx.live(),
           "find": {"user": {"id": "u1", "name": "roan"}, "leagues": [{"league_id": "L1", "name": "Sleeper Test League", "season": "2026", "teams": 4, "status": "in_season"}]}}, open(out, "w"))
print("wrote", out)

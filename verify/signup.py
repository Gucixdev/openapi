"""Run signup.mod on the real catalog. Prints the providers to sign up for, with their signup link. Needs amplpy + ampl_module_highs (verify/run installs them)."""
import json
import os
import sys
from pathlib import Path

from amplpy import AMPL

here = Path(__file__).resolve().parent
rows = json.loads((here.parent / "providers.json").read_text())["providers"]
keyfile = Path(os.environ.get("OPENAPI_KEYS", Path.home() / ".config/openapi/keys.env"))
held = {l.split("=")[0].removeprefix("export ").strip() for l in keyfile.read_text().splitlines() if "=" in l and not l.startswith("#")} if keyfile.exists() else set()
DEAD = ("unreachable", "down")
cand = [r for r in rows if "free" in r.get("tags", []) and r["env"] and r["env"] not in held and r["status"] not in DEAD and not os.environ.get(r["env"])]
KIND = lambda r: "gateway" if "gateway" in r["tags"] else "regional" if "regional" in r["tags"] else "direct"
WEIGHT = {"direct": 1.0, "regional": 0.7, "gateway": 0.4}

a = AMPL()
a.read(str(here / "signup.mod"))
a.set["CAND"] = [r["name"] for r in cand]
a.param["kind"] = {r["name"]: KIND(r) for r in cand}
a.param["value"] = {r["name"]: WEIGHT[KIND(r)] for r in cand}
a.param["K"], a.param["maxgateway"], a.param["maxregional"] = int(os.environ.get("K", 5)), 1, 2
a.option["solver"] = "highs"
a.solve()
if a.get_value("solve_result") != "solved":
    sys.exit("AMPL: " + str(a.get_value("solve_result")))
chosen = [n for n, v in a.get_variable("pick").get_values().to_dict().items() if v > 0.5]
print(f"objective {a.get_objective('independence').value():.1f} over {len(cand)} candidates; sign up for:")
for r in cand:
    if r["name"] in chosen:
        print(f"  {r['name']:<12} {KIND(r):<9} {r['signup'] or r['base_url']}")

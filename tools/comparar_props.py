"""Prueba puntual: compara la probabilidad del modelo de jugadores con el mercado de EE. UU. (sin margen)."""
import os, json, sys, unicodedata, statistics as S, requests
sys.path.insert(0, ".")
import jugadores as J
KEY = os.environ["THE_ODDS_API_KEY"]
BASE = "https://api.the-odds-api.com/v4"
MK = {"player_points": "pts", "player_rebounds": "reb", "player_assists": "ast", "player_threes": "tri",
      "player_points_rebounds_assists": "pra"}
N_PARTIDOS = int(os.getenv("N_PARTIDOS", "3"))
def norm(t):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    for x in (".", "'", "-", " jr", " sr", " iii", " ii", " iv"): t = t.replace(x, " ")
    return " ".join(t.split())
store = J.cargar_store(); res = json.load(open("resultados.json"))
nombres = {norm(j["n"]): (jid, j) for jid, j in store["NBA"]["p"].items()}
team_dates = {}
for r in res["ligas"]["NBA"]:
    for t in (r["hid"], r["aid"]): team_dates[t] = max(team_dates.get(t, ""), r["u"])
evs = requests.get(f"{BASE}/sports/basketball_nba/events", params={"apiKey": KEY}).json()
evs = sorted(evs, key=lambda e: e["commence_time"])[:N_PARTIDOS]
filas = []
for ev in evs:
    r = requests.get(f"{BASE}/sports/basketball_nba/events/{ev['id']}/odds",
                     params={"apiKey": KEY, "regions": "us", "markets": ",".join(MK), "oddsFormat": "decimal"})
    print(ev["home_team"], "vs", ev["away_team"], "HTTP", r.status_code, "créditos restantes", r.headers.get("x-requests-remaining"))
    d = r.json()
    lineas = {}
    for bk in d.get("bookmakers", []):
        for m in bk.get("markets", []):
            st = MK.get(m["key"])
            for o in m.get("outcomes", []):
                k = (norm(o.get("description")), st, o.get("point"))
                lineas.setdefault(k, {}).setdefault(bk["key"], {})[o["name"]] = o["price"]
    for (jug, st, pt), books in lineas.items():
        probs = []
        for b, pr in books.items():
            if "Over" in pr and "Under" in pr:
                io, iu = 1 / pr["Over"], 1 / pr["Under"]
                probs.append(io / (io + iu))
        if len(probs) < 2 or jug not in nombres or pt is None: continue
        jid, j = nombres[jug]
        proy = J.proyectar_jugador(j, "NBA", 1.0, team_dates.get(j["t"], ""))
        if not proy: continue
        media, var = proy[st]
        pm = J._nb_sobre(media, var, pt)
        filas.append((jug, st, pt, round(S.mean(probs), 3), round(pm, 3), round(media, 1), len(probs)))
print("\nlineas comparadas:", len(filas))
for st in ("pts", "reb", "ast", "tri", "pra"):
    f = [x for x in filas if x[1] == st]
    if not f: continue
    dif = [x[4] - x[3] for x in f]
    print(f"{st}: n={len(f)} prob mercado media={S.mean(x[3] for x in f):.3f} modelo={S.mean(x[4] for x in f):.3f} "
          f"diferencia media={S.mean(dif):+.3f} |dif| media={S.mean(abs(d) for d in dif):.3f}")
print("\nmayores diferencias (modelo - mercado):")
for x in sorted(filas, key=lambda x: x[4] - x[3])[:8] + sorted(filas, key=lambda x: x[4] - x[3])[-8:]:
    print(x)
json.dump(filas, open("/tmp/comparacion.json", "w"))

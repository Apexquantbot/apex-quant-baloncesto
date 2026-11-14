import requests, json
H={"User-Agent":"Mozilla/5.0","Accept":"application/json"}
def get(u,p=None):
    r=requests.get(u,params=p,headers=H,timeout=25); print("==",r.status_code,r.url[:150]); return r
d=get("https://api-live.euroleague.net/v2/competitions/E/seasons/E2026/games").json()
print("n",len(d["data"]), "otras claves", [k for k in d if k!="data"])
jug=[g for g in d["data"] if g.get("played")]
print("jugados",len(jug))
g=jug[-1]
g2=json.loads(json.dumps(g))
for side in ("local","road"):
    c=g2[side]["club"]; c.pop("images",None)
print(json.dumps(g2)[:2500])
print("claves local", list(g["local"].keys()))
b=get("https://live.euroleague.net/api/Boxscore",{"gamecode":g["gameCode"],"seasoncode":"E2026"}).json()
print("claves box",list(b.keys()))
ps=b["Stats"][0]["PlayersStats"]
print(json.dumps(ps[0]))
print("totr",json.dumps(b["Stats"][0].get("totr"))[:300])
# ESPN lesiones: estructura de un jugador
s=get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",{"dates":"20261115"}).json()
ev=s["events"][0]["id"]
sm=get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",{"event":ev}).json()
for t in sm.get("injuries",[]):
    for i in t.get("injuries",[])[:3]:
        a=i.get("athlete",{})
        print(t["team"]["id"], i.get("status"), a.get("id"), a.get("displayName"), json.dumps({k:v for k,v in i.items() if k not in("athlete",)})[:300])
# ESPN: jugador que no jugó
r=get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",{"dates":"20261113"}).json()
sm=get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",{"event":r["events"][1]["id"]}).json()
for tp in sm["boxscore"]["players"]:
    for a in tp["statistics"][0]["athletes"]:
        if a.get("didNotPlay") or not a.get("stats"):
            print("DNP",json.dumps({k:v for k,v in a.items() if k!="athlete"}), a["athlete"]["displayName"]); break
print("header comp", json.dumps(sm["header"]["competitions"][0]["competitors"][0]["team"])[:200])

import requests, json
H={"User-Agent":"Mozilla/5.0","Accept":"application/json"}
def get(u,p=None):
    try:
        r=requests.get(u,params=p,headers=H,timeout=20)
        print("==",r.status_code,r.url[:160]); return r
    except Exception as e: print("ERR",u,e)
# ESPN NBA summary de un partido terminado
r=get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",{"dates":"20261113"})
ev=r.json()["events"][0]["id"]
s=get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",{"event":ev}).json()
print("keys",list(s.keys()))
bp=s["boxscore"]["players"][0]
st=bp["statistics"][0]
print("team",bp["team"]["displayName"],"keys",st.get("keys"),"labels",st.get("labels"),"names",st.get("names"))
for a in st["athletes"][:3]: print(json.dumps(a)[:700])
print("injuries",json.dumps(s.get("injuries"))[:800])
# summary de partido futuro (inyecciones)
r=get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",{"dates":"20261115"})
ev2=r.json()["events"][0]["id"]
s2=get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",{"event":ev2}).json()
print("keys futuro",list(s2.keys()))
print("injuries futuro",json.dumps(s2.get("injuries"))[:1500])
print("boxscore futuro",json.dumps(s2.get("boxscore"))[:600])
# Euroliga
for u in ["https://api-live.euroleague.net/v2/competitions/E/seasons/E2026/games",
          "https://api-live.euroleague.net/v1/results?seasonCode=E2026",
          "https://live.euroleague.net/api/Boxscore?gamecode=80&seasoncode=E2026",
          "https://live.euroleague.net/api/Header?gamecode=80&seasoncode=E2026",
          "https://api-live.euroleague.net/v3/competitions/E/seasons/E2026/games/80/stats",
          "https://feeds.incrowdsports.com/provider/euroleague-feeds/v2/competitions/E/seasons/E2026/games?teamCode=&phaseTypeCode=RS",
          "https://api-live.euroleague.net/v2/competitions/E/seasons/E2026/games/80/stats"]:
    r=get(u)
    if r is not None: print(r.text[:900].replace("\n"," "))

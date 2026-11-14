import os
import json
import requests
import time
import unicodedata
import math
import random
import itertools
import difflib
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

VERSION = "1.0"

# ==============================================================================
# APEX QUANT BALONCESTO
# Misma arquitectura que Apex Quant fútbol: calendario + resultados gratuitos,
# cuotas reales (The Odds API) frente al consenso del mercado y modelo estadístico
# propio. Ligas: NBA, Euroliga, Liga Endesa (ACB) y Basketball Champions League.
# ==============================================================================
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TSDB_KEY = os.getenv("TSDB_KEY", "") or "123"      # TheSportsDB (clave pública gratuita "123")
APP_URL = os.getenv("APP_URL", "https://apexquantbot.github.io/apex-quant-baloncesto/")

# Huso horario oficial Peninsular (Madrid)
TZ_MADRID = ZoneInfo("Europe/Madrid")

# Fuentes de datos (todas gratuitas y sin registro):
#   NBA  -> ESPN (marcadores en directo, escudos y calendario completo)
#   Euroliga, ACB y BCL -> TheSportsDB
LEAGUES_MAP = {
    "NBA": {"name": "NBA", "key": "nba", "country": "USA", "flag": "🇺🇸", "fuente": "espn",
            "tsdb": "4387", "tsdb_nombre": "NBA", "min_partido": 48,
            "media_pts": 114.0, "ventaja_local": 2.2, "sd_margen": 13.0, "sd_total": 19.0, "duracion_min": 160},
    "EL":  {"name": "Euroliga", "key": "euroliga", "country": "EUR", "flag": "🇪🇺", "fuente": "tsdb",
            "tsdb": "4546", "tsdb_nombre": "EuroLeague Basketball", "min_partido": 40,
            "media_pts": 83.0, "ventaja_local": 3.0, "sd_margen": 11.5, "sd_total": 15.0, "duracion_min": 135},
    "ACB": {"name": "Liga Endesa ACB", "key": "acb", "country": "ESP", "flag": "🇪🇸", "fuente": "tsdb",
            "tsdb": "4408", "tsdb_nombre": "Spanish Liga ACB", "min_partido": 40,
            "media_pts": 83.0, "ventaja_local": 3.0, "sd_margen": 11.5, "sd_total": 15.0, "duracion_min": 135},
    "BCL": {"name": "Champions League", "key": "bcl", "country": "EUR", "flag": "🇪🇺", "fuente": "tsdb",
            "tsdb": "4548", "tsdb_nombre": "Basketball Champions League", "min_partido": 40,
            "media_pts": 80.0, "ventaja_local": 3.0, "sd_margen": 11.5, "sd_total": 15.0, "duracion_min": 135},
}
LIGA_POR_TSDB = {v["tsdb"]: k for k, v in LEAGUES_MAP.items()}

# ==============================================================================
# CUOTAS REALES (THE ODDS API) - MEJOR CASA vs CONSENSO DEL MERCADO
# The Odds API solo cubre NBA y Euroliga de estas 4 competiciones. En ACB y
# Champions League el bot da el pronóstico de su modelo con una CUOTA MÍNIMA
# (la cuota a partir de la cual la apuesta tiene valor), como las combinadas.
# ==============================================================================
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_SPORT_KEYS = {
    "NBA": "basketball_nba",
    "EL": "basketball_euroleague",
}
BOOKMAKERS_OBJETIVO = [b.strip() for b in os.getenv(
    "ODDS_BOOKMAKERS",
    "winamax_fr,winamax_de,betclic_fr,unibet_eu,williamhill,betsson,marathonbet,sport888,coolbet,tipico_de,nordicbet,leovegas"
).split(",") if b.strip()]
PESOS_CASAS = {"pinnacle": 3.0, "matchbook": 2.0, "betfair_ex_eu": 2.0, "smarkets": 2.0}  # casas "afiladas" pesan más en el consenso
ODDS_REGIONS = os.getenv("ODDS_REGIONS", "eu")
MIN_CASAS_CONSENSO = int(os.getenv("ODDS_MIN_CASAS", "4"))
CREDITOS_RESERVA = float(os.getenv("ODDS_MIN_CREDITS", "20"))
MAX_CONSULTAS_CICLO = int(os.getenv("ODDS_MAX_CALLS", "10"))
EV_MINIMO_PCT = float(os.getenv("EV_MINIMO_PCT", "3.0"))     # filtro de alerta (+EV >= 3% por defecto)
CUOTA_MAXIMA = float(os.getenv("ODDS_MAX_CUOTA", "3.0"))   # sin alertas a cuotas "locas"
PROB_MINIMA = float(os.getenv("ODDS_MIN_PROB", "0.40"))    # probabilidad mínima para alertar

# Combinadas de favoritos (máximo 3 partidos, todas las selecciones en la misma casa)
COMBI_ACTIVA = os.getenv("COMBI_ACTIVA", "1") == "1"
COMBI_MAX_PATAS = 3
COMBI_EV_PATA_MIN = float(os.getenv("COMBI_EV_PATA_MIN", "1.0"))
COMBI_PROB_PATA_MIN = float(os.getenv("COMBI_PROB_PATA_MIN", "0.50"))
COMBI_CUOTA_PATA = (1.25, 2.30)
COMBI_PROB_MIN = float(os.getenv("COMBI_PROB_MIN", "0.30"))
COMBI_EV_MIN = float(os.getenv("COMBI_EV_MIN", "6.0"))
EV_MAXIMO_PCT = 20.0    # por encima se considera dato sospechoso (cuota errónea / línea distinta)
VENTANA_MIN = 15
VENTANA_MAX = 90

# Pronóstico solo con el modelo (ligas sin cuotas en The Odds API)
MODELO_SOLO_ACTIVO = os.getenv("MODELO_SOLO_ACTIVO", "1") == "1"
MODELO_SOLO_PROB_MIN = float(os.getenv("MODELO_SOLO_PROB_MIN", "0.70"))   # solo favoritos claros
MODELO_SOLO_MARGEN = 1.05      # la cuota mínima exige un 5% de ventaja sobre la cuota justa del modelo

ODDS_ESTADO = {"restantes": None, "consultas": 0}

_STOP_TOKENS = {"bc", "bk", "kk", "cb", "fc", "sc", "sad", "club", "de", "del", "la", "le", "the", "and", "basket",
                "basketball", "baloncesto", "basquet", "bàsquet", "pallacanestro", "sport", "sports", "ksk", "bsk",
                "beko", "aktor", "playtika", "mozzart", "meridianbet", "kosner", "ea7", "emporio", "armani",
                "segafredo", "kaunas", "piraeus", "athens", "belgrade", "beograd", "tel", "aviv", "1939", "1929"}
_FRASES_ALIAS = {"red star": "zvezda", "crvena zvezda": "zvezda", "olimpia milano": "milan", "milano": "milan",
                 "munchen": "munich", "münchen": "munich", "istanbul": "", "saski baskonia": "baskonia",
                 "ldlc asvel": "asvel", "villeurbanne": "asvel", "los angeles": "la", "la clippers": "clippers",
                 "la lakers": "lakers", "unicaja malaga": "unicaja", "real madrid": "realmadrid",
                 "gran canaria": "grancanaria", "la laguna tenerife": "tenerife"}
_ALIAS_TOKENS = {"internazionale": "inter"}


def _tokens_equipo(nombre):
    t = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode("ascii").lower()
    for ch in ["&", "-", ".", "/", "'", "(", ")"]:
        t = t.replace(ch, " ")
    t = " " + " ".join(t.split()) + " "
    for frase, rep in _FRASES_ALIAS.items():
        t = t.replace(f" {frase} ", f" {rep} ")
    toks = {_ALIAS_TOKENS.get(x, x) for x in t.split() if x not in _STOP_TOKENS}
    return toks


def equipos_equivalentes(a, b):
    ta, tb = _tokens_equipo(a), _tokens_equipo(b)
    if not ta or not tb:
        return False
    if ta == tb or ta <= tb or tb <= ta:
        return True
    if len(ta & tb) / len(ta | tb) >= 0.6:
        return True
    return difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio() >= 0.85


def odds_get(path, params):
    if not THE_ODDS_API_KEY:
        return None, None
    q = dict(params)
    q["apiKey"] = THE_ODDS_API_KEY
    try:
        res = requests.get(f"{ODDS_API_BASE}{path}", params=q, timeout=15)
    except Exception as e:
        print(f"⚠️ Error The Odds API ({path}): {e}")
        return None, None
    rest = res.headers.get("x-requests-remaining")
    if rest is not None:
        try:
            ODDS_ESTADO["restantes"] = float(rest)
        except Exception:
            pass
    if res.status_code == 200:
        return res.json(), 200
    print(f"⚠️ The Odds API {res.status_code} en {path}: {res.text[:160]}")
    return None, res.status_code


def buscar_evento_odds(sport_key, local, visitante, inicio_utc, cache_eventos):
    if sport_key not in cache_eventos:
        data, _ = odds_get(f"/sports/{sport_key}/events", {"dateFormat": "iso"})
        cache_eventos[sport_key] = data if isinstance(data, list) else []
    for ev in cache_eventos[sport_key]:
        if not (equipos_equivalentes(ev.get("home_team"), local) and equipos_equivalentes(ev.get("away_team"), visitante)):
            continue
        if inicio_utc is not None:
            try:
                t = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
                if abs((t - inicio_utc).total_seconds()) > 6 * 3600:
                    continue
            except Exception:
                pass
        return ev
    return None



# ---------------------------------------------------------------------------
# Perfiles de mercados (cada mercado consultado cuesta 1 crédito por región y partido)
# ---------------------------------------------------------------------------
PERFILES_MERCADOS = {
    "reducido": "h2h,spreads,totals",                      # ganador + hándicap + total de puntos = 3 créditos/partido
    "basico": "h2h,spreads,totals",
    "medio": "h2h,spreads,totals,alternate_spreads,alternate_totals",
    "completo": "h2h,spreads,totals,alternate_spreads,alternate_totals,team_totals,alternate_team_totals",
}
ODDS_PERFIL = os.getenv("ODDS_PERFIL", "reducido")      # reducido | auto | basico | medio | completo
# Familias de mercado que se analizan (el resto se ignora aunque la casa los ofrezca)
MERCADOS_ACTIVOS = {m.strip().upper() for m in os.getenv("MERCADOS_ACTIVOS", "ML,HC,TOT,TT").split(",") if m.strip()}
ODDS_MARKETS_FORZADOS = os.getenv("ODDS_MARKETS", "")   # si se define, sustituye al perfil


def mercados_a_consultar():
    if ODDS_MARKETS_FORZADOS:
        return ODDS_MARKETS_FORZADOS
    perfil = ODDS_PERFIL
    if perfil == "auto":
        r = ODDS_ESTADO["restantes"]
        perfil = "basico" if r is None else ("completo" if r >= 4000 else ("medio" if r >= 1500 else "basico"))
    ODDS_ESTADO["perfil"] = perfil
    return PERFILES_MERCADOS.get(perfil, PERFILES_MERCADOS["basico"])


def consultar_cuotas_evento(sport_key, event_id):
    base = mercados_a_consultar()
    intentos = [base, "h2h,spreads,totals", "h2h,totals", "h2h"]
    vistos, ODDS_ESTADO["consultas"] = [], ODDS_ESTADO["consultas"] + 1
    for mk in intentos:
        if mk in vistos:
            continue
        vistos.append(mk)
        params = {"regions": ODDS_REGIONS, "markets": mk, "oddsFormat": "decimal", "dateFormat": "iso"}
        data, status = odds_get(f"/sports/{sport_key}/events/{event_id}/odds", params)
        if data:
            return data
        if status != 422:
            break
        print(f"   Aviso: la API rechazó algún mercado de «{mk}» (422); se prueba un perfil más pequeño")
    return None


def _lado_equipo(descr, home, away):
    d = (descr or "").strip()
    if not d:
        return None
    if equipos_equivalentes(d, home):
        return "L"
    if equipos_equivalentes(d, away):
        return "V"
    return None


def parsear_bookmaker(bk, ev):
    """Convierte los mercados de una casa en grupos {clave_grupo: {seleccion: cuota}}.
    Claves: ("ML", "FT")  ("HC", "FT", linea_del_local)  ("TOT", "FT", linea)  ("TT", "FT", L|V, linea)"""
    home, away = ev.get("home_team") or "", ev.get("away_team") or ""
    hl, al = home.lower(), away.lower()
    g = {}

    def put(gk, sel, precio):
        g.setdefault(gk, {}).setdefault(sel, precio)

    for m in bk.get("markets", []):
        key = m.get("key") or ""
        for o in m.get("outcomes", []):
            precio = o.get("price")
            if not precio or precio <= 1.0:
                continue
            low = (o.get("name") or "").strip().lower()
            punto = o.get("point")
            lado_ou = "O" if low == "over" else ("U" if low == "under" else None)
            if key == "h2h":
                if low == hl:
                    put(("ML", "FT"), "1", precio)
                elif low == al:
                    put(("ML", "FT"), "2", precio)
            elif key in ("totals", "alternate_totals"):
                if punto is not None and lado_ou:
                    put(("TOT", "FT", float(punto)), lado_ou, precio)
            elif key in ("team_totals", "alternate_team_totals"):
                eq = _lado_equipo(o.get("description"), home, away)
                if eq and punto is not None and lado_ou:
                    put(("TT", "FT", eq, float(punto)), lado_ou, precio)
            elif key in ("spreads", "alternate_spreads"):
                if punto is None:
                    continue
                if low == hl:
                    put(("HC", "FT", float(punto)), "H", precio)
                elif low == al:
                    put(("HC", "FT", -float(punto)), "A", precio)
    return g


def _quitar_margen(precios):
    inv = {k: 1.0 / v for k, v in precios.items() if v and v > 1.0}
    if len(inv) != len(precios) or not inv:
        return None
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s > 0 else None


def _consenso(otros, extractor, minimo=None):
    acum, peso_total, n = {}, 0.0, 0
    for key, b in otros:
        precios = extractor(b)
        if not precios:
            continue
        pr = _quitar_margen(precios)
        if not pr:
            continue
        w = PESOS_CASAS.get(key, 1.0)
        n += 1
        peso_total += w
        for k, v in pr.items():
            acum[k] = acum.get(k, 0.0) + v * w
    if n < (minimo or MIN_CASAS_CONSENSO) or peso_total <= 0:
        return None, n
    return {k: v / peso_total for k, v in acum.items()}, n


def ev_y_stake(cuota, prob, push=0.0):
    """EV por unidad apostada (con devolución si hay push) y stake Kelly 1/4 (1-10)."""
    ev = prob * cuota + push - 1.0
    if ev <= 0:
        return round(ev * 100, 1), 0
    b = cuota - 1.0
    q = max(0.0, 1.0 - prob - push)
    kelly = (b * prob - q) / b
    stake = min(10, max(1, round(max(0.0, kelly * 0.25) * 40)))
    return round(ev * 100, 1), stake


# ==============================================================================
# MODELO ESTADÍSTICO PROPIO DE BALONCESTO
# Rating ofensivo y defensivo de cada equipo (puntos a favor y en contra ajustados
# por rival y por campo, con más peso a los partidos recientes) -> puntos esperados
# de cada equipo -> simulación de 12.000 partidos (distribución normal con la
# dispersión típica de cada liga, prórroga incluida si hay empate).
# Una apuesta solo se confirma si el modelo, por sí solo, también ve valor a esa cuota.
# ==============================================================================
STATS_PESO_MERCADO = float(os.getenv("STATS_PESO_MERCADO", "0.65"))   # peso del consenso en la probabilidad final
STATS_OBLIGATORIAS = os.getenv("STATS_OBLIGATORIAS", "1") == "1"      # alerta solo con respaldo estadístico
STATS_MIN_PARTIDOS = int(os.getenv("STATS_MIN_PARTIDOS", "4"))        # partidos mínimos por equipo
STATS_PRIOR_PARTIDOS = 5.0                                            # "partidos virtuales" de regresión a la media
STATS_VIDA_MEDIA_DIAS = 45.0                                          # peso de un partido: se reduce a la mitad cada 45 días
N_SIMULACIONES = 12000
ARCHIVO_RESULTADOS = "resultados.json"
RESULTADOS_DIAS_MAX = 330


def cargar_resultados():
    if os.path.exists(ARCHIVO_RESULTADOS):
        try:
            with open(ARCHIVO_RESULTADOS, "r", encoding="utf-8") as f:
                d = json.load(f)
                d.setdefault("ligas", {})
                d.setdefault("relleno", {})
                return d
        except Exception as e:
            print(f"Aviso leyendo {ARCHIVO_RESULTADOS}: {e}")
    return {"version": 1, "ligas": {}, "relleno": {}}


def guardar_resultados(res):
    limite = (datetime.now(timezone.utc) - timedelta(days=RESULTADOS_DIAS_MAX)).strftime("%Y-%m-%d")
    for liga, lst in res["ligas"].items():
        res["ligas"][liga] = sorted([r for r in lst if (r.get("u") or "")[:10] >= limite], key=lambda r: r.get("u") or "")
    try:
        with open(ARCHIVO_RESULTADOS, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        print(f"Aviso guardando {ARCHIVO_RESULTADOS}: {e}")


def registrar_resultado(res, m):
    """Guarda un partido finalizado (formato interno) en el histórico de resultados de su liga."""
    comp = (m.get("competition") or {}).get("code")
    ft = (m.get("score") or {}).get("fullTime") or {}
    h, a = ft.get("home"), ft.get("away")
    hid, aid = (m.get("homeTeam") or {}).get("id"), (m.get("awayTeam") or {}).get("id")
    if comp not in LEAGUES_MAP or m.get("status") != "FINISHED" or h is None or a is None or not hid or not aid:
        return False
    if h == a or h < 30 or a < 30:
        return False   # marcador incompleto o erróneo
    lst = res["ligas"].setdefault(comp, [])
    mid = str(m.get("id"))
    for r in lst:
        if r.get("id") == mid:
            if r.get("h") != h or r.get("a") != a:
                r["h"], r["a"] = h, a
            return False
    lst.append({"id": mid, "u": m.get("utcDate") or "", "hid": str(hid), "aid": str(aid), "h": int(h), "a": int(a),
                "hn": (m.get("homeTeam") or {}).get("name"), "an": (m.get("awayTeam") or {}).get("name")})
    return True


def construir_fuerzas(comp_code, resultados):
    cfg = LEAGUES_MAP[comp_code]
    lst = resultados["ligas"].get(comp_code, [])
    ahora = datetime.now(timezone.utc)
    juegos = []
    for r in lst:
        try:
            t = datetime.fromisoformat(r["u"].replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            dias = max(0.0, (ahora - t).total_seconds() / 86400.0)
        except Exception:
            dias = 30.0
        juegos.append((r["hid"], r["aid"], r["h"], r["a"], 0.5 ** (dias / STATS_VIDA_MEDIA_DIAS)))
    n = len(juegos)
    if n < 12:
        return {"valido": False, "n": n}
    wsum = sum(w for *_, w in juegos)
    mu = sum((h + a) * w for _, _, h, a, w in juegos) / (2.0 * wsum)
    hadv = sum((h - a) * w for _, _, h, a, w in juegos) / wsum
    hadv = 0.5 * hadv + 0.5 * cfg["ventaja_local"]           # prudencia: mezcla con el valor típico de la liga
    of, de, cnt = {}, {}, {}
    for hid, aid, *_ in juegos:
        for t in (hid, aid):
            of.setdefault(t, 0.0)
            de.setdefault(t, 0.0)
            cnt[t] = cnt.get(t, 0) + 1
    K = STATS_PRIOR_PARTIDOS
    for _ in range(25):
        acc_o = {t: 0.0 for t in of}
        acc_d = {t: 0.0 for t in of}
        acc_w = {t: 0.0 for t in of}
        for hid, aid, h, a, w in juegos:
            esp_h = mu + hadv / 2.0
            esp_a = mu - hadv / 2.0
            acc_o[hid] += w * (h - esp_h - de[aid])
            acc_d[aid] += w * (h - esp_h - of[hid])
            acc_o[aid] += w * (a - esp_a - de[hid])
            acc_d[hid] += w * (a - esp_a - of[aid])
            acc_w[hid] += w
            acc_w[aid] += w
        for t in of:
            of[t] = acc_o[t] / (acc_w[t] + K)
            de[t] = acc_d[t] / (acc_w[t] + K)
    # dispersión real de la liga (si hay datos suficientes), con un mínimo prudente
    sd_m, sd_t = cfg["sd_margen"], cfg["sd_total"]
    if n >= 60:
        rm, rt = [], []
        for hid, aid, h, a, w in juegos:
            ph = mu + hadv / 2.0 + of[hid] + de[aid]
            pa = mu - hadv / 2.0 + of[aid] + de[hid]
            rm.append((h - a) - (ph - pa))
            rt.append((h + a) - (ph + pa))
        sd_m = max(cfg["sd_margen"] * 0.85, math.sqrt(sum(x * x for x in rm) / len(rm)))
        sd_t = max(cfg["sd_total"] * 0.85, math.sqrt(sum(x * x for x in rt) / len(rt)))
    return {"valido": True, "n": n, "mu": mu, "hadv": hadv, "of": of, "de": de, "cnt": cnt, "sd_m": sd_m, "sd_t": sd_t}


def obtener_fuerzas_liga(comp_code, cache_stats):
    if comp_code not in cache_stats:
        cache_stats[comp_code] = construir_fuerzas(comp_code, cache_stats["_res"])
    return cache_stats[comp_code]


def calcular_modelo_estadistico(comp_code, local_id, visitante_id, cache_stats):
    f = obtener_fuerzas_liga(comp_code, cache_stats)
    lid, vid = str(local_id), str(visitante_id)
    if not f or not f.get("valido") or lid not in f["of"] or vid not in f["of"]:
        return None
    if f["cnt"].get(lid, 0) < STATS_MIN_PARTIDOS or f["cnt"].get(vid, 0) < STATS_MIN_PARTIDOS:
        return None
    mu, hadv = f["mu"], f["hadv"]
    lam_h = mu + hadv / 2.0 + f["of"][lid] + f["de"][vid]
    lam_a = mu - hadv / 2.0 + f["of"][vid] + f["de"][lid]
    sd_m, sd_t = f["sd_m"], f["sd_t"]
    var_eq = (sd_t ** 2 + sd_m ** 2) / 4.0
    cov = (sd_t ** 2 - sd_m ** 2) / 4.0
    s = math.sqrt(var_eq)
    rho = max(-0.95, min(0.95, cov / var_eq))
    rng = random.Random(f"{comp_code}|{lid}|{vid}|{round(lam_h, 2)}|{round(lam_a, 2)}")
    min_ot = 5.0 / LEAGUES_MAP[comp_code]["min_partido"]
    muestras = []
    for _ in range(N_SIMULACIONES):
        z1 = rng.gauss(0.0, 1.0)
        z2 = rho * z1 + math.sqrt(1.0 - rho * rho) * rng.gauss(0.0, 1.0)
        i = int(round(lam_h + s * z1))
        j = int(round(lam_a + s * z2))
        prorroga = 0
        while i == j and prorroga < 4:   # prórroga de 5 minutos
            i += int(round(rng.gauss(lam_h * min_ot, 3.0)))
            j += int(round(rng.gauss(lam_a * min_ot, 3.0)))
            prorroga += 1
        if i == j:
            i += 1 if rng.random() < 0.5 else 0
            j += 0 if i != j else 1
        muestras.append((i, j))
    return {"muestras": muestras, "xg_local": round(lam_h, 1), "xg_visitante": round(lam_a, 1),
            "margen": round(lam_h - lam_a, 1), "total": round(lam_h + lam_a, 1)}


# ==============================================================================
# SELECCIONES: especificación, probabilidad del modelo, texto y liquidación
# spec = {"t": "ML"|"HC"|"TOT"|"TT"|"MULTI", "s": lado, "l": línea, "team": "L"|"V"}
# ==============================================================================
def pred_win(spec):
    t, s, l = spec["t"], spec.get("s"), spec.get("l")
    if t == "ML":
        return (lambda i, j: i > j) if s == "1" else (lambda i, j: i < j)
    if t == "TOT":
        return (lambda i, j: i + j > l) if s == "O" else (lambda i, j: i + j < l)
    if t == "TT":
        if spec["team"] == "L":
            return (lambda i, j: i > l) if s == "O" else (lambda i, j: i < l)
        return (lambda i, j: j > l) if s == "O" else (lambda i, j: j < l)
    if t == "HC":
        if spec["team"] == "L":
            return lambda i, j: (i + l - j) > 0
        return lambda i, j: (j + l - i) > 0
    return lambda i, j: False


def pred_push(spec):
    t, l = spec["t"], spec.get("l")
    if l is None or not float(l).is_integer():
        return None
    if t == "HC":
        if spec["team"] == "L":
            return lambda i, j: (i + l - j) == 0
        return lambda i, j: (j + l - i) == 0
    if t == "TOT":
        return lambda i, j: (i + j) == l
    if t == "TT":
        return (lambda i, j: i == l) if spec["team"] == "L" else (lambda i, j: j == l)
    return None


def prob_modelo_sel(modelo, spec):
    """(prob_ganar, prob_devolución) según el modelo, o (None, None) si no hay modelo."""
    if not modelo or spec["t"] == "MULTI":
        return None, None
    M = modelo["muestras"]
    pw, pp = pred_win(spec), pred_push(spec)
    win = sum(1 for i, j in M if pw(i, j)) / len(M)
    push = (sum(1 for i, j in M if pp(i, j)) / len(M)) if pp else 0.0
    return win, push


def prob_conjunta_modelo(modelo, specs):
    M = modelo["muestras"]
    preds = [pred_win(s) for s in specs]
    return sum(1 for i, j in M if all(p(i, j) for p in preds)) / len(M)


def describir_sel(spec, local, visitante):
    t, s, l = spec["t"], spec.get("s"), spec.get("l")
    if t == "ML":
        return f"Victoria {local if s == '1' else visitante} (Ganador)"
    if t == "TOT":
        return f"Más de {l:g} Puntos (Over {l:g})" if s == "O" else f"Menos de {l:g} Puntos (Under {l:g})"
    if t == "TT":
        eq = local if spec["team"] == "L" else visitante
        return f"{eq}: Más de {l:g} Puntos" if s == "O" else f"{eq}: Menos de {l:g} Puntos"
    if t == "HC":
        eq = local if spec["team"] == "L" else visitante
        return f"Hándicap {eq} {l:+g}"
    if t == "MULTI":
        return " + ".join(describir_sel(x, local, visitante) for x in spec["legs"])
    return str(spec)


def tipo_y_categoria(spec):
    t = spec["t"]
    if t == "ML":
        return "GANADOR", "ganador"
    if t == "HC":
        return "HANDICAP", "handicap"
    if t == "TOT":
        return "PUNTOS", "puntos"
    if t == "TT":
        return "PUNTOS_EQUIPO", "puntos_equipo"
    return "COMBO_MERCADOS", "combo_mercados"


def equipo_de_sel(spec, local, visitante):
    t = spec["t"]
    if t == "ML":
        return local if spec["s"] == "1" else visitante
    if t in ("TT", "HC"):
        return local if spec["team"] == "L" else visitante
    return None


def resolver_seleccion(spec, gl, gv, hl=None, hv=None):
    """True / False / 'PUSH' / None (None = no se puede liquidar todavía)."""
    if spec["t"] == "MULTI":
        res = [resolver_seleccion(x, gl, gv) for x in spec["legs"]]
        if any(r is False for r in res):
            return False
        return True if all(r is True for r in res) else None
    if gl is None or gv is None:
        return None
    if pred_win(spec)(gl, gv):
        return True
    pp = pred_push(spec)
    if pp and pp(gl, gv):
        return "PUSH"
    return False


def evaluar_pick(sel, mercado, tipo, gl, gv, hl=None, hv=None, nombre_local="", nombre_visitante="", equipo_pronosticado=None):
    if sel:
        return resolver_seleccion(sel, gl, gv)
    if gl is None or gv is None:
        return None
    # respaldo por texto (no debería hacer falta: todas las alertas guardan su selección)
    if equipo_pronosticado and equipos_equivalentes(equipo_pronosticado, nombre_visitante):
        return gv > gl
    return gl > gv


# ==============================================================================
# GENERACIÓN DE CANDIDATOS (todos los mercados disponibles)
# ==============================================================================
ESPERADOS_GRUPO = {"ML": 2, "TOT": 2, "TT": 2, "HC": 2}
MIN_CASAS_NICHO = int(os.getenv("ODDS_MIN_CASAS_NICHO", "3"))
COMBO_MERC_ACTIVO = os.getenv("COMBO_MERCADOS_ACTIVO", "1") == "1"
COMBO_MERC_PROB_MIN = float(os.getenv("COMBO_MERCADOS_PROB_MIN", "0.35"))
COMBO_MERC_MARGEN = 1.05      # la cuota mínima exige un 5% de ventaja sobre la cuota justa del modelo
COMBO_MERC_HAIRCUT = 0.90     # recorte prudente a la cuota de referencia (producto de cuotas)


def evaluar_seleccion(cuota, p_mercado, p_mod, push=0.0):
    """Mezcla consenso y modelo. Devuelve (prob_final, confirmado_por_el_modelo)."""
    if p_mod is not None:
        return STATS_PESO_MERCADO * p_mercado + (1.0 - STATS_PESO_MERCADO) * p_mod, (p_mod * cuota + push) > 1.0
    return p_mercado, False


def _spec_desde_grupo(gk, sel):
    tg = gk[0]
    if tg not in MERCADOS_ACTIVOS:
        return None
    if tg == "ML":
        return {"t": "ML", "s": sel}
    if tg == "TOT":
        return {"t": "TOT", "s": sel, "l": gk[2]}
    if tg == "TT":
        return {"t": "TT", "team": gk[2], "s": sel, "l": gk[3]}
    if tg == "HC":
        lh = gk[2]
        return {"t": "HC", "team": "L" if sel == "H" else "V", "l": lh if sel == "H" else -lh}
    return None


def _familia(spec):
    if spec["t"] in ("ML", "HC"):
        return "RES"
    if spec["t"] in ("TOT", "TT"):
        return "PTS"
    return None


def generar_candidatos(ev_odds, local, visitante, modelo=None):
    books, titulos = {}, {}
    for bk in ev_odds.get("bookmakers", []):
        books[bk["key"]] = parsear_bookmaker(bk, ev_odds)
        titulos[bk["key"]] = bk.get("title") or bk["key"]
    print(f"   Casas disponibles en {local} vs {visitante}: {', '.join(sorted(books)) or 'ninguna'}")
    objetivos = [k for k in BOOKMAKERS_OBJETIVO if k in books]
    if not objetivos:
        return [], 0, "Ninguna casa objetivo ofrece cuotas de este partido", None, None

    mejores, patas_por_casa = {}, {}
    mejor_combo = None
    n_max = 0
    tipos_vistos = set()

    for tk in objetivos:
        obj, casa = books[tk], titulos[tk]
        grupo = tk.split("_")[0]
        otros = [(k, b) for k, b in books.items() if k.split("_")[0] != grupo]
        cache = {}

        def cons_grupo(gk):
            if gk not in cache:
                esperado = ESPERADOS_GRUPO[gk[0]]
                minimo = MIN_CASAS_CONSENSO if gk[0] == "ML" else MIN_CASAS_NICHO
                cache[gk] = _consenso(otros, lambda b, g=gk, e=esperado: b.get(g) if len(b.get(g, {})) == e else None, minimo)
            return cache[gk]

        selecciones = []   # (spec, cuota, p_mercado, n)
        for gk, precios in obj.items():
            tipos_vistos.add(gk[0])
            cons, n = cons_grupo(gk)
            if not cons:
                continue
            for sel, cuota in precios.items():
                spec = _spec_desde_grupo(gk, sel)
                if spec and sel in cons:
                    selecciones.append((spec, cuota, cons[sel], n))
        pool_combo, cands_casa = [], []
        for spec, cuota, p_merc, n in selecciones:
            n_max = max(n_max, n)
            p_mod, pp = prob_modelo_sel(modelo, spec)
            push = 0.0
            if pred_push(spec) is not None:
                if p_mod is None:
                    continue
                push = pp
                p_merc = p_merc * (1.0 - push)
            prob, conf = evaluar_seleccion(cuota, p_merc, p_mod, push)
            ev, stake = ev_y_stake(cuota, prob, push)
            if modelo and push == 0.0 and _familia(spec):
                pool_combo.append({"spec": spec, "cuota": cuota, "prob": prob, "p_mod": p_mod, "ev_leg": prob * cuota - 1.0})
            if ev <= 0 or ev > EV_MAXIMO_PCT or cuota > CUOTA_MAXIMA or prob < PROB_MINIMA:
                continue
            tipo, cat = tipo_y_categoria(spec)
            texto = describir_sel(spec, local, visitante)
            cand = {"mercado": texto, "tipo_mercado": tipo, "categoria_visual": cat, "sel": spec,
                    "equipo_pronosticado": equipo_de_sel(spec, local, visitante), "cuota": round(cuota, 2),
                    "prob_modelo": f"{round(prob * 100, 1)}%", "ev": ev, "stake": stake,
                    "prob_mercado": round(p_merc * 100, 1),
                    "prob_estadistica": round(p_mod * 100, 1) if p_mod is not None else None,
                    "confirmado_stats": bool(conf), "sin_modelo_ok": False,
                    "xg_local": modelo["xg_local"] if modelo else None,
                    "xg_visitante": modelo["xg_visitante"] if modelo else None,
                    "icono_tg": "🏀", "n_casas": n, "casa": casa,
                    "prob_final": round(prob, 4), "score": round(ev * math.sqrt(prob), 3)}
            cands_casa.append(cand)
            previo = mejores.get(texto)
            if previo is None or (cand["confirmado_stats"], cand["ev"]) > (previo["confirmado_stats"], previo["ev"]):
                mejores[texto] = cand

        # patas válidas para las combinadas de partidos (máx. 2 por casa)
        legs = [c for c in cands_casa
                if c["confirmado_stats"] and c["prob_final"] >= COMBI_PROB_PATA_MIN
                and COMBI_CUOTA_PATA[0] <= c["cuota"] <= COMBI_CUOTA_PATA[1] and c["ev"] >= COMBI_EV_PATA_MIN
                and pred_push(c["sel"]) is None]
        legs = sorted(legs, key=lambda c: c["score"], reverse=True)[:2]
        if legs:
            patas_por_casa[casa] = [{"mercado": c["mercado"], "tipo": c["tipo_mercado"], "sel": c["sel"],
                                     "equipo": c["equipo_pronosticado"], "cuota": c["cuota"], "prob": c["prob_final"],
                                     "ev": c["ev"], "confirmado": True} for c in legs]

        # combinado de mercados del mismo partido (ganador/hándicap + puntos)
        if COMBO_MERC_ACTIVO and modelo:
            por_fam = {}
            for it in pool_combo:
                if it["prob"] >= 0.50 and it["ev_leg"] >= 0.0 and it["p_mod"]:
                    por_fam.setdefault(_familia(it["spec"]), []).append(it)
            for fam in por_fam:
                por_fam[fam] = sorted(por_fam[fam], key=lambda x: x["prob"], reverse=True)[:3]
            fams = [f for f in ("RES", "PTS") if f in por_fam]
            if len(fams) == 2:
                for elegidas in itertools.product(*[por_fam[f] for f in fams]):
                    specs = [e["spec"] for e in elegidas]
                    joint = prob_conjunta_modelo(modelo, specs)
                    ajuste = 1.0
                    for e in elegidas:
                        ajuste *= max(0.85, min(1.15, e["prob"] / e["p_mod"]))
                    p_joint = min(0.95, joint * ajuste)
                    if p_joint < COMBO_MERC_PROB_MIN:
                        continue
                    cuota_ref = math.prod(e["cuota"] for e in elegidas)
                    cuota_min = COMBO_MERC_MARGEN / p_joint
                    ev_est = (p_joint * cuota_ref * COMBO_MERC_HAIRCUT - 1.0) * 100.0
                    if cuota_ref * COMBO_MERC_HAIRCUT < cuota_min or ev_est > 60:
                        continue
                    score = ev_est * math.sqrt(p_joint)
                    if mejor_combo is None or score > mejor_combo["score"]:
                        spec_m = {"t": "MULTI", "legs": specs}
                        mejor_combo = {"mercado": describir_sel(spec_m, local, visitante), "tipo_mercado": "COMBO_MERCADOS",
                                       "categoria_visual": "combo_mercados", "sel": spec_m, "equipo_pronosticado": None,
                                       "cuota": round(cuota_min, 2), "cuota_ref": round(cuota_ref, 2), "cuota_minima": True,
                                       "prob_modelo": f"{round(p_joint * 100, 1)}%",
                                       "ev": round((COMBO_MERC_MARGEN - 1.0) * 100, 1), "ev_estimado": round(ev_est, 1), "stake": 1,
                                       "prob_mercado": None, "prob_estadistica": round(joint * 100, 1),
                                       "confirmado_stats": True, "sin_modelo_ok": False,
                                       "xg_local": modelo["xg_local"], "xg_visitante": modelo["xg_visitante"],
                                       "icono_tg": "🧩", "n_casas": n_max, "casa": casa,
                                       "prob_final": round(p_joint, 4), "score": round(score, 3)}

    print(f"   Tipos de mercado recibidos: {', '.join(sorted(tipos_vistos)) or 'ninguno'}")
    fav = {"por_casa": patas_por_casa} if patas_por_casa else None
    cands = list(mejores.values())
    if not cands and not mejor_combo:
        if n_max < MIN_CASAS_CONSENSO:
            return [], n_max, f"Pocas casas para el consenso ({n_max}/{MIN_CASAS_CONSENSO})", fav, None
        return [], n_max, "Sin valor: ninguna casa supera al consenso del mercado", fav, None
    return cands, n_max, None, fav, mejor_combo


def es_alertable(c):
    if c.get("cuota_minima"):   # combinado de mercados / pronóstico del modelo: ya exigen su propio margen mínimo
        return True
    return (c["ev"] >= EV_MINIMO_PCT and (c.get("confirmado_stats") or not STATS_OBLIGATORIAS))


ODDS_DIA_RESET = int(os.getenv("ODDS_DIA_RESET", "1"))        # día del mes en que se renuevan los créditos
ODDS_FACTOR_PRESUPUESTO = float(os.getenv("ODDS_FACTOR_PRESUPUESTO", "1.25"))


def dias_hasta_reinicio(hoy):
    """Días (>= 1) que faltan para que se renueven los créditos de The Odds API."""
    reset = hoy.replace(day=min(ODDS_DIA_RESET, 28))
    if hoy.day >= reset.day:
        mes = hoy.month % 12 + 1
        anio = hoy.year + (1 if hoy.month == 12 else 0)
        reset = hoy.replace(year=anio, month=mes, day=min(ODDS_DIA_RESET, 28))
    return max(1, (reset - hoy).days)


def preparar_presupuesto(datos_previos, ahora_madrid):
    """Reparte los créditos que quedan entre los días que faltan para la renovación (lo no gastado un día se acumula)."""
    r = ODDS_ESTADO["restantes"]
    hoy_str = ahora_madrid.strftime("%Y-%m-%d")
    dia = dict(datos_previos.get("creditos_dia") or {})
    if r is None:
        ODDS_ESTADO["dia"], ODDS_ESTADO["presupuesto_dia"] = dia or None, None
        return
    if dia.get("fecha") != hoy_str or r > dia.get("restante_inicio", r):   # día nuevo o plan renovado
        dia = {"fecha": hoy_str, "restante_inicio": r}
    ODDS_ESTADO["dia"] = dia
    ODDS_ESTADO["presupuesto_dia"] = dia["restante_inicio"] / dias_hasta_reinicio(ahora_madrid.date() if hasattr(ahora_madrid, "date") else ahora_madrid)


def presupuesto_agotado():
    dia, pres, r = ODDS_ESTADO.get("dia"), ODDS_ESTADO.get("presupuesto_dia"), ODDS_ESTADO.get("restantes")
    if not dia or pres is None or r is None:
        return False
    gastado = dia["restante_inicio"] - r
    coste = len(mercados_a_consultar().split(","))
    return gastado > 0 and (gastado + coste) > pres * ODDS_FACTOR_PRESUPUESTO



def analizar_solo_modelo(comp_code, local, visitante, form_info, local_id, visitante_id, cache_stats):
    """Ligas sin cuotas en The Odds API (ACB y Champions League): pronóstico del modelo con CUOTA MÍNIMA.
    Solo se emite si un equipo es favorito claro (prob. >= 70% por defecto)."""
    modelo = calcular_modelo_estadistico(comp_code, local_id, visitante_id, cache_stats)
    if not modelo:
        return None, True, "Sin datos estadísticos suficientes (se necesitan varias jornadas jugadas)"
    p1, _ = prob_modelo_sel(modelo, {"t": "ML", "s": "1"})
    s = "1" if p1 >= 0.5 else "2"
    p = p1 if s == "1" else 1.0 - p1
    nota_modelo = (f"Modelo: {local} {modelo['xg_local']} - {modelo['xg_visitante']} {visitante} "
                   f"(margen {modelo['margen']:+g}, total {modelo['total']:g})")
    if not MODELO_SOLO_ACTIVO or p < MODELO_SOLO_PROB_MIN:
        return None, True, f"Sin favorito claro para el modelo ({round(p * 100)}%). {nota_modelo}"
    spec = {"t": "ML", "s": s}
    tipo, cat = tipo_y_categoria(spec)
    cuota_min = round(MODELO_SOLO_MARGEN / p, 2)
    analisis = {"mercado": describir_sel(spec, local, visitante), "tipo_mercado": tipo, "categoria_visual": cat,
                "sel": spec, "equipo_pronosticado": equipo_de_sel(spec, local, visitante),
                "cuota": cuota_min, "cuota_ref": None, "cuota_minima": True, "solo_modelo": True,
                "prob_modelo": f"{round(p * 100, 1)}%", "ev": round((MODELO_SOLO_MARGEN - 1.0) * 100, 1), "stake": 1,
                "prob_mercado": None, "prob_estadistica": round(p * 100, 1), "confirmado_stats": True,
                "sin_modelo_ok": False, "xg_local": modelo["xg_local"], "xg_visitante": modelo["xg_visitante"],
                "icono_tg": "📐", "n_casas": None, "casa": None, "form_info": form_info, "nota": nota_modelo}
    return analisis, True, nota_modelo


def analizar_partido_con_cuotas(comp_code, local, visitante, inicio_utc, form_info, cache_eventos,
                                local_id=None, visitante_id=None, cache_stats=None):
    """Devuelve (analisis|None, definitivo, nota, fav). definitivo=False -> se reintenta en el siguiente ciclo.
    Prioridad: 1) mejor pick individual alertable (valor + respaldo del modelo); 2) combinado de mercados
    del mismo partido; 3) mejor pick no alertable (grupo de control)."""
    sport_key = ODDS_SPORT_KEYS.get(comp_code)
    if not sport_key:
        a, definitivo, nota = analizar_solo_modelo(comp_code, local, visitante, form_info, local_id, visitante_id, cache_stats)
        return a, definitivo, nota, None
    if not THE_ODDS_API_KEY:
        return None, False, "Sin API de cuotas configurada", None
    if ODDS_ESTADO["consultas"] >= MAX_CONSULTAS_CICLO:
        return None, False, "Límite de consultas por ciclo", None
    if ODDS_ESTADO["restantes"] is not None and ODDS_ESTADO["restantes"] <= CREDITOS_RESERVA:
        return None, False, "Créditos de The Odds API en reserva", None
    if presupuesto_agotado():
        return None, False, "Presupuesto diario de créditos agotado (se reparte el mes)", None
    ev = buscar_evento_odds(sport_key, local, visitante, inicio_utc, cache_eventos)
    if not ev:
        return None, False, "Evento no encontrado en The Odds API", None
    modelo = None
    if local_id is not None and visitante_id is not None and cache_stats is not None:
        modelo = calcular_modelo_estadistico(comp_code, local_id, visitante_id, cache_stats)
    datos = consultar_cuotas_evento(sport_key, ev.get("id"))
    if not datos:
        return None, False, "Sin respuesta de cuotas", None
    cands, n_casas, nota, fav, combo = generar_candidatos(datos, local, visitante, modelo)
    alertables = [c for c in cands if es_alertable(c)]
    if alertables:
        mejor = max(alertables, key=lambda c: c["score"])
    elif combo:
        mejor = combo
    elif cands:
        mejor = max(cands, key=lambda c: (c["confirmado_stats"], c["score"]))
    else:
        return None, True, nota, fav
    mejor["form_info"] = form_info
    if modelo is None:
        mejor["nota"] = "Sin datos estadísticos suficientes para confirmar"
    elif not es_alertable(mejor):
        mejor["nota"] = "El modelo estadístico no respalda la apuesta" if not mejor["confirmado_stats"] else "Ventaja inferior al mínimo"
    return mejor, True, mejor.get("nota"), fav


CLAVES_ANALISIS = ("mercado", "tipo_mercado", "categoria_visual", "equipo_pronosticado", "cuota", "prob_modelo", "stake",
                   "n_casas", "casa", "prob_mercado", "prob_estadistica", "confirmado_stats", "xg_local", "xg_visitante",
                   "sel", "cuota_ref", "cuota_minima", "sin_modelo_ok", "solo_modelo")


def reconstruir_analisis(prev, form_info):
    if not prev or not prev.get("tiene_pronostico") or not prev.get("mercado"):
        return None
    a = {k: prev.get(k) for k in CLAVES_ANALISIS}
    a["categoria_visual"] = prev.get("categoria_visual", "ganador")
    a["ev"] = prev.get("ev_numero")
    a["form_info"] = form_info
    return a


AVISO_JUEGO = "🔞 <i>+18 • Juega con responsabilidad • Información estadística, no es asesoramiento • jugarbien.es</i>"


def enviar_telegram(mensaje, url_accion=None, texto_boton="📱 Abrir en Web App"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"{mensaje}\n\n{AVISO_JUEGO}",
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    destino_web = url_accion or APP_URL
    if destino_web:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": texto_boton, "url": destino_web}]
            ]
        }

    for intento in range(3):
        try:
            res = requests.post(url, json=payload, timeout=12)
            if res.status_code == 200:
                return True
            time.sleep(1.5)
        except Exception as e:
            print(f"Error Telegram intento {intento+1}: {e}")
            time.sleep(2)
    return False


def normalizar_texto(texto):
    if not texto:
        return ""
    return " ".join(sorted(_tokens_equipo(texto)))


# ==============================================================================
# CALENDARIO Y RESULTADOS (fuentes gratuitas) -> formato interno común
# m = {"id", "utcDate", "status", "detalle", "competition": {"code"},
#      "homeTeam": {"id", "name", "crest"}, "awayTeam": {...}, "score": {"fullTime": {"home", "away"}}}
# status: TIMED | IN_PLAY | HT | FINISHED | POSTPONED | CANCELLED
# ==============================================================================
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (ApexQuantBaloncesto; +https://apexquantbot.github.io/apex-quant-baloncesto/)",
                "Accept": "application/json"}
DIAG_FUENTES = {"espn_ok": 0, "espn_error": 0, "tsdb_ok": 0, "tsdb_error": 0, "tsdb_vacias": 0, "por_liga": {},
                "tsdb_cortado": False, "muestras": []}
TSDB_ULTIMA = [0.0]
TSDB_PAUSA = float(os.getenv("TSDB_PAUSA", "3.2"))    # TheSportsDB gratis: máx. 30 peticiones por minuto (vamos holgados)
TSDB_FALLOS_SEGUIDOS = [0]
INICIO_CICLO = time.time()
TIEMPO_MAX_DESCARGAS = float(os.getenv("TIEMPO_MAX_DESCARGAS", "540"))   # segundos para descargar datos en cada ciclo
TIEMPO_MAX_RELLENO = float(os.getenv("TIEMPO_MAX_RELLENO", "240"))      # segundos para rellenar histórico en cada ciclo
RELLENO_DIAS_TSDB_CICLO = int(os.getenv("RELLENO_DIAS_TSDB_CICLO", "6"))  # días de histórico (Euroliga/ACB/BCL) por ciclo


def tiempo_usado():
    return time.time() - INICIO_CICLO


def _muestra(origen, url, st, data):
    if sum(1 for m in DIAG_FUENTES["muestras"] if m["o"] == origen) < 10:
        txt = json.dumps(data, ensure_ascii=False)[:160] if data is not None else None
        DIAG_FUENTES["muestras"].append({"o": origen, "url": url[-90:], "st": st, "r": txt})
ESTADOS_CERRADOS = ("FINISHED", "CANCELLED", "POSTPONED")


def http_json(url, params=None, intentos=2):
    for intento in range(intentos):
        try:
            res = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=(5, 12))
            if res.status_code == 200:
                try:
                    return res.json(), 200
                except Exception:
                    return None, "200-no-json"
            if res.status_code == 429 and intento + 1 < intentos:
                time.sleep(3)
                continue
            return None, res.status_code
        except Exception as e:
            print(f"   Aviso red ({url.split('?')[0]}): {e}")
            if intento + 1 < intentos:
                time.sleep(1)
    return None, "sin-respuesta"


def tsdb_get(endpoint, params):
    """GET a TheSportsDB con pausa entre peticiones y "cortacircuitos": si falla varias veces seguidas
    (límite de peticiones o caída) se deja de consultar en este ciclo para no bloquear el bot."""
    if DIAG_FUENTES["tsdb_cortado"] or tiempo_usado() > TIEMPO_MAX_DESCARGAS:
        return None
    espera = TSDB_PAUSA - (time.time() - TSDB_ULTIMA[0])
    if espera > 0:
        time.sleep(espera)
    TSDB_ULTIMA[0] = time.time()
    url = f"https://www.thesportsdb.com/api/v1/json/{TSDB_KEY}/{endpoint}"
    data, st = http_json(url, params, intentos=1)
    if st == 429:
        time.sleep(15)
    _muestra("tsdb", f"{endpoint}?{'&'.join(f'{k}={v}' for k, v in params.items())}", st, data)
    if data is None:
        DIAG_FUENTES["tsdb_error"] += 1
        TSDB_FALLOS_SEGUIDOS[0] += 1
        print(f"   ⚠️ TheSportsDB {endpoint} {params}: HTTP {st}")
        if TSDB_FALLOS_SEGUIDOS[0] >= 3:
            DIAG_FUENTES["tsdb_cortado"] = True
            print("   ⛔ TheSportsDB no responde: se deja para el siguiente ciclo")
        return None
    TSDB_FALLOS_SEGUIDOS[0] = 0
    DIAG_FUENTES["tsdb_ok"] += 1
    return data


def _int_o_none(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return int(float(v))
    except Exception:
        return None


def _a_entero(v):
    return _int_o_none(v)


# ---------------------------- ESPN (NBA) -------------------------------------
def _estado_espn(comp):
    st = (comp.get("status") or {}).get("type") or {}
    nombre = (st.get("name") or "").upper()
    state = st.get("state")
    detalle = st.get("shortDetail") or st.get("detail") or ""
    if "POSTPONED" in nombre:
        return "POSTPONED", detalle
    if "CANCEL" in nombre or "FORFEIT" in nombre:
        return "CANCELLED", detalle
    if state == "post" or st.get("completed"):
        return "FINISHED", detalle
    if "HALFTIME" in nombre:
        return "HT", "Descanso"
    if state == "in":
        return "IN_PLAY", detalle
    return "TIMED", detalle


def espn_partidos_dia(fecha_yyyymmdd):
    if tiempo_usado() > TIEMPO_MAX_DESCARGAS:
        return []
    data, st = http_json("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                         {"dates": fecha_yyyymmdd, "limit": 100})
    _muestra("espn", f"scoreboard?dates={fecha_yyyymmdd}", st, None if data is None else {"events": len(data.get("events") or [])})
    if data is None:
        DIAG_FUENTES["espn_error"] += 1
        print(f"   ⚠️ ESPN NBA {fecha_yyyymmdd}: HTTP {st}")
        return []
    DIAG_FUENTES["espn_ok"] += 1
    salida = []
    for ev in data.get("events", []) or []:
        try:
            comp = (ev.get("competitions") or [{}])[0]
            status, detalle = _estado_espn(comp)
            if status == "TIMED":
                status_ev, det_ev = _estado_espn(ev)
                if status_ev != "TIMED":
                    status, detalle = status_ev, det_ev
            eq = {c.get("homeAway"): c for c in comp.get("competitors", [])}
            h, a = eq.get("home") or {}, eq.get("away") or {}
            th, ta = h.get("team") or {}, a.get("team") or {}
            sh, sa = _int_o_none(h.get("score")), _int_o_none(a.get("score"))
            if status == "TIMED":
                sh = sa = None
            salida.append({
                "id": f"nba{ev.get('id')}",
                "utcDate": (comp.get("date") or ev.get("date") or "").replace("Z", ":00Z") if len(comp.get("date") or ev.get("date") or "") == 17 else (comp.get("date") or ev.get("date") or ""),
                "status": status,
                "detalle": detalle,
                "competition": {"code": "NBA"},
                "homeTeam": {"id": f"nba{th.get('id')}", "name": th.get("displayName") or th.get("name") or "Local",
                             "crest": th.get("logo") or ""},
                "awayTeam": {"id": f"nba{ta.get('id')}", "name": ta.get("displayName") or ta.get("name") or "Visitante",
                             "crest": ta.get("logo") or ""},
                "score": {"fullTime": {"home": sh, "away": sa}},
            })
        except Exception as e:
            print(f"   Aviso parseando partido ESPN: {e}")
    return salida


def espn_partido(pid):
    """Ficha individual de un partido NBA (para liquidar alertas atascadas)."""
    eid = str(pid).replace("nba", "")
    data, st = http_json("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary", {"event": eid})
    if not data:
        return None
    comp = ((data.get("header") or {}).get("competitions") or [{}])[0]
    if not comp:
        return None
    status, detalle = _estado_espn(comp)
    eq = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    h, a = eq.get("home") or {}, eq.get("away") or {}
    th, ta = h.get("team") or {}, a.get("team") or {}
    logo = lambda t: t.get("logo") or ((t.get("logos") or [{}])[0].get("href") or "")
    return {"id": f"nba{eid}", "utcDate": comp.get("date") or "", "status": status, "detalle": detalle,
            "competition": {"code": "NBA"},
            "homeTeam": {"id": f"nba{th.get('id')}", "name": th.get("displayName") or "Local", "crest": logo(th)},
            "awayTeam": {"id": f"nba{ta.get('id')}", "name": ta.get("displayName") or "Visitante", "crest": logo(ta)},
            "score": {"fullTime": {"home": _int_o_none(h.get("score")) if status != "TIMED" else None,
                                   "away": _int_o_none(a.get("score")) if status != "TIMED" else None}}}


# ---------------------------- TheSportsDB (Euroliga, ACB, BCL) ----------------
_TSDB_FIN = {"FT", "AOT", "AP", "MATCH FINISHED", "FINISHED", "AFTER OVER TIME", "AFTER OVERTIME", "FINAL", "ENDED", "FT_PEN"}
_TSDB_VIVO = {"Q1", "Q2", "Q3", "Q4", "OT", "1H", "2H", "BT", "LIVE", "IN PROGRESS", "INPLAY", "1Q", "2Q", "3Q", "4Q"}


def _utc_tsdb(e):
    ts = (e.get("strTimestamp") or "").strip()
    if ts:
        ts = ts.replace(" ", "T")
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        try:
            t = datetime.fromisoformat(ts)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t.astimezone(timezone.utc)
        except Exception:
            pass
    d, h = (e.get("dateEvent") or "").strip(), (e.get("strTime") or "").strip()
    if d:
        try:
            hh = (h or "00:00:00")[:8]
            if len(hh) == 5:
                hh += ":00"
            t = datetime.fromisoformat(f"{d}T{hh}")
            return t.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def tsdb_a_partido(e, comp_code, ahora_utc):
    t = _utc_tsdb(e)
    sh, sa = _int_o_none(e.get("intHomeScore")), _int_o_none(e.get("intAwayScore"))
    est = (e.get("strStatus") or "").strip().upper()
    duracion = LEAGUES_MAP[comp_code]["duracion_min"]
    mins = (ahora_utc - t).total_seconds() / 60.0 if t else None
    if "POSTP" in est or est == "PST":
        status = "POSTPONED"
    elif "CANC" in est or "ABAND" in est:
        status = "CANCELLED"
    elif est in _TSDB_FIN or (est.startswith("FT") and est != "FT_"):
        status = "FINISHED"
    elif est == "HT":
        status = "HT"
    elif est in _TSDB_VIVO:
        status = "IN_PLAY"
    elif sh is not None and sa is not None and mins is not None and mins > duracion + 25 and (sh > 0 or sa > 0):
        status = "FINISHED"   # TheSportsDB a veces no actualiza el estado aunque publique el resultado
    elif mins is not None and 0 <= mins <= duracion:
        status = "IN_PLAY"
    else:
        status = "TIMED"
    if status == "TIMED":
        sh = sa = None
    detalle = {"Q1": "1er cuarto", "Q2": "2º cuarto", "Q3": "3er cuarto", "Q4": "4º cuarto", "OT": "Prórroga",
               "HT": "Descanso"}.get(est, "")
    return {"id": f"tsdb{e.get('idEvent')}", "utcDate": t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else "",
            "status": status, "detalle": detalle, "competition": {"code": comp_code},
            "homeTeam": {"id": f"tsdb{e.get('idHomeTeam')}", "name": e.get("strHomeTeam") or "Local",
                         "crest": e.get("strHomeTeamBadge") or ""},
            "awayTeam": {"id": f"tsdb{e.get('idAwayTeam')}", "name": e.get("strAwayTeam") or "Visitante",
                         "crest": e.get("strAwayTeamBadge") or ""},
            "score": {"fullTime": {"home": sh, "away": sa}}}


def _eventos_de_liga(data, comp_code):
    cfg = LEAGUES_MAP[comp_code]
    eventos = []
    for clave in ("events", "results", "event"):
        v = (data or {}).get(clave)
        if isinstance(v, list):
            eventos.extend(v)
    return [e for e in eventos if isinstance(e, dict) and e.get("idEvent") and
            (str(e.get("idLeague")) == cfg["tsdb"] or (e.get("strLeague") or "") == cfg["tsdb_nombre"])]


def tsdb_partidos_dia(comp_code, fecha, ahora_utc):
    cfg = LEAGUES_MAP[comp_code]
    vistos = {}
    data = tsdb_get("eventsday.php", {"d": fecha, "l": cfg["tsdb_nombre"]})
    for e in _eventos_de_liga(data, comp_code):
        vistos[e["idEvent"]] = e
    if not vistos:
        DIAG_FUENTES["tsdb_vacias"] += 1
    return [tsdb_a_partido(e, comp_code, ahora_utc) for e in vistos.values()]


def tsdb_partidos_liga(comp_code, endpoint, ahora_utc):
    data = tsdb_get(endpoint, {"id": LEAGUES_MAP[comp_code]["tsdb"]})
    return [tsdb_a_partido(e, comp_code, ahora_utc) for e in _eventos_de_liga(data, comp_code)]


def tsdb_partido(pid, comp_code, ahora_utc):
    data = tsdb_get("lookupevent.php", {"id": str(pid).replace("tsdb", "")})
    evs = _eventos_de_liga(data, comp_code) if comp_code else ((data or {}).get("events") or [])
    if not evs:
        return None
    e = evs[0]
    cc = comp_code or LIGA_POR_TSDB.get(str(e.get("idLeague")))
    return tsdb_a_partido(e, cc, ahora_utc) if cc else None


def _fechas(desde, hasta):
    d = datetime.fromisoformat(desde).date()
    h = datetime.fromisoformat(hasta).date()
    out = []
    while d <= h:
        out.append(d)
        d += timedelta(days=1)
    return out


def obtener_partidos_completos(lunes_semana_str, domingo_semana_str, partidos_previos=None):
    """Partidos de las 4 competiciones: desde ayer hasta dentro de 3 días (los días anteriores de la semana ya
    están guardados en datos.json y se fusionan al final del ciclo; los pendientes se refrescan por ficha)."""
    ahora_utc = datetime.now(timezone.utc)
    hoy = ahora_utc.astimezone(TZ_MADRID).date()
    desde = max(datetime.fromisoformat(lunes_semana_str).date(), hoy - timedelta(days=1))
    hasta = hoy + timedelta(days=2)
    if not partidos_previos:   # primer ciclo de la semana: se recorre la semana entera
        desde = datetime.fromisoformat(lunes_semana_str).date()
    matches = {}

    # NBA (ESPN usa la fecha de EE. UU.: un partido de las 02:00 en Madrid es del día anterior allí)
    for d in _fechas((desde - timedelta(days=1)).isoformat(), hasta.isoformat()):
        for m in espn_partidos_dia(d.strftime("%Y%m%d")):
            matches[m["id"]] = m
    n_nba = sum(1 for m in matches.values() if m["competition"]["code"] == "NBA")
    DIAG_FUENTES["por_liga"]["NBA"] = n_nba
    print(f"📡 [ESPN] NBA: {n_nba} partidos ({desde - timedelta(days=1)} a {hasta})")

    # Euroliga, ACB y Champions League (TheSportsDB)
    for comp in ("EL", "ACB", "BCL"):
        antes = len(matches)
        for d in _fechas(desde.isoformat(), hasta.isoformat()):
            for m in tsdb_partidos_dia(comp, d.isoformat(), ahora_utc):
                matches[m["id"]] = m
        # complemento: próximos partidos y últimos resultados de la competición
        for endpoint in ("eventsnextleague.php", "eventspastleague.php"):
            for m in tsdb_partidos_liga(comp, endpoint, ahora_utc):
                try:
                    f = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(TZ_MADRID).date()
                except Exception:
                    continue
                if desde - timedelta(days=7) <= f <= hasta:
                    actual = matches.get(m["id"])
                    if not actual or (actual["status"] != "FINISHED" and m["status"] == "FINISHED"):
                        matches[m["id"]] = m
        n = sum(1 for m in matches.values() if m["competition"]["code"] == comp)
        DIAG_FUENTES["por_liga"][comp] = n
        print(f"📡 [TheSportsDB] {LEAGUES_MAP[comp]['name']}: {n} partidos (+{len(matches) - antes})")
    return list(matches.values())


def refrescar_partido(pid, comp_code, ahora_utc):
    pid = str(pid)
    if pid.startswith("nba"):
        return espn_partido(pid)
    if pid.startswith("tsdb"):
        return tsdb_partido(pid, comp_code, ahora_utc)
    return None


MAX_REFRESCOS_ID = int(os.getenv("MAX_REFRESCOS", "8"))


def _minutos_desde_inicio(utc_str, ahora_utc):
    try:
        ini = datetime.fromisoformat((utc_str or "").replace("Z", "+00:00"))
        if ini.tzinfo is None:
            ini = ini.replace(tzinfo=timezone.utc)
        return (ahora_utc - ini).total_seconds() / 60.0
    except Exception:
        return None


def refrescar_partidos_atascados(partidos_raw, partidos_previos, ahora_utc, alertas=None):
    """Consulta la ficha individual de partidos que llevan demasiado sin cerrarse, partidos guardados que han
    desaparecido del listado y partidos de alertas todavía sin liquidar."""
    diag = {"atascados": 0, "ausentes": 0, "alertas": 0, "refrescados": 0}
    presentes = {str(m.get("id")): m for m in partidos_raw if m.get("id")}
    liga_de = {str(m.get("id")): (m.get("competition") or {}).get("code") for m in partidos_raw}
    for pv in partidos_previos:
        liga_de.setdefault(str(pv.get("id")), pv.get("liga_code"))
    candidatos = []
    for m in partidos_raw:
        if m.get("status") in ESTADOS_CERRADOS:
            continue
        mins = _minutos_desde_inicio(m.get("utcDate"), ahora_utc)
        lim = LEAGUES_MAP.get((m.get("competition") or {}).get("code"), {}).get("duracion_min", 150) + 30
        if mins is not None and mins > lim:
            candidatos.append((mins, str(m.get("id"))))
            diag["atascados"] += 1
    for pv in partidos_previos:
        pid = str(pv.get("id"))
        if pid in presentes or pv.get("estado") == "FINISHED":
            continue
        mins = _minutos_desde_inicio(pv.get("utcDate") or pv.get("hora_utc"), ahora_utc)
        if mins is not None and mins > 170:
            candidatos.append((mins, pid))
            diag["ausentes"] += 1
    ya = {c[1] for c in candidatos}
    for a in (alertas or []):
        if a.get("finalizado"):
            continue
        ids = [l.get("id") for l in a.get("legs", [])] if a.get("es_combinada") else [a.get("id_partido")]
        for raw_id in ids:
            pid = str(raw_id or "")
            if pid and pid not in presentes and pid not in ya:
                candidatos.append((10 ** 6, pid))
                liga_de.setdefault(pid, a.get("liga_code"))
                ya.add(pid)
                diag["alertas"] += 1
    candidatos.sort(reverse=True)
    for mins, pid in candidatos[:MAX_REFRESCOS_ID]:
        try:
            m = refrescar_partido(pid, liga_de.get(pid), ahora_utc)
            if not m or not m.get("id") or (m.get("competition") or {}).get("code") not in LEAGUES_MAP:
                continue
            partidos_raw[:] = [x for x in partidos_raw if str(x.get("id")) != str(m["id"])]
            partidos_raw.append(m)
            diag["refrescados"] += 1
            print(f"   🔄 Partido {pid} refrescado por ficha individual: {m.get('status')}")
        except Exception as e:
            print(f"   Aviso refresco partido {pid}: {e}")
    return diag


def _temporadas_tsdb(ahora_utc):
    a = ahora_utc.year if ahora_utc.month >= 8 else ahora_utc.year - 1
    return [f"{a}-{a + 1}", str(a)]


def rellenar_historico(resultados, ahora_utc):
    """Descarga los resultados de la temporada para que el modelo tenga base desde el primer día.
    Primero se intenta la temporada completa de una vez; si la API no la da, se va día a día hacia atrás
    (con un tiempo máximo por ciclo: lo que falte se completa en los ciclos siguientes)."""
    hoy = ahora_utc.date()
    t0 = time.time()
    dias_hechos = resultados.setdefault("relleno_dias", {})
    presupuesto_relleno = [RELLENO_DIAS_TSDB_CICLO]
    for comp in LEAGUES_MAP:
        n = len(resultados["ligas"].get(comp, []))
        estado = resultados["relleno"].get(comp, "")
        if n >= 80 or estado == "completo":
            continue
        print(f"📚 Rellenando histórico de {LEAGUES_MAP[comp]['name']} (tiene {n} resultados)...")
        nuevos = 0
        if comp != "NBA" and not estado.startswith("temporada"):
            for temporada in _temporadas_tsdb(ahora_utc):
                data = tsdb_get("eventsseason.php", {"id": LEAGUES_MAP[comp]["tsdb"], "s": temporada})
                evs = _eventos_de_liga(data, comp)
                for e in evs:
                    nuevos += registrar_resultado(resultados, tsdb_a_partido(e, comp, ahora_utc))
                if evs:
                    print(f"   Temporada {temporada}: {len(evs)} partidos en la API")
                    break
            for m in tsdb_partidos_liga(comp, "eventspastleague.php", ahora_utc):
                nuevos += registrar_resultado(resultados, m)
            resultados["relleno"][comp] = "temporada"
        dias_max = 30 if comp == "NBA" else 42
        hechos = set(dias_hechos.get(comp, []))
        for k in range(1, dias_max + 1):
            if comp != "NBA" and presupuesto_relleno[0] <= 0:
                break
            if time.time() - t0 > TIEMPO_MAX_RELLENO or tiempo_usado() > TIEMPO_MAX_DESCARGAS:
                break
            d = hoy - timedelta(days=k)
            if d.isoformat() in hechos:
                continue
            if comp == "NBA":
                lista = espn_partidos_dia(d.strftime("%Y%m%d"))
            else:
                if DIAG_FUENTES["tsdb_cortado"]:
                    break
                lista = tsdb_partidos_dia(comp, d.isoformat(), ahora_utc)
                presupuesto_relleno[0] -= 1
            for m in lista:
                nuevos += registrar_resultado(resultados, m)
            hechos.add(d.isoformat())
        dias_hechos[comp] = sorted(hechos)[-60:]
        if len([x for x in hechos]) >= dias_max:
            resultados["relleno"][comp] = "completo"
        print(f"   +{nuevos} resultados ({LEAGUES_MAP[comp]['name']}) · días revisados {len(hechos)}/{dias_max}")


def cargar_datos_existentes():
    if os.path.exists("datos.json"):
        try:
            with open("datos.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Aviso leyendo datos existentes: {e}")
    return {}

def obtener_identificador_semana(fecha_madrid):
    año, semana, dia_semana = fecha_madrid.isocalendar()
    return f"{año}-W{semana:02d}"

def obtener_fechas_limite_semana(fecha_madrid):
    dia_semana_idx = fecha_madrid.weekday()
    lunes_dt = fecha_madrid - timedelta(days=dia_semana_idx)
    domingo_dt = lunes_dt + timedelta(days=6)
    return lunes_dt.strftime("%Y-%m-%d"), domingo_dt.strftime("%Y-%m-%d")

def archivar_historico_mensual(alertas_viejas, mes_id):
    archivo_hist = "historico_mensual.json"
    historico = {}
    if os.path.exists(archivo_hist):
        try:
            with open(archivo_hist, "r", encoding="utf-8") as f:
                historico = json.load(f)
        except Exception:
            historico = {}
    
    if mes_id not in historico:
        historico[mes_id] = []
    
    ids_existentes = {a.get("id_alerta") for a in historico[mes_id]}
    for a in alertas_viejas:
        if a.get("id_alerta") not in ids_existentes:
            historico[mes_id].append(a)

    try:
        with open(archivo_hist, "w", encoding="utf-8") as f:
            json.dump(historico, f, ensure_ascii=False, indent=2)
        print(f"📚 Archivo histórico mensual actualizado para {mes_id}")
    except Exception as e:
        print(f"Aviso guardando histórico mensual: {e}")

def calcular_resumen_financiero(alertas_lista):
    unidades_apostadas = 0.0
    beneficio_neto = 0.0
    acertadas = 0
    finalizadas = 0

    for a in alertas_lista:
        if a.get("finalizado") and not a.get("push"):
            finalizadas += 1
            stk_str = str(a.get("stake", "2")).split("/")[0].replace("U", "").strip()
            try:
                stk = float(stk_str)
            except Exception:
                stk = 2.0
            
            cuota_str = str(a.get("cuota", "@2.00")).replace("@", "").strip()
            try:
                cuota_val = float(cuota_str)
            except Exception:
                cuota_val = 2.00

            unidades_apostadas += stk
            if a.get("acertado") is True:
                acertadas += 1
                beneficio_neto += stk * (cuota_val - 1.0)
            else:
                beneficio_neto -= stk

    roi = round((beneficio_neto / unidades_apostadas) * 100, 1) if unidades_apostadas > 0 else 0.0
    return {
        "finalizadas": finalizadas,
        "acertadas": acertadas,
        "falladas": finalizadas - acertadas,
        "unidades_apostadas": round(unidades_apostadas, 1),
        "unidades_netas": round(beneficio_neto, 2),
        "roi_pct": roi
    }

def formatear_fecha_corta(fecha_str):
    if not fecha_str:
        return ""
    if "-" in fecha_str:
        partes = fecha_str.split("-")
        if len(partes) == 3:
            return f"{partes[2]}/{partes[1]}"
    return fecha_str
def buscar_mejor_combinada(partidos, alertas):
    """Combinada de 2-3 selecciones de partidos distintos, todas en la MISMA casa. Cada selección sale del análisis
    del partido (cualquier mercado con respaldo estadístico, probabilidad >= 50%, cuota 1.25-2.30 y ventaja >= +1%).
    La combinada debe superar la ventaja y la probabilidad conjuntas mínimas."""
    usados = set()
    for a in alertas:
        if a.get("es_combinada"):
            usados.update(str(l.get("id")) for l in a.get("legs", []))
    por_casa = {}
    for p in partidos:
        fav = p.get("fav")
        if not fav or p.get("estado") not in ("TIMED", "SCHEDULED") or p.get("minutos_restantes", 0) < 10:
            continue
        if str(p.get("id")) in usados or p.get("telegram_enviado"):
            continue   # si el partido ya tiene su alerta individual, no se repite la exposición en una combinada
        for casa, patas in fav.get("por_casa", {}).items():
            if isinstance(patas, dict):   # formato antiguo: solo victoria del favorito
                patas = [{"mercado": f"Victoria {patas['equipo']} (1X2)", "tipo": "1X2", "sel": None, "equipo": patas["equipo"],
                          "cuota": patas["cuota"], "prob": patas["prob"], "ev": patas["ev"], "confirmado": patas.get("confirmado")}]
            mejor_leg = None
            for d in patas:
                if not (COMBI_CUOTA_PATA[0] <= d["cuota"] <= COMBI_CUOTA_PATA[1] and d["prob"] >= COMBI_PROB_PATA_MIN
                        and d["ev"] >= COMBI_EV_PATA_MIN and d.get("confirmado")):
                    continue
                score = d["ev"] * math.sqrt(d["prob"])
                if mejor_leg is None or score > mejor_leg["score"]:
                    mejor_leg = {"id": p.get("id"), "partido": f"{p.get('local')} vs {p.get('visitante')}", "equipo": d.get("equipo"),
                                 "mercado": d["mercado"], "tipo": d.get("tipo", "1X2"), "sel": d.get("sel"),
                                 "cuota": d["cuota"], "prob": d["prob"], "ev": d["ev"], "score": score,
                                 "timestamp_ms": p.get("timestamp_ms", 0), "fecha": p.get("fecha"),
                                 "fecha_hora_display": p.get("fecha_hora_display"), "liga": p.get("liga")}
            if mejor_leg:
                por_casa.setdefault(casa, []).append(mejor_leg)
    mejor = None
    for casa, legs in por_casa.items():
        legs = sorted(legs, key=lambda l: l["ev"], reverse=True)[:8]
        for k in range(2, COMBI_MAX_PATAS + 1):
            for combo in itertools.combinations(legs, k):
                cuota = math.prod(l["cuota"] for l in combo)
                prob = math.prod(l["prob"] for l in combo)
                ev = (prob * cuota - 1.0) * 100
                if prob < COMBI_PROB_MIN or ev < COMBI_EV_MIN:
                    continue
                score = ev * math.sqrt(prob)
                if mejor is None or score > mejor["score"]:
                    mejor = {"casa": casa, "legs": list(combo), "cuota": cuota, "prob": prob, "ev": ev, "score": score}
    return mejor


def crear_alerta_combinada(c, semana_id):
    legs = sorted(c["legs"], key=lambda l: l["timestamp_ms"])
    primero = legs[0]
    ids = "-".join(str(l["id"]) for l in legs)
    return {
        "id_partido": "",
        "id_alerta": f"COMBI_{semana_id}_{ids}",
        "partido": f"Combinada de partidos ×{len(legs)}",
        "mercado": " + ".join(l["mercado"] for l in legs),
        "tipo_mercado": "COMBINADA",
        "categoria_visual": "combinada",
        "es_combinada": True,
        "legs": [{"id": l["id"], "partido": l["partido"], "equipo": l["equipo"], "mercado": l["mercado"], "tipo": l["tipo"],
                  "sel": l["sel"], "cuota": l["cuota"], "fecha_hora_display": l["fecha_hora_display"],
                  "res": None, "marcador": None} for l in legs],
        "casa": c["casa"],
        "cuota": f"@{round(c['cuota'], 2)}",
        "ev": f"+{round(c['ev'], 1)}%",
        "prob_combinada": round(c["prob"] * 100, 1),
        "stake": "1/10",
        "hora": first_hora(primero),
        "fecha": primero["fecha"],
        "fecha_hora_display": primero["fecha_hora_display"],
        "semana_id": semana_id,
        "es_ht": False,
        "es_semana_actual": True,
        "estado_partido": "TIMED",
        "marcador": None,
        "marcador_final": None,
        "finalizado": False,
        "acertado": None,
        "liquidada_telegram": False,
        "cuotas_reales": True,
    }


def first_hora(leg):
    txt = leg.get("fecha_hora_display") or ""
    return txt.split("•")[-1].strip() if "•" in txt else txt


def mensaje_combinada(a):
    lineas = []
    for i, l in enumerate(a["legs"], 1):
        lineas.append(f"{i}️⃣ <b>{l.get('mercado') or 'Victoria ' + str(l['equipo'])}</b> — {l['partido']} ({l['fecha_hora_display']}) @{l['cuota']}")
    return (
        f"🎯 <b>COMBINADA DE PARTIDOS (+EV) ×{len(a['legs'])}</b>\n\n"
        + "\n".join(lineas) + "\n\n"
        f"🏦 <b>Casa:</b> {a['casa']} (todas las selecciones en la misma casa)\n"
        f"💰 <b>Cuota total:</b> {a['cuota']}\n"
        f"📈 <b>Ventaja combinada (+EV):</b> {a['ev']}\n"
        f"🎲 <b>Probabilidad de acierto:</b> {a['prob_combinada']}% (deben acertar todas)\n"
        f"💡 <b>Stake Sugerido:</b> {a['stake']} unidades\n\n"
        f"⚡ <i>Apex Quant Engine • Combinada con respaldo estadístico</i>"
    )


def liquidar_combinadas(alertas, mapa_por_id):
    for a in alertas:
        if not a.get("es_combinada") or a.get("finalizado"):
            continue
        legs = a.get("legs", [])
        aciertos, pendientes, fallo, en_juego = 0, 0, False, False
        for leg in legs:
            info = mapa_por_id.get(str(leg.get("id")))
            if not info:
                pendientes += 1
                continue
            if info["estado"] == "FINISHED" and info["goles_local"] is not None and info["goles_visitante"] is not None:
                ok = evaluar_pick(leg.get("sel"), leg.get("mercado") or f"Victoria {leg.get('equipo')} (1X2)", leg.get("tipo", "1X2"),
                                  info["goles_local"], info["goles_visitante"], info.get("ht_local"), info.get("ht_visitante"),
                                  nombre_local=info["local"], nombre_visitante=info["visitante"],
                                  equipo_pronosticado=leg.get("equipo"))
                leg["marcador"] = info["marcador"]
                if ok is True:
                    leg["res"] = True
                    aciertos += 1
                elif ok is False:
                    leg["res"] = False
                    fallo = True
                else:
                    pendientes += 1   # sin datos suficientes todavía
                    continue
            else:
                pendientes += 1
                if info["estado"] in ("IN_PLAY", "HT", "PAUSED"):
                    en_juego = True
        if fallo or pendientes == 0:
            a["finalizado"] = True
            a["acertado"] = (not fallo) and pendientes == 0
            a["marcador_final"] = f"{aciertos}/{len(legs)} aciertos"
            a["estado_partido"] = "FINISHED"
            if not a.get("liquidada_telegram"):
                detalle = "\n".join(
                    f"{'✅' if l.get('res') else ('❌' if l.get('res') is False else '⏳')} {l.get('mercado') or l.get('equipo')} ({l['partido']}) {l.get('marcador') or ''}".rstrip()
                    for l in legs)
                titulo = "✅ <b>Resultado:</b> ACERTADA" if a["acertado"] else "❌ <b>Resultado:</b> PERDIDA"
                enviar_telegram(
                    f"🎯 <b>COMBINADA DE PARTIDOS ×{len(legs)}</b>\n{detalle}\n\n{titulo}\n"
                    f"💰 <b>Cuota:</b> {a['cuota']} • 💡 <b>Stake:</b> {a['stake']}\n\n⚡ <i>Apex Quant Engine</i>",
                    url_accion=APP_URL, texto_boton="📱 Ver en la Web App")
                a["liquidada_telegram"] = True
        elif en_juego or aciertos > 0:
            a["estado_partido"] = "IN_PLAY"


ARCHIVO_LEDGER = "historico_picks.json"


def _a_float(txt, defecto):
    try:
        return float(str(txt).split("/")[0].replace("@", "").replace("U", "").replace("+", "").replace("%", "").strip())
    except Exception:
        return defecto


def actualizar_ledger(alertas, partidos):
    """Libro mayor permanente (no se reinicia los lunes) con todas las picks liquidadas con cuotas reales:
    grupo TG = alertas enviadas a Telegram; grupo CONTROL = picks analizadas y descartadas."""
    ledger = {"version": 1, "picks": []}
    if os.path.exists(ARCHIVO_LEDGER):
        try:
            with open(ARCHIVO_LEDGER, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        except Exception:
            ledger = {"version": 1, "picks": []}
    picks = ledger.get("picks", [])
    ids = {p.get("id") for p in picks}
    nuevas = 0

    for a in alertas:
        if not (a.get("finalizado") and a.get("acertado") is not None and a.get("cuotas_reales") and not a.get("push")):
            continue
        es_combi = bool(a.get("es_combinada"))
        pid = str(a.get("id_alerta")) if es_combi else f"TG_{a.get('id_alerta')}"
        if pid in ids:
            continue
        picks.append({"id": pid, "version": VERSION, "grupo": "COMBI" if es_combi else "TG", "fecha": a.get("fecha"), "mes": (a.get("fecha") or "")[:7],
                      "semana_id": a.get("semana_id"), "partido": a.get("partido"), "mercado": a.get("mercado"),
                      "casa": a.get("casa"), "cuota": _a_float(a.get("cuota"), 2.0), "stake": _a_float(a.get("stake"), 1.0),
                      "ev": _a_float(a.get("ev"), 0.0), "confirmado_stats": a.get("confirmado_stats"),
                      "acertado": bool(a.get("acertado")), "marcador": a.get("marcador_final")})
        ids.add(pid)
        nuevas += 1

    for p in partidos:
        if not (p.get("estado") == "FINISHED" and p.get("acertado") is not None and p.get("tiene_pronostico")
                and p.get("analizado") and not p.get("telegram_enviado")):
            continue
        pid = f"CTRL_{p.get('id')}_{p.get('tipo_mercado')}"
        if pid in ids:
            continue
        picks.append({"id": pid, "version": VERSION, "grupo": "CONTROL", "fecha": p.get("fecha"), "mes": (p.get("fecha") or "")[:7],
                      "semana_id": p.get("semana_id"), "partido": f"{p.get('local')} vs {p.get('visitante')}",
                      "mercado": p.get("mercado"), "casa": p.get("casa"), "cuota": _a_float(p.get("cuota"), 2.0),
                      "stake": _a_float(p.get("stake"), 1.0), "ev": _a_float(p.get("ev_numero"), 0.0),
                      "confirmado_stats": p.get("confirmado_stats"), "acertado": bool(p.get("acertado")),
                      "marcador": p.get("marcador")})
        ids.add(pid)
        nuevas += 1

    if nuevas:
        ledger["picks"] = picks
        try:
            with open(ARCHIVO_LEDGER, "w", encoding="utf-8") as f:
                json.dump(ledger, f, ensure_ascii=False, separators=(",", ":"))
            print(f"📒 Libro mayor actualizado: +{nuevas} picks liquidadas (total {len(picks)})")
        except Exception as e:
            print(f"Aviso guardando libro mayor: {e}")



FORMAS_TTL_MIN = int(os.getenv("FORMAS_TTL_MIN", "60"))


def calcular_formas(resultados):
    """Últimos 5 resultados REALES de cada equipo (todas sus competiciones), del más antiguo al más reciente."""
    por_equipo = {}
    for comp, lst in resultados["ligas"].items():
        for r in lst:
            r_h = "W" if r["h"] > r["a"] else "L"
            r_a = "L" if r["h"] > r["a"] else "W"
            por_equipo.setdefault(r["hid"], []).append((r["u"], r_h))
            por_equipo.setdefault(r["aid"], []).append((r["u"], r_a))
    formas = {}
    for tid, lst in por_equipo.items():
        lst.sort(key=lambda x: x[0])
        formas[str(tid)] = "-".join(r for _, r in lst[-5:])
    return formas


def actualizar_formas(formas_prev, resultados, ahora_utc):
    nuevas = calcular_formas(resultados)
    if nuevas:
        return {"t": ahora_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "e": nuevas}
    return formas_prev or {"t": "", "e": {}}


def construir_form_info(formas, hid, aid):
    rl, rv = formas.get(str(hid)), formas.get(str(aid))
    if not rl or not rv or len(rl.split("-")) < 3 or len(rv.split("-")) < 3:
        return {"ajuste_prob": 0.0, "racha_local": rl or "", "racha_visitante": rv or "", "tendencia": "",
                "resumen": "Sin datos de forma suficientes", "diferencia_forma": 0,
                "es_club_top_local": False, "es_club_top_vis": False, "real": False}

    def vics(r):
        return sum(1 for c in r.split("-") if c == "W")

    vl, vv = vics(rl), vics(rv)
    dif = vl - vv
    if dif >= 3:
        tendencia = "🔥 Racha Favorable Local (+Dinámica)"
    elif dif <= -3:
        tendencia = "🔥 Racha Favorable Visitante (+Dinámica)"
    elif dif == -2:
        tendencia = "⚠️ Visitante en Alza (+Dinámica)"
    elif dif == 2:
        tendencia = "⚖️ Ligera Ventaja Local"
    elif dif == -1:
        tendencia = "⚖️ Ligera Ventaja Visitante"
    elif dif == 1:
        tendencia = "⚖️ Ligera Ventaja Local"
    else:
        tendencia = "⚖️ Tendencia Equilibrada"
    return {"ajuste_prob": 0.0, "racha_local": rl, "racha_visitante": rv, "tendencia": tendencia,
            "resumen": f"Local: {rl} ({vl}V) vs Vis: {rv} ({vv}V)", "diferencia_forma": dif,
            "es_club_top_local": False, "es_club_top_vis": False, "real": True}


def ejecutar_ciclo():
    ahora_utc = datetime.now(timezone.utc)
    ahora_madrid = ahora_utc.astimezone(TZ_MADRID)
    hora_madrid_str = ahora_madrid.strftime('%H:%M')
    hoy_madrid_str = ahora_madrid.strftime('%Y-%m-%d')
    semana_actual_id = obtener_identificador_semana(ahora_madrid)
    mes_actual_id = ahora_madrid.strftime('%Y-%m')
    dia_semana = ahora_madrid.weekday()
    hora_int = ahora_madrid.hour

    lunes_semana_str, domingo_semana_str = obtener_fechas_limite_semana(ahora_madrid)

    print(f"[{hora_madrid_str}] Iniciando ciclo Apex Quant Baloncesto v{VERSION} (Semana {semana_actual_id}: {lunes_semana_str} al {domingo_semana_str})...")

    # Comprobación de la clave de The Odds API (el endpoint /sports no consume créditos) y lectura de créditos restantes
    estado_api_cuotas = "sin_clave"
    if THE_ODDS_API_KEY:
        info_sports, st_sports = odds_get("/sports", {})
        if info_sports is not None:
            estado_api_cuotas = "ok"
            print(f"🔑 The Odds API OK | créditos restantes: {ODDS_ESTADO['restantes']}")
        else:
            estado_api_cuotas = f"error_{st_sports}"
            print(f"🔑 The Odds API ERROR (HTTP {st_sports}): revisa THE_ODDS_API_KEY")
    else:
        print("🔑 THE_ODDS_API_KEY no configurada: no habrá cuotas reales ni alertas")

    datos_previos = cargar_datos_existentes()
    preparar_presupuesto(datos_previos, ahora_madrid)
    alertas_acumuladas = datos_previos.get("alertas", [])
    partidos_previos = datos_previos.get("partidos", [])
    semana_guardada = datos_previos.get("semana_activa", semana_actual_id)
    resumen_semanal_enviado = datos_previos.get("resumen_semanal_enviado_para", "")
    resumen_mensual_enviado = datos_previos.get("resumen_mensual_enviado_para", "")

    # REINICIO AUTOMÁTICO CADA LUNES A LAS 00:00H
    es_reinicio_lunes = (semana_actual_id != semana_guardada)
    if es_reinicio_lunes:
        print(f"🔄 ¡LUNES A LAS 00:00 DETECTADO! Reiniciando ciclo semanal ({semana_guardada} -> {semana_actual_id})...")
        archivar_historico_mensual([a for a in alertas_acumuladas if not a.get("es_semana_actual", True)], mes_actual_id)
        
        for a in alertas_acumuladas:
            a["es_semana_actual"] = False
        
        partidos_previos = []
        semana_guardada = semana_actual_id

    # HISTÓRICO DE RESULTADOS (base del modelo estadístico y de las rachas)
    resultados = cargar_resultados()

    # OBTENCIÓN DE PARTIDOS (DESDE EL LUNES DE LA SEMANA HASTA DÍAS FUTUROS)
    partidos_raw = obtener_partidos_completos(lunes_semana_str, domingo_semana_str, partidos_previos)
    rellenar_historico(resultados, ahora_utc)   # después de lo urgente: lo que falte se completa en otros ciclos
    partidos_api_n = len(partidos_raw)
    diag_refresco = refrescar_partidos_atascados(partidos_raw, partidos_previos, ahora_utc, alertas_acumuladas)
    print(f"🩺 Diagnóstico partidos: API={partidos_api_n} | atascados={diag_refresco['atascados']} | "
          f"ausentes={diag_refresco['ausentes']} | alertas pendientes={diag_refresco['alertas']} | refrescados por ficha={diag_refresco['refrescados']}")

    nuevos_res = sum(registrar_resultado(resultados, m) for m in partidos_raw)
    guardar_resultados(resultados)
    print(f"📚 Histórico de resultados: " + ", ".join(f"{k} {len(v)}" for k, v in resultados["ligas"].items()) + f" (+{nuevos_res} nuevos)")

    partidos_para_web = []
    partidos_map_por_id = {}
    partidos_map_por_nombres = {}
    partidos_previos_map = {str(x.get('id')): x for x in partidos_previos}
    cache_eventos_odds = {}
    cache_stats_liga = {"_res": resultados}
    formas_equipos = actualizar_formas(datos_previos.get("formas_equipos"), resultados, ahora_utc)

    for p in partidos_raw:
        comp_code = p.get("competition", {}).get("code", "")
        if comp_code not in LEAGUES_MAP:
            continue

        comp_info = LEAGUES_MAP[comp_code]

        hora_utc_raw = p.get("utcDate", "")
        minutos_restantes = 999
        hora_madrid_partido = "TBD"
        fecha_madrid_partido = hoy_madrid_str
        timestamp_inicio_ms = 0

        if hora_utc_raw:
            try:
                utc_clean = hora_utc_raw.replace("Z", "+00:00")
                inicio_utc = datetime.fromisoformat(utc_clean)
                timestamp_inicio_ms = int(inicio_utc.timestamp() * 1000)
                inicio_madrid = inicio_utc.astimezone(TZ_MADRID)
                hora_madrid_partido = inicio_madrid.strftime('%H:%M')
                fecha_madrid_partido = inicio_madrid.strftime('%Y-%m-%d')
                minutos_restantes = round((inicio_utc - ahora_utc).total_seconds() / 60.0)
            except Exception as e:
                print(f"Aviso parseando fecha UTC ({hora_utc_raw}): {e}")
                if len(hora_utc_raw) >= 10:
                    fecha_madrid_partido = hora_utc_raw[:10]
                if len(hora_utc_raw) >= 16:
                    hora_madrid_partido = hora_utc_raw[11:16]

        fecha_corta_display = formatear_fecha_corta(fecha_madrid_partido)
        fecha_hora_display = f"{fecha_corta_display} • {hora_madrid_partido}"

        home_team_data = p.get("homeTeam", {})
        away_team_data = p.get("awayTeam", {})
        escudo_local = home_team_data.get("crest", "")
        escudo_visitante = away_team_data.get("crest", "")
        nombre_local = home_team_data.get("name", "Local")
        nombre_visitante = away_team_data.get("name", "Visitante")
        partido_nombre = f"{nombre_local} vs {nombre_visitante}"

        score_data = p.get("score", {})
        full_time = score_data.get("fullTime", {})
        half_time = {}   # en baloncesto no se usa el marcador al descanso

        goles_local = full_time.get("home")        # puntos del local (se mantiene el nombre de campo de la app)
        goles_visitante = full_time.get("away")    # puntos del visitante

        estado_api = p.get("status", "TIMED")
        minutos_desde_inicio = -minutos_restantes if minutos_restantes < 0 else 0
        es_finalizado = estado_api == "FINISHED"
        duracion_liga = comp_info.get("duracion_min", 150)

        es_en_juego = False
        if not es_finalizado:
            if estado_api in ["IN_PLAY", "HT"]:
                es_en_juego = True
            elif minutos_restantes <= 0 and minutos_desde_inicio <= duracion_liga and estado_api not in ["CANCELLED", "POSTPONED", "TIMED"]:
                es_en_juego = True

        if es_en_juego and (goles_local is None or goles_visitante is None):
            goles_local = 0
            goles_visitante = 0

        marcador_str = None
        if goles_local is not None and goles_visitante is not None:
            marcador_str = f"{goles_local} - {goles_visitante}"

        estado_normalizado = estado_api
        minuto_en_juego_str = None
        detalle_api = (p.get("detalle") or "").strip()

        if es_finalizado:
            estado_normalizado = "FINISHED"
        elif estado_api == "HT":
            estado_normalizado = "HT"
            minuto_en_juego_str = "Descanso"
        elif es_en_juego:
            estado_normalizado = "IN_PLAY"
            minuto_en_juego_str = detalle_api or "En juego"
        elif estado_api == "TIMED" and minutos_desde_inicio > duracion_liga:
            # La fuente sigue diciendo "programado" aunque el partido ya debería haber terminado:
            # no se da por finalizado (no hay marcador) hasta que se publique el resultado.
            estado_normalizado = "PENDING_RESULT"
            minuto_en_juego_str = "Esperando resultado"

        info_partido_actual = {
            "id": p.get("id"),
            "partido": partido_nombre,
            "local": nombre_local,
            "visitante": nombre_visitante,
            "estado": estado_normalizado,
            "estado_original": estado_api,
            "minuto_juego": minuto_en_juego_str,
            "goles_local": goles_local,
            "goles_visitante": goles_visitante,
            "marcador": marcador_str,
            "ht_local": half_time.get("home"),
            "ht_visitante": half_time.get("away"),
            "fecha": fecha_madrid_partido,
            "hora": hora_madrid_partido,
            "fecha_hora_display": fecha_hora_display,
            "escudo_local": escudo_local,
            "escudo_visitante": escudo_visitante
        }

        partidos_map_por_id[str(p.get("id"))] = info_partido_actual
        partidos_map_por_nombres[partido_nombre] = info_partido_actual
        key_norm = f"{normalizar_texto(nombre_local)} vs {normalizar_texto(nombre_visitante)}"
        partidos_map_por_nombres[key_norm] = info_partido_actual
        if fecha_madrid_partido < lunes_semana_str:
            continue   # partido de la semana anterior: no se muestra, pero sí liquida alertas pendientes

        form_info = construir_form_info(formas_equipos.get("e", {}), home_team_data.get("id"), away_team_data.get("id"))
        prev = partidos_previos_map.get(str(p.get("id")))
        analizado = bool(prev and prev.get("analizado"))
        nota_cuotas = prev.get("nota_cuotas") if prev else None
        analisis = None
        analisis_nuevo = False
        fav_info = prev.get("fav") if (prev and prev.get("analizado")) else None

        prevision = prev.get("prevision") if prev else None
        if not es_finalizado and not es_en_juego:
            try:
                mod_prev = calcular_modelo_estadistico(comp_code, home_team_data.get("id"), away_team_data.get("id"), cache_stats_liga)
                if mod_prev:
                    p1_prev, _ = prob_modelo_sel(mod_prev, {"t": "ML", "s": "1"})
                    prevision = {"pts_local": mod_prev["xg_local"], "pts_visitante": mod_prev["xg_visitante"],
                                 "margen": mod_prev["margen"], "total": mod_prev["total"],
                                 "prob_local": round(p1_prev * 100, 1), "prob_visitante": round((1 - p1_prev) * 100, 1)}
            except Exception as e:
                print(f"   Aviso previsión {partido_nombre}: {e}")

        if analizado:
            analisis = reconstruir_analisis(prev, form_info)
        elif (not es_finalizado) and (not es_en_juego) and VENTANA_MIN <= minutos_restantes <= VENTANA_MAX:
            inicio_dt = datetime.fromtimestamp(timestamp_inicio_ms / 1000.0, timezone.utc) if timestamp_inicio_ms else None
            analisis, definitivo, nota, fav_nuevo = analizar_partido_con_cuotas(comp_code, nombre_local, nombre_visitante, inicio_dt, form_info, cache_eventos_odds, home_team_data.get("id"), away_team_data.get("id"), cache_stats_liga)
            nota_cuotas = nota
            if definitivo:
                analizado = True
                analisis_nuevo = analisis is not None
                fav_info = fav_nuevo
            else:
                analisis = None

        acertado_partido = None
        push_partido = False
        id_alerta_unica = None
        ya_registrada = False
        if analisis:
            if es_finalizado and goles_local is not None and goles_visitante is not None:
                res_pick = evaluar_pick(
                    analisis.get("sel"), analisis["mercado"], analisis["tipo_mercado"],
                    goles_local, goles_visitante, half_time.get("home"), half_time.get("away"),
                    nombre_local=nombre_local, nombre_visitante=nombre_visitante,
                    equipo_pronosticado=analisis.get("equipo_pronosticado")
                )
                if res_pick == "PUSH":
                    push_partido = True
                elif res_pick is True or res_pick is False:
                    acertado_partido = res_pick
            id_alerta_unica = f"{p.get('id')}_{analisis['tipo_mercado']}"
            ya_registrada = any(a.get("id_alerta") == id_alerta_unica or (a.get("partido") == partido_nombre and a.get("tipo_mercado") == analisis["tipo_mercado"]) for a in alertas_acumuladas)

        fue_enviado_telegram = False

        # ALERTA OFICIAL: mejor cuota real entre las casas objetivo con +EV >= 3% frente al consenso (T-90m a T-15m)
        if analisis_nuevo and analisis["ev"] >= EV_MINIMO_PCT and (analisis.get("confirmado_stats") or analisis.get("sin_modelo_ok") or not STATS_OBLIGATORIAS) and not ya_registrada:
            fue_enviado_telegram = True
            if analisis.get("solo_modelo"):
                linea_cuota = (f"💰 <b>Cuota mínima para tener valor:</b> @{analisis['cuota']}. "
                               f"Juégala solo si tu casa paga esa cuota o más.\n")
            elif analisis.get("cuota_minima"):
                linea_cuota = (f"💰 <b>Cuota mínima para tener valor:</b> @{analisis['cuota']} "
                               f"(referencia ≈ @{analisis.get('cuota_ref')}). Comprueba el precio real en «Crear apuesta»; si es menor, no la juegues.\n")
            else:
                linea_cuota = f"💰 <b>Cuota {analisis.get('casa', '')}:</b> @{analisis['cuota']}\n"
            if analisis.get("solo_modelo"):
                linea_ev = f"📈 <b>Margen exigido sobre la cuota justa:</b> +{analisis['ev']}%\n"
                linea_prob = f"🧮 <b>Probabilidad según el modelo:</b> {analisis['prob_modelo']}\n"
                linea_resp = (f"📐 <b>Puntos esperados:</b> {analisis.get('xg_local')} - {analisis.get('xg_visitante')} "
                              f"(sin cuotas de mercado en esta competición)\n")
                pie = "⚡ <i>Apex Quant Engine • Pronóstico del modelo estadístico</i>"
            else:
                linea_ev = f"📈 <b>Ventaja (+EV vs consenso):</b> +{analisis['ev']}%\n"
                linea_prob = (f"🧮 <b>Prob. combinada:</b> {analisis['prob_modelo']} (mercado {analisis.get('prob_mercado') if analisis.get('prob_mercado') is not None else '—'}% · "
                              f"modelo {analisis.get('prob_estadistica') if analisis.get('prob_estadistica') is not None else '—'}%)\n")
                linea_resp = (f"📐 <b>Respaldo estadístico:</b> {'✅ sí' if analisis.get('confirmado_stats') else '—'} • "
                              f"Puntos esperados {analisis.get('xg_local')} - {analisis.get('xg_visitante')} • consenso de {analisis.get('n_casas', '?')} casas\n")
                pie = "⚡ <i>Apex Quant Engine • Cuotas reales de mercado</i>"
            alerta_msg = (
                f"🎯 <b>ALERTA CUANTITATIVA (+EV) — [PRE-PARTIDO]</b>\n\n"
                f"🏆 <b>Competición:</b> {comp_info['flag']} {comp_info['name']}\n"
                f"🏀 <b>Partido:</b> {partido_nombre}\n"
                f"⏰ <b>Inicio:</b> {fecha_hora_display} (en {minutos_restantes} min)\n"
                f"{analisis.get('icono_tg', '🛡️')} <b>Mercado:</b> {analisis['mercado']}\n"
                f"{linea_cuota}"
                f"{linea_ev}"
                f"💡 <b>Stake Sugerido:</b> {analisis['stake']}/10 unidades\n"
                f"{linea_prob}"
                f"{linea_resp}"
                f"📊 <b>Filtro de Racha:</b> {analisis['form_info']['resumen']}\n"
                f"📈 <b>Dinámica:</b> {analisis['form_info']['tendencia']}\n\n"
                f"{pie}"
            )
            enviar_telegram(alerta_msg, url_accion=APP_URL, texto_boton="📱 Abrir en Web App")
            alertas_acumuladas.append({
                "id_partido": str(p.get("id")),
                "id_alerta": id_alerta_unica,
                "partido": partido_nombre,
                "local": nombre_local,
                "visitante": nombre_visitante,
                "escudo_local": escudo_local,
                "escudo_visitante": escudo_visitante,
                "mercado": analisis["mercado"],
                "tipo_mercado": analisis["tipo_mercado"],
                "categoria_visual": analisis.get("categoria_visual", "doble_oportunidad"),
                "equipo_pronosticado": analisis.get("equipo_pronosticado"),
                "casa": analisis.get("casa"),
                "sel": analisis.get("sel"),
                "cuota_ref": analisis.get("cuota_ref"),
                "cuota_minima": analisis.get("cuota_minima"),
                "cuotas_reales": not analisis.get("solo_modelo"),
                "solo_modelo": bool(analisis.get("solo_modelo")),
                "liga": comp_info["name"],
                "liga_code": comp_code,
                "confirmado_stats": analisis.get("confirmado_stats"),
                "cuota": f"@{analisis['cuota']}",
                "ev": f"+{analisis['ev']}%",
                "stake": f"{analisis['stake']}/10",
                "hora": hora_madrid_partido,
                "fecha": fecha_madrid_partido,
                "fecha_hora_display": fecha_hora_display,
                "semana_id": semana_actual_id,
                "es_ht": False,
                "es_semana_actual": True,
                "estado_partido": estado_normalizado,
                "marcador": marcador_str,
                "marcador_final": None,
                "finalizado": False,
                "acertado": None,
                "liquidada_telegram": False
            })

        tiene_pron = analisis is not None
        item = {
            "id": p.get("id"),
            "local": nombre_local,
            "visitante": nombre_visitante,
            "escudo_local": escudo_local,
            "escudo_visitante": escudo_visitante,
            "liga": comp_info["name"],
            "liga_key": comp_info["key"],
            "liga_code": comp_code,
            "fecha": fecha_madrid_partido,
            "hora": hora_madrid_partido,
            "fecha_hora_display": fecha_hora_display,
            "hora_utc": hora_utc_raw,
            "utcDate": hora_utc_raw,
            "timestamp_ms": timestamp_inicio_ms,
            "minutos_restantes": minutos_restantes,
            "estado": estado_normalizado,
            "minuto_juego": minuto_en_juego_str,
            "marcador": marcador_str,
            "goles_local": goles_local,
            "goles_visitante": goles_visitante,
            "mercado": analisis["mercado"] if tiene_pron else None,
            "tipo_mercado": analisis["tipo_mercado"] if tiene_pron else None,
            "categoria_visual": analisis.get("categoria_visual") if tiene_pron else None,
            "equipo_pronosticado": analisis.get("equipo_pronosticado") if tiene_pron else None,
            "cuota": analisis["cuota"] if tiene_pron else None,
            "prob_modelo": analisis["prob_modelo"] if tiene_pron else None,
            "ev": f"+{analisis['ev']}%" if tiene_pron else None,
            "ev_numero": analisis["ev"] if tiene_pron else None,
            "stake": analisis["stake"] if tiene_pron else None,
            "n_casas": analisis.get("n_casas") if tiene_pron else None,
            "casa": analisis.get("casa") if tiene_pron else None,
            "sel": analisis.get("sel") if tiene_pron else None,
            "cuota_ref": analisis.get("cuota_ref") if tiene_pron else None,
            "cuota_minima": analisis.get("cuota_minima") if tiene_pron else None,
            "sin_modelo_ok": analisis.get("sin_modelo_ok") if tiene_pron else None,
            "solo_modelo": analisis.get("solo_modelo") if tiene_pron else None,
            "ht_local": half_time.get("home"),
            "ht_visitante": half_time.get("away"),
            "push": push_partido,
            "prob_mercado": analisis.get("prob_mercado") if tiene_pron else None,
            "prob_estadistica": analisis.get("prob_estadistica") if tiene_pron else None,
            "confirmado_stats": analisis.get("confirmado_stats") if tiene_pron else None,
            "xg_local": analisis.get("xg_local") if tiene_pron else None,
            "xg_visitante": analisis.get("xg_visitante") if tiene_pron else None,
            "tiene_pronostico": tiene_pron,
            "analizado": analizado,
            "nota_cuotas": nota_cuotas,
            "fav": fav_info,
            "semana_id": semana_actual_id,
            "telegram_enviado": bool(fue_enviado_telegram or ya_registrada or (prev and prev.get("telegram_enviado"))),
            "es_empate_ht": False,
            "acertado": acertado_partido,
            "form_info": form_info,
            "prevision": prevision
        }
        partidos_para_web.append(item)

    # Un partido ya finalizado no puede "retroceder" a programado/en juego por un dato atrasado de la API
    for i, it in enumerate(partidos_para_web):
        pv = partidos_previos_map.get(str(it.get("id")))
        if pv and pv.get("estado") == "FINISHED" and it.get("estado") != "FINISHED" and pv.get("marcador"):
            restaurado = dict(pv)
            restaurado["minutos_restantes"] = it.get("minutos_restantes")
            restaurado["telegram_enviado"] = bool(pv.get("telegram_enviado") or it.get("telegram_enviado"))
            partidos_para_web[i] = restaurado

    # COMBINADAS DE FAVORITOS (máx. 3 partidos, misma casa, +EV conjunto y respaldo estadístico)
    if COMBI_ACTIVA:
        mejor_combi = buscar_mejor_combinada(partidos_para_web, alertas_acumuladas)
        if mejor_combi:
            alerta_combi = crear_alerta_combinada(mejor_combi, semana_actual_id)
            if not any(a.get("id_alerta") == alerta_combi["id_alerta"] for a in alertas_acumuladas):
                enviar_telegram(mensaje_combinada(alerta_combi), url_accion=APP_URL, texto_boton="📱 Abrir en Web App")
                alertas_acumuladas.append(alerta_combi)
                print(f"🎯 Combinada creada: {alerta_combi['mercado']} {alerta_combi['cuota']} (EV {alerta_combi['ev']})")

    # RECONCILIACIÓN Y LIQUIDACIÓN
    liquidar_combinadas(alertas_acumuladas, partidos_map_por_id)
    for alerta in alertas_acumuladas:
        if alerta.get("es_combinada"):
            continue
        p_id = str(alerta.get("id_partido", ""))
        p_nombre = alerta.get("partido", "")
        p_norm = normalizar_texto(p_nombre)

        partido_encontrado = None
        if p_id and p_id in partidos_map_por_id:
            partido_encontrado = partidos_map_por_id[p_id]
        elif p_nombre in partidos_map_por_nombres:
            partido_encontrado = partidos_map_por_nombres[p_nombre]
        else:
            for k, info in partidos_map_por_nombres.items():
                if normalizar_texto(k) == p_norm or (info["local"] in p_nombre and info["visitante"] in p_nombre):
                    partido_encontrado = info
                    break

        if partido_encontrado:
            alerta["estado_partido"] = partido_encontrado["estado"]
            if partido_encontrado["marcador"]:
                alerta["marcador"] = partido_encontrado["marcador"]

            if partido_encontrado["estado"] == "FINISHED":
                g_loc = partido_encontrado["goles_local"]
                g_vis = partido_encontrado["goles_visitante"]
                marcador_fin = partido_encontrado["marcador"] or (f"{g_loc} - {g_vis}" if g_loc is not None else None)
                
                res_pick = evaluar_pick(
                    alerta.get("sel"), alerta["mercado"], alerta.get("tipo_mercado", ""), g_loc, g_vis,
                    partido_encontrado.get("ht_local"), partido_encontrado.get("ht_visitante"),
                    nombre_local=partido_encontrado["local"], nombre_visitante=partido_encontrado["visitante"],
                    equipo_pronosticado=alerta.get("equipo_pronosticado")
                )
                if res_pick is None:
                    continue   # falta algún dato (p. ej. el marcador del descanso): se reintenta en el siguiente ciclo
                alerta["finalizado"] = True
                alerta["marcador_final"] = marcador_fin
                alerta["goles_local"] = g_loc
                alerta["goles_visitante"] = g_vis
                es_push = (res_pick == "PUSH")
                acertado = None if es_push else bool(res_pick)
                alerta["acertado"] = acertado
                alerta["push"] = es_push

                if not alerta.get("liquidada_telegram"):
                    nombre_partido_display = p_nombre or f"{partido_encontrado['local']} vs {partido_encontrado['visitante']}"
                    if es_push:
                        msg_settle = (
                            f"🏀 <b>{nombre_partido_display}</b>\n"
                            f"📌 <b>Pronóstico:</b> {alerta['mercado']}\n"
                            f"↩️ <b>Resultado:</b> DEVUELTA (push)\n"
                            f"🏁 <b>Marcador Final:</b> {marcador_fin or 'Final'}\n\n"
                            f"⚡ <i>Apex Quant Engine</i>"
                        )
                    elif acertado:
                        msg_settle = (
                            f"🏀 <b>{nombre_partido_display}</b>\n"
                            f"📌 <b>Pronóstico:</b> {alerta['mercado']}\n"
                            f"✅ <b>Resultado:</b> ACERTADO\n"
                            f"🏁 <b>Marcador Final:</b> {marcador_fin or 'Final'}\n"
                            f"💰 <b>{'Cuota mínima' if alerta.get('cuota_minima') else 'Cuota Cobrada'}:</b> {alerta['cuota']}\n"
                            f"💡 <b>Stake:</b> {alerta['stake']}\n\n"
                            f"🎉 <i>¡Verificado y sumado a la Efectividad Oficial en la App!</i>\n"
                            f"⚡ <i>Apex Quant Engine</i>"
                        )
                    else:
                        msg_settle = (
                            f"🏀 <b>{nombre_partido_display}</b>\n"
                            f"📌 <b>Pronóstico:</b> {alerta['mercado']}\n"
                            f"❌ <b>Resultado:</b> PERDIDO\n"
                            f"🏁 <b>Resultado Final:</b> {marcador_fin or 'Final'}\n"
                            f"💡 <b>Stake:</b> {alerta['stake']} (Control estricto de banca)\n\n"
                            f"⚡ <i>Apex Quant Engine • Disciplina a Largo Plazo</i>"
                        )
                    enviar_telegram(msg_settle, url_accion=APP_URL, texto_boton="📱 Ver en la Web App")
                    alerta["liquidada_telegram"] = True

    # RESUMEN DOMINICAL
    if dia_semana == 6 and hora_int >= 22 and resumen_semanal_enviado != semana_actual_id:
        partidos_domingo = [p for p in partidos_para_web if p["fecha"] == hoy_madrid_str]
        partidos_pendientes_domingo = [p for p in partidos_domingo if p["estado"] != "FINISHED"]
        
        if len(partidos_pendientes_domingo) == 0 or (hora_int == 23 and ahora_madrid.minute >= 45):
            alertas_semana_actual = [a for a in alertas_acumuladas if a.get("semana_id") == semana_actual_id and a.get("finalizado")]
            resumen_fin = calcular_resumen_financiero(alertas_semana_actual)
            total_semana = resumen_fin["finalizadas"]
            
            if total_semana > 0:
                aciertos_semana = resumen_fin["acertadas"]
                fallos_semana = resumen_fin["falladas"]
                pct_acierto = round((aciertos_semana / total_semana) * 100, 1)
                signo_u = "+" if resumen_fin["unidades_netas"] >= 0 else ""

                msg_resumen_domingo = (
                    f"📊 <b>CIERRE SEMANAL OFICIAL — APEX QUANT BALONCESTO</b> 🏆\n\n"
                    f"📅 <b>Semana:</b> {semana_actual_id} (Finalizada)\n"
                    f"📈 <b>Total Pronósticos Oficiales:</b> <b>{total_semana}</b>\n\n"
                    f"✅ <b>Alertas Acertadas:</b> <b>{aciertos_semana}</b> ({pct_acierto}%)\n"
                    f"❌ <b>Alertas Falladas:</b> <b>{fallos_semana}</b>\n"
                    f"🎯 <b>Efectividad Oficial:</b> <b>{pct_acierto}%</b>\n\n"
                    f"💰 <b>Rendimiento Neto:</b> <b>{signo_u}{resumen_fin['unidades_netas']} Unidades</b>\n"
                    f"📈 <b>Yield / ROI Semanal:</b> <b>{signo_u}{resumen_fin['roi_pct']}%</b>\n\n"
                    f"🔄 <i>Las estadísticas y partidos se reiniciarán automáticamente a las 00:00 del lunes para la nueva semana.</i>\n"
                    f"⚡ <i>Apex Quant Engine • Máxima Transparencia y Disciplina</i>"
                )
                enviar_telegram(msg_resumen_domingo, url_accion=APP_URL, texto_boton="📱 Abrir Balance Completo en la App")
                resumen_semanal_enviado = semana_actual_id

    # RESUMEN MENSUAL
    dia_manana_madrid = ahora_madrid + timedelta(days=1)
    es_ultimo_dia_mes = (dia_manana_madrid.month != ahora_madrid.month)

    if es_ultimo_dia_mes and hora_int >= 22 and resumen_mensual_enviado != mes_actual_id:
        partidos_hoy_mes = [p for p in partidos_para_web if p["fecha"] == hoy_madrid_str]
        partidos_pendientes_mes = [p for p in partidos_hoy_mes if p["estado"] != "FINISHED"]

        if len(partidos_pendientes_mes) == 0 or (hora_int == 23 and ahora_madrid.minute >= 45):
            alertas_mes_totales = []
            archivo_hist = "historico_mensual.json"
            if os.path.exists(archivo_hist):
                try:
                    with open(archivo_hist, "r", encoding="utf-8") as f:
                        hist_data = json.load(f)
                        alertas_mes_totales.extend(hist_data.get(mes_actual_id, []))
                except Exception as e:
                    pass

            ids_ya_contadas = {a.get("id_alerta") for a in alertas_mes_totales}
            for a in alertas_acumuladas:
                f_a = a.get("fecha", "")
                if f_a.startswith(mes_actual_id) and a.get("finalizado") and a.get("id_alerta") not in ids_ya_contadas:
                    alertas_mes_totales.append(a)
                    ids_ya_contadas.add(a.get("id_alerta"))

            resumen_mes_fin = calcular_resumen_financiero(alertas_mes_totales)
            total_mes = resumen_mes_fin["finalizadas"]

            if total_mes > 0:
                aciertos_mes = resumen_mes_fin["acertadas"]
                fallos_mes = resumen_mes_fin["falladas"]
                pct_acierto_mes = round((aciertos_mes / total_mes) * 100, 1)
                signo_u_mes = "+" if resumen_mes_fin["unidades_netas"] >= 0 else ""
                signo_roi_mes = "+" if resumen_mes_fin["roi_pct"] >= 0 else ""

                semanas_map = {}
                for a in alertas_mes_totales:
                    sem_key = a.get("semana_id") or "S/D"
                    if sem_key not in semanas_map:
                        semanas_map[sem_key] = []
                    semanas_map[sem_key].append(a)

                lineas_semanas = []
                for idx_sem, (sem_k, sem_alertas) in enumerate(sorted(semanas_map.items()), 1):
                    res_sem = calcular_resumen_financiero(sem_alertas)
                    signo_sem_u = "+" if res_sem["unidades_netas"] >= 0 else ""
                    signo_sem_roi = "+" if res_sem["roi_pct"] >= 0 else ""
                    lineas_semanas.append(
                        f"▫️ <b>Semana {idx_sem} ({sem_k}):</b> {signo_sem_u}{res_sem['unidades_netas']} U. (ROI {signo_sem_roi}{res_sem['roi_pct']}%)"
                    )

                bloque_semanas_str = "\n".join(lineas_semanas) if lineas_semanas else "▫️ Registro semanal consolidado"
                nombres_meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
                nombre_mes_str = nombres_meses[ahora_madrid.month - 1]
                año_str = ahora_madrid.strftime('%Y')

                msg_resumen_mensual = (
                    f"🏆 <b>CIERRE MENSUAL OFICIAL — APEX QUANT BALONCESTO</b> 📊\n\n"
                    f"📅 <b>Mes:</b> <b>{nombre_mes_str} {año_str}</b> (Finalizado)\n"
                    f"📈 <b>Total Pronósticos Oficiales:</b> <b>{total_mes}</b>\n\n"
                    f"✅ <b>Alertas Acertadas:</b> <b>{aciertos_mes}</b> ({pct_acierto_mes}%)\n"
                    f"❌ <b>Alertas Falladas:</b> <b>{fallos_mes}</b>\n"
                    f"🎯 <b>Efectividad Mensual:</b> <b>{pct_acierto_mes}%</b>\n\n"
                    f"💰 <b>Rendimiento Neto:</b> <b>{signo_u_mes}{resumen_mes_fin['unidades_netas']} Unidades</b>\n"
                    f"📈 <b>Yield / ROI Mensual:</b> <b>{signo_roi_mes}{resumen_mes_fin['roi_pct']}%</b>\n\n"
                    f"📊 <b>EVOLUCIÓN SEMANA A SEMANA:</b>\n"
                    f"{bloque_semanas_str}\n\n"
                    f"⚡ <i>Apex Quant Engine • Transparencia, Disciplina y Consistencia</i>"
                )
                enviar_telegram(msg_resumen_mensual, url_accion=APP_URL, texto_boton="📱 Abrir Balance Completo en la App")
                resumen_mensual_enviado = mes_actual_id

    alertas_semana_act = [a for a in alertas_acumuladas if a.get("semana_id") == semana_actual_id]
    metricas_semana = calcular_resumen_financiero(alertas_semana_act)

    # FUSIÓN DE PARTIDOS
    partidos_guardar = list(partidos_para_web)
    ids_guardados = {p["id"] for p in partidos_guardar}

    for prev_p in partidos_previos:
        p_id = prev_p.get("id")
        p_fecha = prev_p.get("fecha", "")
        if p_id not in ids_guardados:
            if p_fecha >= lunes_semana_str:
                partidos_guardar.append(prev_p)
                ids_guardados.add(p_id)

    datos_completos = {
        "version_bot": VERSION,
        "ultima_actualizacion": hora_madrid_str,
        "ultima_actualizacion_utc": ahora_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "huso_horario": "Europe/Madrid",
        "semana_activa": semana_guardada,
        "lunes_semana": lunes_semana_str,
        "domingo_semana": domingo_semana_str,
        "resumen_semanal_enviado_para": resumen_semanal_enviado,
        "resumen_mensual_enviado_para": resumen_mensual_enviado,
        "metricas_semana": metricas_semana,
        "total_partidos_hoy": len(partidos_guardar),
        "partidos": partidos_guardar,
        "creditos_odds_restantes": ODDS_ESTADO["restantes"],
        "api_cuotas_estado": estado_api_cuotas,
        "creditos_dia": ODDS_ESTADO.get("dia"),
        "formas_equipos": formas_equipos,
        "diagnostico": {"perfil_mercados": ODDS_ESTADO.get("perfil"), "presupuesto_dia": (round(ODDS_ESTADO["presupuesto_dia"], 1) if ODDS_ESTADO.get("presupuesto_dia") else None), "partidos_api": partidos_api_n, "atascados": diag_refresco["atascados"],
                        "ausentes": diag_refresco["ausentes"], "alertas_pendientes": diag_refresco["alertas"],
                        "refrescados": diag_refresco["refrescados"],
                        "fuentes": DIAG_FUENTES, "resultados_por_liga": {k: len(v) for k, v in resultados["ligas"].items()}},
        "alertas": alertas_acumuladas[-70:]
    }

    actualizar_ledger(alertas_acumuladas, partidos_guardar)

    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(datos_completos, f, ensure_ascii=False, indent=2)

    total_fin = sum(1 for p in partidos_guardar if p.get("estado") == "FINISHED")
    print(f"💳 The Odds API: {ODDS_ESTADO['consultas']} consultas de cuotas en este ciclo | créditos restantes: {ODDS_ESTADO['restantes']}")
    print(f"📁 datos.json guardado. Partidos semana: {len(partidos_guardar)} (Finalizados: {total_fin}) | Alertas: {len(alertas_acumuladas)} | Semana: {semana_guardada}")

if __name__ == "__main__":
    ejecutar_ciclo()


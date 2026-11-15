"""
APEX QUANT BALONCESTO — MERCADOS DE JUGADORES Y MYMATCH (NBA y Euroliga)

1) Descarga las estadísticas de cada partido terminado (minutos, puntos, rebotes, asistencias y triples de cada
   jugador): NBA desde ESPN y Euroliga desde la web oficial de la Euroliga. Se guardan en jugadores.json.
2) Para cada partido que va a empezar calcula lo que se espera de cada jugador (minutos, ritmo por minuto de sus
   últimos partidos y defensa del rival) y la probabilidad de superar cada línea de «logros» (Más de 14,5 puntos,
   Más de 2,5 triples...). En la NBA quita a los lesionados y dudosos del parte de ESPN.
3) Monta el mejor MyMatch del partido (2 o 3 selecciones del mismo partido, teniendo en cuenta que van
   relacionadas) y la mejor selección individual de jugador. Como ninguna API da el precio de MyMatch de Winamax,
   se da la CUOTA MÍNIMA: solo tiene valor si la casa paga esa cuota o más.
4) Liquida cada selección con el box score real. Si un jugador no juega, su selección se anula (regla habitual de
   las casas) y la apuesta sigue con el resto.
"""
import os
import json
import math
import time
import random
import itertools
import requests
from datetime import datetime, timezone

ARCHIVO_JUGADORES = "jugadores.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (ApexQuantBaloncesto)", "Accept": "application/json"}

PROPS_ACTIVO = os.getenv("PROPS_ACTIVO", "1") == "1"
LIGAS_PROPS = ("NBA", "EL")
MAX_BOX_CICLO = {"NBA": int(os.getenv("PROPS_MAX_BOX_NBA", "60")), "EL": int(os.getenv("PROPS_MAX_BOX_EL", "60"))}
MIN_MINUTOS = {"NBA": 22.0, "EL": 18.0}         # solo jugadores con minutos importantes
MIN_PARTIDOS_JUG = 4
MARGEN_PROPS = 1.08          # cuota mínima = cuota justa del modelo + 8% (más margen: el modelo de jugadores tiene más incertidumbre)
PROB_TOPE = 0.93             # nunca se considera una selección más segura que esto
PATA_PROB_MIN = 0.78         # selecciones "seguras" para el MyMatch
MYMATCH_MAX_PATAS = int(os.getenv("MYMATCH_MAX_PATAS", "3"))
MYMATCH_CUOTA = (1.45, 2.30)  # rango de cuota mínima del MyMatch completo
SIMPLE_PROB = (0.62, 0.74)   # selección individual: probabilidad buscada (cuota mínima ~1,45-1,75)

LINEAS = {
    "pts": [4.5, 9.5, 14.5, 19.5, 24.5, 29.5, 34.5, 39.5],
    "reb": [2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5],
    "ast": [2.5, 4.5, 6.5, 8.5, 10.5, 12.5],
    "tri": [0.5, 1.5, 2.5, 3.5, 4.5, 5.5],
    "pra": [9.5, 14.5, 19.5, 24.5, 29.5, 34.5, 39.5, 44.5, 49.5],
}
NOMBRE_STAT = {"pts": "puntos", "reb": "rebotes", "ast": "asistencias", "tri": "triples", "pra": "puntos + rebotes + asistencias"}
IDX = {"min": 2, "pts": 3, "reb": 4, "ast": 5, "tri": 6}   # columnas de cada partido guardado: [u, rival, min, pts, reb, ast, tri, gid]

# Correlación entre selecciones del mismo jugador (cópula gaussiana)
CORR_MISMO = {frozenset(("pts", "tri")): 0.55, frozenset(("pts", "ast")): 0.30, frozenset(("pts", "reb")): 0.25,
              frozenset(("reb", "ast")): 0.15, frozenset(("tri", "ast")): 0.15, frozenset(("tri", "reb")): 0.05}

DIAG = {"box_nba": 0, "box_el": 0, "errores": 0, "lesiones": 0}


# ------------------------------------------------------------------------------------------------
# Almacén
# ------------------------------------------------------------------------------------------------
def cargar_store():
    if os.path.exists(ARCHIVO_JUGADORES):
        try:
            with open(ARCHIVO_JUGADORES, "r", encoding="utf-8") as f:
                s = json.load(f)
                for liga in LIGAS_PROPS:
                    s.setdefault(liga, {"p": {}, "hechos": []})
                return s
        except Exception as e:
            print(f"Aviso leyendo {ARCHIVO_JUGADORES}: {e}")
    return {liga: {"p": {}, "hechos": []} for liga in LIGAS_PROPS}


def guardar_store(s):
    for liga in LIGAS_PROPS:
        for pid, j in s[liga]["p"].items():
            j["g"] = sorted(j["g"], key=lambda r: r[0])[-30:]
        s[liga]["hechos"] = s[liga]["hechos"][-700:]
    try:
        with open(ARCHIVO_JUGADORES, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        print(f"Aviso guardando {ARCHIVO_JUGADORES}: {e}")


def _get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=(5, 15))
        if r.status_code == 200:
            return r.json()
        print(f"   ⚠️ {url.split('?')[0]} -> HTTP {r.status_code}")
    except Exception as e:
        print(f"   ⚠️ {url.split('?')[0]}: {e}")
    DIAG["errores"] += 1
    return None


def _entero(v):
    try:
        if v is None:
            return 0
        s = str(v).strip()
        if "-" in s and not s.startswith("-"):
            s = s.split("-")[0]
        return int(float(s))
    except Exception:
        return 0


def _minutos(v):
    s = str(v or "").strip()
    if not s or s.upper() in ("DNP", "--"):
        return 0.0
    if ":" in s:
        m, sec = s.split(":")[:2]
        try:
            return int(m) + int(sec) / 60.0
        except Exception:
            return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def _nombre_el(txt):
    """'BELLO, RASHEED' -> 'Rasheed Bello'"""
    t = (txt or "").strip()
    if "," in t:
        ape, nom = t.split(",", 1)
        t = f"{nom.strip()} {ape.strip()}"
    return " ".join(w.capitalize() if not w.isupper() or len(w) > 3 else w.capitalize() for w in t.split())


def _registrar(liga_store, jid, nombre, equipo, fila):
    j = liga_store["p"].setdefault(jid, {"n": nombre, "t": equipo, "g": []})
    j["n"], j["t"] = nombre, equipo
    gid = fila[7]
    j["g"] = [r for r in j["g"] if r[7] != gid] + [fila]


# ------------------------------------------------------------------------------------------------
# Descarga de box scores
# ------------------------------------------------------------------------------------------------
def box_nba(store, gid, utc):
    data = _get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary", {"event": gid.replace("nba", "")})
    if not data:
        return False
    bloques = (data.get("boxscore") or {}).get("players") or []
    if len(bloques) != 2:
        return False
    equipos = [f"nba{(b.get('team') or {}).get('id')}" for b in bloques]
    n = 0
    for k, b in enumerate(bloques):
        st = (b.get("statistics") or [{}])[0]
        keys = st.get("keys") or []
        pos = {key: i for i, key in enumerate(keys)}
        need = {"minutes", "points", "rebounds", "assists", "threePointFieldGoalsMade-threePointFieldGoalsAttempted"}
        if not need <= set(pos):
            continue
        for a in st.get("athletes") or []:
            vals = a.get("stats") or []
            if a.get("didNotPlay") or len(vals) < len(keys):
                continue
            mins = _minutos(vals[pos["minutes"]])
            if mins <= 0:
                continue
            ath = a.get("athlete") or {}
            fila = [utc, equipos[1 - k], round(mins, 1), _entero(vals[pos["points"]]), _entero(vals[pos["rebounds"]]),
                    _entero(vals[pos["assists"]]),
                    _entero(vals[pos["threePointFieldGoalsMade-threePointFieldGoalsAttempted"]]), gid]
            _registrar(store["NBA"], f"nba{ath.get('id')}", ath.get("displayName") or "?", equipos[k], fila)
            n += 1
    if n:
        DIAG["box_nba"] += 1
    return n > 0


def temporada_el(utc):
    try:
        t = datetime.fromisoformat(utc.replace("Z", "+00:00"))
    except Exception:
        t = datetime.now(timezone.utc)
    return f"E{t.year if t.month >= 8 else t.year - 1}"


def box_el(store, gid, utc, local_id, visit_id):
    code = gid.replace("el", "")
    data = _get("https://live.euroleague.net/api/Boxscore", {"gamecode": code, "seasoncode": temporada_el(utc)})
    if not data or len(data.get("Stats") or []) != 2:
        return False
    equipos = [local_id, visit_id]
    n = 0
    for k, bloque in enumerate(data["Stats"]):
        for ps in bloque.get("PlayersStats") or []:
            pid = str(ps.get("Player_ID") or "").strip()
            mins = _minutos(ps.get("Minutes"))
            if not pid or mins <= 0:
                continue
            fila = [utc, equipos[1 - k], round(mins, 1), _entero(ps.get("Points")), _entero(ps.get("TotalRebounds")),
                    _entero(ps.get("Assistances")), _entero(ps.get("FieldGoalsMade3")), gid]
            _registrar(store["EL"], f"el{pid}", _nombre_el(ps.get("Player")), equipos[k], fila)
            n += 1
    if n:
        DIAG["box_el"] += 1
    return n > 0


def actualizar_box_scores(store, resultados, t_limite):
    """Descarga los box scores de los partidos terminados que faltan (primero los más recientes)."""
    for liga in LIGAS_PROPS:
        hechos = set(store[liga]["hechos"])
        pendientes = [r for r in resultados["ligas"].get(liga, []) if r["id"] not in hechos]
        pendientes.sort(key=lambda r: r["u"], reverse=True)
        hechos_ciclo = 0
        for r in pendientes[:MAX_BOX_CICLO[liga]]:
            if time.time() > t_limite:
                break
            ok = box_nba(store, r["id"], r["u"]) if liga == "NBA" else box_el(store, r["id"], r["u"], r["hid"], r["aid"])
            if ok:
                store[liga]["hechos"].append(r["id"])
                hechos_ciclo += 1
            time.sleep(0.25)
        print(f"🏀 Jugadores {liga}: +{hechos_ciclo} box scores ({len(store[liga]['hechos'])} partidos, "
              f"{len(store[liga]['p'])} jugadores; faltan {max(0, len(pendientes) - hechos_ciclo)})")


def lesionados_nba(gid):
    """IDs de jugadores Out / Doubtful / Day-To-Day según el parte de ESPN para ese partido."""
    data = _get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary", {"event": gid.replace("nba", "")})
    fuera = set()
    for t in (data or {}).get("injuries") or []:
        for i in t.get("injuries") or []:
            if (i.get("status") or "").lower() in ("out", "doubtful", "day-to-day", "questionable", "suspension"):
                fuera.add(f"nba{(i.get('athlete') or {}).get('id')}")
    DIAG["lesiones"] += len(fuera)
    return fuera, data is not None


# ------------------------------------------------------------------------------------------------
# Modelo de jugador
# ------------------------------------------------------------------------------------------------
def _nb_sobre(media, var, linea):
    """P(X > linea) con binomial negativa (o Poisson si no hay sobredispersión)."""
    k_max = int(math.floor(linea))
    if media <= 0:
        return 0.0
    if var <= media * 1.02:
        p0 = math.exp(-media)
        cdf, pk = p0, p0
        for k in range(1, k_max + 1):
            pk *= media / k
            cdf += pk
        return max(0.0, 1.0 - cdf)
    r = media * media / (var - media)
    p = r / (r + media)
    cdf = 0.0
    for k in range(0, k_max + 1):
        cdf += math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1 - p))
    return max(0.0, 1.0 - cdf)


def _ultimo_partido_equipo(resultados, liga, equipo):
    u = ""
    for r in resultados["ligas"].get(liga, []):
        if (r["hid"] == equipo or r["aid"] == equipo) and r["u"] > u:
            u = r["u"]
    return u


def proyectar_jugador(j, liga, factor_ataque, ultimo_equipo):
    g = [r for r in sorted(j["g"], key=lambda r: r[0]) if r[2] > 0]
    if len(g) < MIN_PARTIDOS_JUG:
        return None
    if ultimo_equipo and g[-1][0][:10] < ultimo_equipo[:10]:
        return None            # no jugó el último partido de su equipo: posible lesión/baja
    ult = g[-10:]
    mins5 = [r[2] for r in ult[-5:]]
    pesos5 = list(range(1, len(mins5) + 1))
    exp_min = sum(m * w for m, w in zip(mins5, pesos5)) / sum(pesos5)
    if exp_min < MIN_MINUTOS[liga]:
        return None
    sd_min = max(3.0, math.sqrt(sum((m - exp_min) ** 2 for m in mins5) / len(mins5)))
    pesos = [0.85 ** (len(ult) - 1 - i) for i in range(len(ult))]
    wmin = sum(r[2] * w for r, w in zip(ult, pesos))
    proy = {"min": round(exp_min, 1)}
    for st in ("pts", "reb", "ast", "tri", "pra"):
        if st == "pra":
            vals = [r[3] + r[4] + r[5] for r in ult]
        else:
            vals = [r[IDX[st]] for r in ult]
        rate = sum(v * w for v, w in zip(vals, pesos)) / wmin
        fac = 1.0 if st == "reb" else (factor_ataque if st != "pra" else 1.0 + (factor_ataque - 1.0) * 0.8)
        media = rate * exp_min * fac
        m_hist = sum(vals) / len(vals)
        v_hist = sum((v - m_hist) ** 2 for v in vals) / max(1, len(vals) - 1)
        disp = max(1.0, v_hist / m_hist) if m_hist > 0 else 1.0
        var = media * disp + (rate * sd_min) ** 2
        proy[st] = (media, var)
    return proy


def _factor_ataque(fuerzas, rival):
    try:
        if fuerzas and fuerzas.get("valido") and rival in fuerzas["de"]:
            return max(0.92, min(1.08, (fuerzas["mu"] + fuerzas["de"][rival]) / fuerzas["mu"]))
    except Exception:
        pass
    return 1.0


def candidatos_partido(store, liga, partido, resultados, fuerzas, fuera):
    """Todas las selecciones de jugador evaluadas: {jid, jugador, equipo, stat, l, p}."""
    loc, vis = partido["local_id"], partido["visitante_id"]
    ult = {loc: _ultimo_partido_equipo(resultados, liga, loc), vis: _ultimo_partido_equipo(resultados, liga, vis)}
    sel = []
    for jid, j in store[liga]["p"].items():
        eq = j.get("t")
        if eq not in (loc, vis) or jid in fuera:
            continue
        rival = vis if eq == loc else loc
        proy = proyectar_jugador(j, liga, _factor_ataque(fuerzas, rival), ult[eq])
        if not proy:
            continue
        for st, lineas in LINEAS.items():
            media, var = proy[st]
            for l in lineas:
                p = min(PROB_TOPE, _nb_sobre(media, var, l))
                if p < 0.55:
                    break
                sel.append({"t": "JUG", "jid": jid, "jugador": j["n"], "equipo": eq, "stat": st, "l": l,
                            "p": p, "media": round(media, 1), "min": proy["min"]})
    return sel


def _corr(a, b):
    if a["t"] == "JUG" and b["t"] == "JUG":
        if a["jid"] == b["jid"]:
            return CORR_MISMO.get(frozenset((a["stat"], b["stat"])), 0.3)
        return -0.05 if a["equipo"] == b["equipo"] else 0.05
    eq_team = a if a["t"] == "ML" else b
    jug = b if eq_team is a else a
    return 0.15 if jug["equipo"] == eq_team["equipo"] else -0.10


def prob_conjunta(patas, n=20000):
    k = len(patas)
    if k == 1:
        return patas[0]["p"]
    C = [[1.0 if i == j else _corr(patas[i], patas[j]) for j in range(k)] for i in range(k)]
    L = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(i + 1):
            s = sum(L[i][m] * L[j][m] for m in range(j))
            if i == j:
                L[i][j] = math.sqrt(max(1e-9, C[i][i] - s))
            else:
                L[i][j] = (C[i][j] - s) / L[j][j]
    umbrales = [_inv_normal(pt["p"]) for pt in patas]
    rng = random.Random("|".join(f"{pt.get('jid', pt['t'])}{pt.get('stat', '')}{pt.get('l', '')}" for pt in patas))
    ok = 0
    for _ in range(n):
        z = [rng.gauss(0, 1) for _ in range(k)]
        x = [sum(L[i][m] * z[m] for m in range(i + 1)) for i in range(k)]
        if all(x[i] < umbrales[i] for i in range(k)):
            ok += 1
    return ok / n


def _inv_normal(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    # aproximación de Acklam
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def texto_pata(pt, partido=None):
    if pt["t"] == "ML":
        return f"Ganador: {pt['nombre_equipo']}"
    return f"{pt['jugador']}: Más de {str(pt['l']).replace('.', ',')} {NOMBRE_STAT[pt['stat']]}"


def elegir_mymatch(cands, patas_equipo):
    """Mejor MyMatch de 2-3 selecciones del mismo partido. Por jugador: su línea más alta con prob >= 78%
    en cada estadística; P+R+A nunca se mezcla con estadísticas sueltas (regla de Winamax); máximo 2 selecciones por jugador."""
    seguras = {}
    for c in cands:
        if c["p"] >= PATA_PROB_MIN:
            k = (c["jid"], c["stat"])
            if k not in seguras or c["l"] > seguras[k]["l"]:
                seguras[k] = c
    pool = sorted(seguras.values(), key=lambda c: (c["p"], c["media"]), reverse=True)[:10] + patas_equipo
    mejor = None
    for k in range(MYMATCH_MAX_PATAS, 1, -1):
        for combo in itertools.combinations(pool, k):
            jugs = [c for c in combo if c["t"] == "JUG"]
            por_j = {}
            for c in jugs:
                por_j.setdefault(c["jid"], []).append(c["stat"])
            if any(len(v) > 2 for v in por_j.values()):
                continue
            # Winamax no deja mezclar P+R+A con puntos/rebotes/asistencias/triples sueltos en la misma
            # combinada (de ningún jugador): o todas las selecciones de jugador son P+R+A o ninguna
            stats = [c["stat"] for c in jugs]
            if "pra" in stats and any(st != "pra" for st in stats):
                continue
            if sum(1 for c in combo if c["t"] == "ML") > 1:
                continue
            p = prob_conjunta(list(combo), n=2500)
            cuota_min = MARGEN_PROPS / max(p, 1e-6)
            if not (MYMATCH_CUOTA[0] <= cuota_min <= MYMATCH_CUOTA[1]):
                continue
            score = p * (1.0 + 0.04 * k)
            if mejor is None or score > mejor["score"]:
                mejor = {"patas": list(combo), "p": p, "score": score}
        if mejor:
            break   # se prefieren 3 selecciones si hay una combinación buena
    if mejor:
        mejor["p"] = prob_conjunta(mejor["patas"], n=20000)
        mejor["cuota_min"] = round(MARGEN_PROPS / mejor["p"], 2)
    return mejor


def elegir_simple(cands):
    ok = [c for c in cands if SIMPLE_PROB[0] <= c["p"] <= SIMPLE_PROB[1] and c["stat"] in ("pts", "tri", "reb", "ast", "pra")]
    if not ok:
        return None
    # la línea más exigente con buena probabilidad, de un jugador con muchos minutos
    return max(ok, key=lambda c: (c["p"] * (1 + c["min"] / 100.0)))


# ------------------------------------------------------------------------------------------------
# Alertas y liquidación
# ------------------------------------------------------------------------------------------------
def _alerta_base(partido, semana_id, id_alerta, patas, p, cuota_min, titulo):
    return {
        "id_partido": str(partido["id"]), "id_alerta": id_alerta,
        "partido": f"{partido['local']} vs {partido['visitante']}",
        "mercado": " + ".join(texto_pata(pt) for pt in patas),
        "tipo_mercado": "MYMATCH" if len(patas) > 1 else "JUGADOR",
        "categoria_visual": "mymatch" if len(patas) > 1 else "jugador",
        "es_mymatch": True, "titulo": titulo,
        "legs": [{"t": pt["t"], "jid": pt.get("jid"), "jugador": pt.get("jugador"), "stat": pt.get("stat"), "l": pt.get("l"),
                  "equipo": pt.get("equipo"), "nombre_equipo": pt.get("nombre_equipo"),
                  "mercado": texto_pata(pt), "partido": f"{partido['local']} vs {partido['visitante']}",
                  "prob": round(pt["p"] * 100, 1), "media": pt.get("media"), "cuota": round(1.0 / pt["p"], 2),
                  "fecha_hora_display": partido.get("fecha_hora_display"), "res": None, "valor": None} for pt in patas],
        "casa": "Winamax (combinada)" if len(patas) > 1 else None,
        "cuota": f"@{cuota_min}", "cuota_minima": True,
        "ev": f"+{round((MARGEN_PROPS - 1) * 100, 1)}%",
        "prob_combinada": round(p * 100, 1), "stake": "1/10",
        "hora": partido.get("hora"), "fecha": partido.get("fecha"), "fecha_hora_display": partido.get("fecha_hora_display"),
        "liga": partido.get("liga"), "liga_code": partido.get("liga_code"),
        "semana_id": semana_id, "es_ht": False, "es_semana_actual": True, "estado_partido": "TIMED",
        "marcador": None, "marcador_final": None, "finalizado": False, "acertado": None,
        "liquidada_telegram": False, "cuotas_reales": False, "solo_modelo": True,
    }


def mensaje_alerta(a, minutos):
    lineas = []
    for i, l in enumerate(a["legs"], 1):
        extra = f" (espera {l['media']})" if l.get("media") is not None else ""
        lineas.append(f"{i}️⃣ <b>{l['mercado']}</b> · prob. {l['prob']}%{extra}")
    es_mm = len(a["legs"]) > 1
    return (
        f"🎯 <b>{'COMBINADA' if es_mm else 'JUGADOR'} — [PRE-PARTIDO]</b>\n\n"
        f"🏆 <b>Competición:</b> {a.get('liga')}\n"
        f"🏀 <b>Partido:</b> {a['partido']}\n"
        f"⏰ <b>Inicio:</b> {a.get('fecha_hora_display')} (en {minutos} min)\n\n"
        + "\n".join(lineas) + "\n\n"
        f"💰 <b>Cuota mínima para tener valor:</b> {a['cuota']}{' (combinada del mismo partido en Winamax)' if es_mm else ''}. "
        f"Juégala solo si tu casa paga esa cuota o más.\n"
        f"🎲 <b>Probabilidad {'de que acierten todas' if es_mm else 'de acierto'}:</b> {a['prob_combinada']}%\n"
        f"💡 <b>Stake Sugerido:</b> {a['stake']} unidades\n"
        f"ℹ️ Si un jugador no juega, su selección suele anularse.\n\n"
        f"⚡ <i>Apex Quant Engine • Modelo de jugadores</i>"
    )


def generar_alertas(partidos_web, store, resultados, fuerzas_por_liga, alertas, semana_id, enviar, app_url, ventana):
    if not PROPS_ACTIVO:
        return 0
    existentes = {a.get("id_alerta") for a in alertas}
    nuevas = 0
    for p in partidos_web:
        liga = p.get("liga_code")
        if liga not in LIGAS_PROPS or p.get("estado") != "TIMED":
            continue
        mr = p.get("minutos_restantes", 0)
        if not (ventana[0] <= mr <= ventana[1]):
            continue
        pid = str(p["id"])
        id_mm, id_s = f"MM_{pid}", f"PROP_{pid}"
        if id_mm in existentes and id_s in existentes:
            continue
        if not p.get("local_id") or not p.get("visitante_id"):
            continue
        fuera = set()
        if liga == "NBA":
            fuera, ok = lesionados_nba(pid)
            if not ok:
                continue   # sin parte de lesiones no se arriesga: se reintenta en el siguiente ciclo
        cands = candidatos_partido(store, liga, p, resultados, fuerzas_por_liga.get(liga), fuera)
        if not cands:
            print(f"   🏀 {p['local']} vs {p['visitante']}: sin datos de jugadores suficientes")
            continue
        patas_eq = []
        f = fuerzas_por_liga.get(liga)
        if p.get("prevision"):
            for lado, eq_id, nombre in (("local", p["local_id"], p["local"]), ("visitante", p["visitante_id"], p["visitante"])):
                prob = (p["prevision"].get(f"prob_{lado}") or 0) / 100.0
                if prob >= 0.75:
                    patas_eq.append({"t": "ML", "equipo": eq_id, "nombre_equipo": nombre, "p": min(PROB_TOPE, prob)})
        if id_mm not in existentes:
            mm = elegir_mymatch(cands, patas_eq)
            if mm:
                a = _alerta_base(p, semana_id, id_mm, mm["patas"], mm["p"], mm["cuota_min"], "Combinada")
                enviar(mensaje_alerta(a, mr), url_accion=app_url, texto_boton="📱 Abrir en Web App")
                alertas.append(a)
                existentes.add(id_mm)
                nuevas += 1
                print(f"   🧩 Combinada {a['partido']}: {a['mercado']} @{mm['cuota_min']} (p {round(mm['p'] * 100, 1)}%)")
        if id_s not in existentes:
            usados = {(l.get("jid"), l.get("stat")) for a in alertas if a.get("id_alerta") == id_mm for l in a.get("legs", [])}
            s = elegir_simple([c for c in cands if (c["jid"], c["stat"]) not in usados])
            if s:
                cuota_min = round(MARGEN_PROPS / s["p"], 2)
                a = _alerta_base(p, semana_id, id_s, [s], s["p"], cuota_min, "Jugador")
                enviar(mensaje_alerta(a, mr), url_accion=app_url, texto_boton="📱 Abrir en Web App")
                alertas.append(a)
                existentes.add(id_s)
                nuevas += 1
                print(f"   👤 Jugador {a['partido']}: {a['mercado']} @{cuota_min} (p {round(s['p'] * 100, 1)}%)")
    return nuevas


def liquidar(alertas, store, partidos_map, enviar, app_url):
    for a in alertas:
        if not a.get("es_mymatch") or a.get("finalizado"):
            continue
        liga = a.get("liga_code")
        pid = str(a.get("id_partido"))
        info = partidos_map.get(pid)
        if info:
            a["estado_partido"] = info["estado"]
            if info.get("marcador"):
                a["marcador"] = info["marcador"]
        if not info or info["estado"] != "FINISHED":
            continue
        if liga not in store or pid not in set(store[liga]["hechos"]):
            continue   # falta el box score: se liquida en otro ciclo
        aciertos = fallos = nulas = 0
        for leg in a["legs"]:
            if leg["t"] == "ML":
                gl, gv = info.get("goles_local"), info.get("goles_visitante")
                gano_local = gl is not None and gv is not None and gl > gv
                es_local = leg.get("nombre_equipo") == info.get("local")
                leg["res"] = gano_local if es_local else (not gano_local)
                leg["valor"] = info.get("marcador")
            else:
                filas = [r for r in store[liga]["p"].get(leg["jid"], {}).get("g", []) if r[7] == pid]
                if not filas:
                    leg["res"] = "NULA"
                    leg["valor"] = "no jugó"
                else:
                    r = filas[0]
                    v = r[3] + r[4] + r[5] if leg["stat"] == "pra" else r[IDX[leg["stat"]]]
                    leg["valor"] = v
                    leg["res"] = v > leg["l"]
            if leg["res"] == "NULA":
                nulas += 1
            elif leg["res"]:
                aciertos += 1
            else:
                fallos += 1
        a["finalizado"] = True
        a["estado_partido"] = "FINISHED"
        a["marcador_final"] = info.get("marcador")
        if fallos:
            a["acertado"] = False
        elif aciertos == 0:
            a["acertado"] = None
            a["push"] = True
        else:
            a["acertado"] = True
            if nulas:   # la casa recalcula la cuota sin las selecciones anuladas: se aplica el mismo recorte
                cuota = float(a["cuota"].replace("@", ""))
                quitar = math.prod(l["cuota"] for l in a["legs"] if l["res"] == "NULA")
                a["cuota"] = f"@{max(1.01, round(cuota / quitar, 2))}"
        if not a.get("liquidada_telegram"):
            det = "\n".join(
                f"{'✅' if l['res'] is True else ('➖' if l['res'] == 'NULA' else '❌')} {l['mercado']} → {l.get('valor')}"
                for l in a["legs"])
            if a.get("push"):
                titulo = "➖ <b>Resultado:</b> ANULADA (no jugó)"
            else:
                titulo = "✅ <b>Resultado:</b> ACERTADA" if a["acertado"] else "❌ <b>Resultado:</b> PERDIDA"
            enviar(f"🎯 <b>{('Combinada' if a.get('titulo') in (None, 'MyMatch') else a.get('titulo')).upper()}</b> — {a['partido']} ({a.get('marcador_final') or ''})\n{det}\n\n{titulo}\n"
                   f"💰 <b>Cuota mínima:</b> {a['cuota']} • 💡 <b>Stake:</b> {a['stake']}\n\n⚡ <i>Apex Quant Engine</i>",
                   url_accion=app_url, texto_boton="📱 Ver en la Web App")
            a["liquidada_telegram"] = True

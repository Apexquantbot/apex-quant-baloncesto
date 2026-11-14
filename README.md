# Apex Quant Baloncesto

Versión de Apex Quant para baloncesto: **NBA, Euroliga, Liga Endesa (ACB) y Basketball Champions League**.
Misma estructura, estilo y funcionamiento que Apex Quant fútbol y Apex Quant Mercados.

- Web / app: https://apexquantbot.github.io/apex-quant-baloncesto/
- Canal de Telegram: https://t.me/apex_quant_basket

## Cómo funciona

| Pieza | Qué hace |
|---|---|
| `bot_quant.py` | El bot. Se ejecuta cada 15 minutos con GitHub Actions. |
| `datos.json` | Lo que muestra la web (partidos, pronósticos, alertas). Lo genera el bot. |
| `resultados.json` | Histórico de resultados con el que el modelo calcula la fuerza de cada equipo. |
| `historico_picks.json` / `historico_mensual.json` | Libro de todas las picks liquidadas (no se reinicia los lunes). |

**Fuentes de datos (gratis):**
- NBA: ESPN (calendario, marcadores en directo y escudos).
- Euroliga, ACB y Champions League: TheSportsDB (clave pública gratuita).
- Cuotas: The Odds API (solo cubre NBA y Euroliga).

**Pronósticos:**
- NBA y Euroliga: se comparan las cuotas reales de las casas con el consenso del mercado y con el modelo estadístico
  propio. Solo se envía alerta si hay ventaja (+EV ≥ 3 %) y el modelo la respalda. Mercados: ganador, hándicap y total de puntos.
- ACB y Champions League: como no hay cuotas en la API, el bot da el pronóstico del modelo para favoritos claros
  (probabilidad ≥ 70 %) con una **cuota mínima**: solo tiene valor si tu casa paga esa cuota o más.

## Secretos del repositorio (Settings → Secrets and variables → Actions)

| Nombre | Obligatorio | Valor |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Sí | Token del bot de Telegram (BotFather) |
| `TELEGRAM_CHAT_ID` | Sí | `@apex_quant_basket` (o el ID numérico del canal) |
| `THE_ODDS_API_KEY` | Recomendado | Clave de The Odds API (mejor una propia, distinta de la del bot de fútbol) |
| `TSDB_KEY` | No | Solo si algún día se paga TheSportsDB premium |

🔞 +18 · Juega con responsabilidad · Información estadística, no es asesoramiento.

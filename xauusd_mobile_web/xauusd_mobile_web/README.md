# XAUUSD Analysis Web App (Python)

Mobile-friendly dashboard for XAUUSD candle analysis. **No order placement and no trading automation.**

## Architecture

- `server.py`: FastAPI dashboard and secure ingestion endpoint; SQLite candle storage; EMA20/50, RSI14, ADX14, Bollinger Bands and 20-candle high/low.
- `mt5_bridge.py`: Windows-only reader of **completed** MT5 candles, uploads M5/M15/H1/H4 every 60 seconds.
- `templates/index.html`: responsive browser dashboard, HTML canvas candlesticks, 60-second refresh.
- `Dockerfile`: server deployment (e.g. Railway).

## Important requirements

- Windows laptop with MT5 **running and logged in** to a broker account with XAUUSD candles available.
- Python **3.13 64-bit** recommended for MT5 integration. You can keep Python 3.15 installed; use `py -3.13` to select 3.13.
- For access anywhere, deploy the **server** to a public HTTPS host; the bridge runs on the laptop. **Do not expose your laptop directly to the internet.**
- The bridge sends **candles only**, not MT5 login credentials or orders.
- Broker symbols may differ (e.g. XAUUSDm); set `MT5_SYMBOL` accordingly.

## 1. Deploy server (Railway example)

1. Upload this folder to a private GitHub repository. **Never commit `.env` or `.env.bridge`**.
2. Railway: New Project > Deploy from GitHub repo; Railway detects the Dockerfile.
3. Add environment variables `DASH_USER`, `DASH_PASSWORD`, `INGEST_TOKEN` with **different, long random** values. Set `DB_PATH=/data/market.db`.
4. Add a **persistent volume mounted at `/data`**. Without it, SQLite data can disappear after redeployment/restart. Keep the server to **one replica** because SQLite is local to the volume.
5. Generate a Railway public HTTPS domain. Open it on your phone; browser asks for username/password (HTTP Basic over HTTPS).
6. Do not use the demo placeholders as actual passwords. Use HTTPS only.

## 2. Start bridge on Windows

1. Keep MT5 running and logged in; enable XAUUSD in Market Watch.
2. Install Python 3.13 (64-bit) if necessary. Run `py -3.13 --version` in CMD to check.
3. Copy `.env.bridge.example` to `.env.bridge` (Windows File Explorer may hide extensions).
4. Set `SERVER_URL` to Railway HTTPS URL, `INGEST_TOKEN` to exactly the server's ingest token, and `MT5_SYMBOL` to the symbol shown by your broker.
5. Double-click `start_bridge_windows.bat` (or run it from CMD to see errors).
6. Wait for `M5 300 candles uploaded` etc. Then refresh dashboard on your phone.

## 3. Local testing without hosting

1. Copy `.env.example` to `.env` and set long credentials.
2. **Load environment variables** in CMD: `for /f "usebackq tokens=1,* delims==" %a in (".env") do @set "%a=%b"` (run from project folder). Or set them manually via Windows environment settings.
3. Run `start_local_server.bat`, then visit http://127.0.0.1:8000 on the same laptop.
4. Note: `.env` is not automatically read by the server. Railway env vars are read automatically.
5. Bridge requires an HTTPS URL, so local testing of the bridge needs a properly secured HTTPS endpoint.

## Interpretation

- BULLISH: close > EMA20 > EMA50; BEARISH: close < EMA20 < EMA50; otherwise SIDEWAYS.
- WATCH BUY: BULLISH + ADX>=20 + RSI 50..70; WATCH SELL: BEARISH + ADX>=20 + RSI 30..50; otherwise WAIT.
- These are **illustrative heuristics**, not validated trading signals. Backtest before relying on them.
- All analysis uses completed candles, and the latest candle time is shown. If the laptop or MT5 is off, the dashboard displays **stale historical data**, not live quotes.

## Security notes

- The server uses HTTP Basic login; suitable for a **private single-user MVP only** over HTTPS. For multi-user production, add proper sessions, rate limiting, and MFA.
- Store passwords and tokens only in host environment variables or local `.env.bridge`; never publish them.
- Keep your GitHub repo private and rotate tokens if accidentally exposed.
- Deploy only behind HTTPS. Do not forward ports from your home router.
- No broker passwords are uploaded; bridge calls only `mt5.copy_rates_from_pos`.

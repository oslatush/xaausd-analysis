import os, sqlite3, math, secrets, hmac, hashlib, base64, time, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from pydantic import BaseModel, Field
from bbma_signals import analyze_bbma

app = FastAPI(title='XAUUSD Analysis', docs_url=None, redoc_url=None)

DB = os.getenv('DB_PATH', './market.db')
TOKEN = os.getenv('INGEST_TOKEN', '')
USER = os.getenv('DASH_USER', 'admin')
PASS = os.getenv('DASH_PASSWORD', '')
BBMA_USER = os.getenv('BBMA_USER', '')
BBMA_PASS = os.getenv('BBMA_PASSWORD', '')
FRAMES = {'M1', 'M5', 'M15', 'H1', 'H4', 'D1', 'W1'}
BBMA_FRAMES = {'M15', 'H1', 'H4', 'D1', 'W1'}

def db():
    conn = sqlite3.connect(DB, timeout=15)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE IF NOT EXISTS candles (symbol TEXT NOT NULL, tf TEXT NOT NULL, ts INTEGER NOT NULL, o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL, volume REAL NOT NULL, PRIMARY KEY(symbol,tf,ts))')
    conn.commit()
    return conn

# Signed, HttpOnly cookie. Set SESSION_SECRET in Railway (independent of passwords).
SESSION_SECRET = os.getenv('SESSION_SECRET', '')
SESSION_TTL = 12 * 60 * 60

def make_session(role):
    payload = base64.urlsafe_b64encode(json.dumps({'role': role, 'exp': int(time.time()) + SESSION_TTL}, separators=(',', ':')).encode()).decode().rstrip('=')
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + '.' + signature

def get_role(request: Request):
    token = request.cookies.get('xau_session', '')
    if not SESSION_SECRET or '.' not in token:
        return None
    try:
        payload, signature = token.rsplit('.', 1)
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        if data.get('exp', 0) <= time.time() or data.get('role') not in ('ema', 'bbma'):
            return None
        return data['role']
    except (ValueError, TypeError, KeyError, UnicodeError):
        return None

def require_role(request: Request, role: str):
    if get_role(request) != role:
        raise HTTPException(401, 'Unauthorized')

class LoginPayload(BaseModel):
    username: str
    password: str

@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    if get_role(request):
        return RedirectResponse('/', status_code=303)
    return (Path(__file__).parent / 'templates' / 'login.html').read_text(encoding='utf-8')

@app.post('/login')
def login(body: LoginPayload):
    if not SESSION_SECRET or len(SESSION_SECRET) < 32:
        raise HTTPException(503, 'SESSION_SECRET belum disetel dengan aman')
    ema_ok = bool(USER and PASS) and secrets.compare_digest(body.username, USER) and secrets.compare_digest(body.password, PASS)
    bbma_ok = bool(BBMA_USER and BBMA_PASS) and secrets.compare_digest(body.username, BBMA_USER) and secrets.compare_digest(body.password, BBMA_PASS)
    if USER == BBMA_USER and BBMA_USER and PASS == BBMA_PASS:
        raise HTTPException(503, 'Konfigurasi akun EMA dan BBMA harus berbeda')
    if not (ema_ok or bbma_ok):
        raise HTTPException(401, 'Username atau password salah')
    response = JSONResponse({'ok': True, 'dashboard': 'ema' if ema_ok else 'bbma'})
    response.set_cookie('xau_session', make_session('ema' if ema_ok else 'bbma'), max_age=SESSION_TTL, httponly=True, secure=True, samesite='lax', path='/')
    return response


@app.post('/api/logout')
def logout():
    response = JSONResponse({'ok': True})
    response.delete_cookie(
        'xau_session',
        path='/',
        secure=True,
        httponly=True,
        samesite='lax'
    )
    return response


def ema(a, n):
    out=[]; value=None; k=2/(n+1)
    for x in a:
        value=x if value is None else value+(x-value)*k
        out.append(value)
    return out

def analyze(rows):
    if len(rows) < 50:
        return {'state':'INSUFFICIENT_DATA','signal':'WAIT','reason':'Minimal 50 candle diperlukan untuk analisis.'}
    closes=[x['c'] for x in rows]
    opens=[x['o'] for x in rows]
    ema7=ema(closes,7); ema10=ema(opens,10); ema50=ema(closes,50)
    price=closes[-1]
    cross_up=ema7[-2]<=ema10[-2] and ema7[-1]>ema10[-1]
    cross_down=ema7[-2]>=ema10[-2] and ema7[-1]<ema10[-1]
    trend='BULLISH' if price>ema50[-1] else 'BEARISH' if price<ema50[-1] else 'SIDEWAYS'
    signal='WAIT'; reason='Tidak ada crossover baru.'
    if cross_up:
        if price>ema50[-1]:
            signal='BUY'; reason='EMA7 Close cross ke atas EMA10 Open, harga di atas EMA50.'
        else: reason='Cross BUY terjadi, tetapi harga tidak di atas EMA50.'
    elif cross_down:
        if price<ema50[-1]:
            signal='SELL'; reason='EMA7 Close cross ke bawah EMA10 Open, harga di bawah EMA50.'
        else: reason='Cross SELL terjadi, tetapi harga tidak di bawah EMA50.'
    return {'state':trend,'signal':signal,'reason':reason,'price':round(price,5),'ema7':round(ema7[-1],5),'ema10':round(ema10[-1],5),'ema50':round(ema50[-1],5),'support20':round(min(x['l'] for x in rows[-20:]),5),'resistance20':round(max(x['h'] for x in rows[-20:]),5)}

class Candle(BaseModel):
    ts: int = Field(ge=1000000000, le=9999999999)
    o: float
    h: float
    l: float
    c: float
    volume: float = 0

class Upload(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    timeframe: Literal['M1','M5','M15','H1','H4','D1','W1']
    candles: list[Candle] = Field(min_length=1, max_length=1000)

def get_rows(tf):
    with db() as conn:
        records=conn.execute('SELECT ts,o,h,l,c,volume FROM candles WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT 300',('XAUUSD',tf)).fetchall()
    return [dict(zip(['ts','o','h','l','c','volume'],x)) for x in reversed(records)]

def candle_time(rows):
    return datetime.fromtimestamp(rows[-1]['ts'],timezone.utc).isoformat() if rows else None

@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    role = get_role(request)
    if not role:
        return RedirectResponse('/login', status_code=303)
    filename = 'index.html' if role == 'ema' else 'bbma.html'
    return (Path(__file__).parent / 'templates' / filename).read_text(encoding='utf-8')

@app.get('/bbma')
def bbma_redirect():
    return RedirectResponse('/', status_code=303)

@app.get('/api/market')
def market(request: Request, tf: str = 'M5'):
    require_role(request, 'ema')
    if tf not in FRAMES: raise HTTPException(400, 'Unsupported timeframe')
    rows = get_rows(tf)
    return {'symbol':'XAUUSD','timeframe':tf,'candles':rows,'analysis':analyze(rows),'last_candle_utc':candle_time(rows),'source':'MT5 bridge' if rows else 'No data yet'}

@app.get('/api/bbma/market')
def bbma_market(request: Request, tf: str = 'H1'):
    require_role(request, 'bbma')
    if tf not in BBMA_FRAMES: raise HTTPException(400, 'Unsupported timeframe')
    rows = get_rows(tf)
    return {'symbol':'XAUUSD','timeframe':tf,'candles':rows,'analysis':analyze_bbma(rows),'last_candle_utc':candle_time(rows),'source':'MT5 bridge' if rows else 'No data yet'}

@app.post('/api/ingest')
def ingest(body:Upload, request:Request):
    if not TOKEN or not secrets.compare_digest(request.headers.get('X-Ingest-Token',''),TOKEN):
        raise HTTPException(401,'Unauthorized')
    if body.symbol.upper()!='XAUUSD': raise HTTPException(400,'Only XAUUSD allowed')
    for v in body.candles:
        if not all(math.isfinite(x) for x in [v.o,v.h,v.l,v.c,v.volume]) or v.l>min(v.o,v.c) or v.h<max(v.o,v.c) or v.h<v.l or v.volume<0:
            raise HTTPException(422,'Invalid OHLC')
    with db() as conn:
        conn.executemany('INSERT INTO candles(symbol,tf,ts,o,h,l,c,volume) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,tf,ts) DO UPDATE SET o=excluded.o,h=excluded.h,l=excluded.l,c=excluded.c,volume=excluded.volume', [('XAUUSD',body.timeframe,x.ts,x.o,x.h,x.l,x.c,x.volume) for x in body.candles])
        conn.execute('DELETE FROM candles WHERE rowid IN (SELECT rowid FROM candles WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT -1 OFFSET 3000)',('XAUUSD',body.timeframe))
        conn.commit()
    return {'ok':True,'received':len(body.candles)}

@app.get('/health')
def health(): return {'ok':True}

import os, sqlite3, json, math, statistics, secrets
from datetime import datetime, timezone
from contextlib import contextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from typing import Literal
from pathlib import Path

app=FastAPI(title='XAUUSD Analysis',docs_url=None,redoc_url=None)
security=HTTPBasic()
DB=os.getenv('DB_PATH','./market.db')
TOKEN=os.getenv('INGEST_TOKEN','')
USER=os.getenv('DASH_USER','admin')
PASS=os.getenv('DASH_PASSWORD','')
FRAMES={'M5','M15','H1','H4'}

def db():
    conn=sqlite3.connect(DB,timeout=15)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE IF NOT EXISTS candles (symbol TEXT NOT NULL, tf TEXT NOT NULL, ts INTEGER NOT NULL, o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL, volume REAL NOT NULL, PRIMARY KEY(symbol,tf,ts))')
    conn.commit()
    return conn

def auth(creds:HTTPBasicCredentials=Depends(security)):
    if not PASS or not (secrets.compare_digest(creds.username,USER) and secrets.compare_digest(creds.password,PASS)):
        raise HTTPException(401,'Invalid login',headers={'WWW-Authenticate':'Basic'})

def ema(a,n):
    out=[]; value=None; k=2/(n+1)
    for x in a:
        value=x if value is None else value+(x-value)*k
        out.append(value)
    return out

def rsi(closes,n=14):
    if len(closes)<n+1:return None
    d=[closes[i]-closes[i-1] for i in range(1,len(closes))]
    gain=sum(max(x,0) for x in d[:n])/n; loss=sum(max(-x,0) for x in d[:n])/n
    for x in d[n:]:
        gain=(gain*(n-1)+max(x,0))/n;loss=(loss*(n-1)+max(-x,0))/n
    if loss==0:return 100.0 if gain>0 else 50.0
    return 100-100/(1+gain/loss)

def adx(rows,n=14):
    if len(rows)<2*n+1:return None
    tr=[];pdm=[];mdm=[]
    for i in range(1,len(rows)):
        prev,cur=rows[i-1],rows[i]
        up=cur['h']-prev['h'];down=prev['l']-cur['l']
        pdm.append(up if up>down and up>0 else 0)
        mdm.append(down if down>up and down>0 else 0)
        tr.append(max(cur['h']-cur['l'],abs(cur['h']-prev['c']),abs(cur['l']-prev['c'])))
    st=sum(tr[:n]);sp=sum(pdm[:n]);sm=sum(mdm[:n]);dx=[]
    for i in range(n-1,len(tr)):
        if i>=n:
            st=st-st/n+tr[i];sp=sp-sp/n+pdm[i];sm=sm-sm/n+mdm[i]
        plus=100*sp/st if st else 0;minus=100*sm/st if st else 0
        dx.append(100*abs(plus-minus)/(plus+minus) if plus+minus else 0)
    if len(dx)<n:return None
    a=sum(dx[:n])/n
    for d in dx[n:]:a=(a*(n-1)+d)/n
    return round(a,2)

def analyze(rows):
    if len(rows)<50:return {'state':'INSUFFICIENT_DATA','reason':'Minimal 50 candle diperlukan untuk analisis','signal':'WAIT'}
    c=[x['c'] for x in rows]; e20=ema(c,20)[-1];e50=ema(c,50)[-1]
    bb_mid=statistics.mean(c[-20:]);sd=statistics.pstdev(c[-20:]);upper=bb_mid+2*sd;lower=bb_mid-2*sd
    strength=adx(rows);momentum=rsi(c);price=c[-1]
    trend='BULLISH' if price>e20>e50 else 'BEARISH' if price<e20<e50 else 'SIDEWAYS'
    signal='WAIT';reason='Belum ada keselarasan tren dan momentum.'
    if strength is not None and strength>=20 and momentum is not None:
        if trend=='BULLISH' and 50<=momentum<=70:
            signal='WATCH BUY';reason='Harga di atas EMA20/EMA50, ADX >= 20, RSI 50–70.'
        elif trend=='BEARISH' and 30<=momentum<=50:
            signal='WATCH SELL';reason='Harga di bawah EMA20/EMA50, ADX >= 20, RSI 30–50.'
    return {'state':trend,'signal':signal,'reason':reason,'price':round(price,5),'ema20':round(e20,5),'ema50':round(e50,5),'bb_upper':round(upper,5),'bb_middle':round(bb_mid,5),'bb_lower':round(lower,5),'rsi14':round(momentum,2) if momentum is not None else None,'adx14':strength,'support20':round(min(x['l'] for x in rows[-20:]),5),'resistance20':round(max(x['h'] for x in rows[-20:]),5)}

class Candle(BaseModel):
    ts:int=Field(ge=1000000000,le=9999999999)
    o:float;h:float;l:float;c:float
    volume:float=0
class Upload(BaseModel):
    symbol:str=Field(min_length=1,max_length=32)
    timeframe:Literal['M5','M15','H1','H4']
    candles:list[Candle]=Field(min_length=1,max_length=1000)

@app.get('/',response_class=HTMLResponse)
def home(_:None=Depends(auth)):
    return (Path(__file__).parent/'templates'/'index.html').read_text(encoding='utf-8')

@app.get('/api/market')
def market(tf:str='M5',_:None=Depends(auth)):
    if tf not in FRAMES:raise HTTPException(400,'Unsupported timeframe')
    with db() as conn:
        records=conn.execute('SELECT ts,o,h,l,c,volume FROM candles WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT 300',('XAUUSD',tf)).fetchall()
    rows=[dict(zip(['ts','o','h','l','c','volume'],x)) for x in reversed(records)]
    last=rows[-1]['ts'] if rows else None
    return {'symbol':'XAUUSD','timeframe':tf,'candles':rows,'analysis':analyze(rows),'last_candle_utc':datetime.fromtimestamp(last,timezone.utc).isoformat() if last else None,'source':'MT5 bridge' if rows else 'No data yet'}

@app.post('/api/ingest')
def ingest(body:Upload,request:Request):
    if not TOKEN or not secrets.compare_digest(request.headers.get('X-Ingest-Token',''),TOKEN):raise HTTPException(401,'Unauthorized')
    if body.symbol.upper()!='XAUUSD':raise HTTPException(400,'Only XAUUSD allowed')
    for v in body.candles:
        if not all(math.isfinite(x) for x in [v.o,v.h,v.l,v.c,v.volume]) or v.l>min(v.o,v.c) or v.h<max(v.o,v.c) or v.h<v.l or v.volume<0:raise HTTPException(422,'Invalid OHLC')
    with db() as conn:
        conn.executemany('INSERT INTO candles(symbol,tf,ts,o,h,l,c,volume) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,tf,ts) DO UPDATE SET o=excluded.o,h=excluded.h,l=excluded.l,c=excluded.c,volume=excluded.volume', [('XAUUSD',body.timeframe,x.ts,x.o,x.h,x.l,x.c,x.volume) for x in body.candles])
        conn.execute('DELETE FROM candles WHERE rowid IN (SELECT rowid FROM candles WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT -1 OFFSET 3000)',('XAUUSD',body.timeframe))
        conn.commit()
    return {'ok':True,'received':len(body.candles)}

@app.get('/health')
def health():return {'ok':True}

"""Windows-only MT5 reader. Sends OHLC data; never places trades."""
import os,time,sys
from pathlib import Path
import httpx
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent/'.env.bridge')
try:import MetaTrader5 as mt5
except ImportError:
    print('Library MetaTrader5 belum tersedia untuk Python ini. Gunakan Python 3.13 64-bit.');sys.exit(1)
URL=os.getenv('SERVER_URL','').rstrip('/')
TOKEN=os.getenv('INGEST_TOKEN','')
SYMBOL=os.getenv('MT5_SYMBOL','XAUUSD')
if not URL.startswith('https://') or not TOKEN:
    print('Isi SERVER_URL (https://...) dan INGEST_TOKEN di .env.bridge');sys.exit(1)
if not mt5.initialize():
    print('MT5 tidak tersambung:',mt5.last_error());sys.exit(1)
print('MT5 connected. Login:',mt5.account_info().login if mt5.account_info() else 'unknown')
if not mt5.symbol_select(SYMBOL,True):
    print('Symbol tidak tersedia:',SYMBOL,'(cek nama broker, misalnya XAUUSDm)');mt5.shutdown();sys.exit(1)
frames={'M5':mt5.TIMEFRAME_M5,'M15':mt5.TIMEFRAME_M15,'H1':mt5.TIMEFRAME_H1,'H4':mt5.TIMEFRAME_H4}
try:
    with httpx.Client(timeout=25) as client:
        while True:
            for name,tf in frames.items():
                try:
                    rates=mt5.copy_rates_from_pos(SYMBOL,tf,1,300) # exclude current unfinished candle
                    if rates is None or len(rates)==0:
                        print(name,'no candles:',mt5.last_error());continue
                    candles=[{'ts':int(r['time']),'o':float(r['open']),'h':float(r['high']),'l':float(r['low']),'c':float(r['close']),'volume':float(r['tick_volume'])} for r in rates]
                    resp=client.post(URL+'/api/ingest',json={'symbol':'XAUUSD','timeframe':name,'candles':candles},headers={'X-Ingest-Token':TOKEN})
                    resp.raise_for_status()
                    print(name,len(candles),'candles uploaded')
                except Exception as e:print(name,'ERROR:',str(e)[:200])
            time.sleep(60)
except KeyboardInterrupt:print('Stopped')
finally:mt5.shutdown()

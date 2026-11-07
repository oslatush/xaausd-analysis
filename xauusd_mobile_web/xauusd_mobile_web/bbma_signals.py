"""BBMA-inspired heuristic scanner for CLOSED OHLC candles.
Historical signals use only current and previous candles (no look-ahead).
Not official BBMA Oma Ally rules or trading advice.
"""
from math import sqrt

SIGNALS = ('EXTREME', 'MHV', 'CSAK', 'CSM', 'REENTRY')


def sma(a, n):
    return [None if i + 1 < n else sum(a[i-n+1:i+1]) / n for i in range(len(a))]


def lwma(a, n):
    denom = n * (n + 1) / 2
    return [None if i + 1 < n else sum((j + 1) * v for j, v in enumerate(a[i-n+1:i+1])) / denom for i in range(len(a))]


def ema(a, n):
    out = []
    v = None
    for x in a:
        v = x if v is None else v + (x-v) * 2 / (n+1)
        out.append(v)
    return out


def analyze_bbma(rows):
    if len(rows) < 60:
        return {
            'signal': 'WAIT', 'detected': [], 'history': [],
            'reason': 'Minimal 60 candle close diperlukan',
            'indicators': {},
        }

    o = [float(r['o']) for r in rows]
    h = [float(r['h']) for r in rows]
    l = [float(r['l']) for r in rows]
    c = [float(r['c']) for r in rows]
    mid = sma(c, 20)
    upper = [None if i < 19 else mid[i] + 2 * sqrt(sum((x-mid[i])**2 for x in c[i-19:i+1])/20) for i in range(len(c))]
    lower = [None if i < 19 else mid[i] - 2 * sqrt(sum((x-mid[i])**2 for x in c[i-19:i+1])/20) for i in range(len(c))]
    ma5h, ma10h = lwma(h, 5), lwma(h, 10)
    ma5l, ma10l = lwma(l, 5), lwma(l, 10)
    ma50 = ema(c, 50)

    history = []
    for i in range(59, len(rows)):
        p = i - 1
        ts = rows[i].get('ts')

        def add(name, direction, why):
            history.append({'ts': ts, 'name': name, 'direction': direction, 'reason': why})

        if h[i] > upper[i] and ma5h[i] > upper[i] and c[i] < upper[i]:
            add('EXTREME', 'SELL', 'MA5 High dan wick melewati Upper BB, close kembali di dalam BB')
        if l[i] < lower[i] and ma5l[i] < lower[i] and c[i] > lower[i]:
            add('EXTREME', 'BUY', 'MA5 Low dan wick melewati Lower BB, close kembali di dalam BB')
        if h[p] > upper[p] and h[i] <= h[p] and c[i] < upper[i]:
            add('MHV', 'SELL', 'Setelah sentuhan Upper BB, high tidak meningkat dan close di dalam BB')
        if l[p] < lower[p] and l[i] >= l[p] and c[i] > lower[i]:
            add('MHV', 'BUY', 'Setelah sentuhan Lower BB, low tidak menurun dan close di dalam BB')
        if c[i] > o[i] and c[i] > mid[i] and c[p] <= mid[p]:
            add('CSAK', 'BUY', 'Candle bullish menutup melampaui Mid BB')
        if c[i] < o[i] and c[i] < mid[i] and c[p] >= mid[p]:
            add('CSAK', 'SELL', 'Candle bearish menutup di bawah Mid BB')
        if c[i] > o[i] and c[i] > upper[i] and c[p] <= upper[p]:
            add('CSM', 'BUY', 'Candle bullish menutup di luar Upper BB')
        if c[i] < o[i] and c[i] < lower[i] and c[p] >= lower[p]:
            add('CSM', 'SELL', 'Candle bearish menutup di luar Lower BB')
        if c[i] > ma50[i] and c[i] > mid[i] and l[i] <= max(ma5l[i], ma10l[i]) and c[i] > max(ma5l[i], ma10l[i]) and c[i] > o[i]:
            add('REENTRY', 'BUY', 'Tren di atas MA50/Mid BB, retrace ke MA5/10 Low, candle bullish memantul')
        if c[i] < ma50[i] and c[i] < mid[i] and h[i] >= min(ma5h[i], ma10h[i]) and c[i] < min(ma5h[i], ma10h[i]) and c[i] < o[i]:
            add('REENTRY', 'SELL', 'Tren di bawah MA50/Mid BB, retrace ke MA5/10 High, candle bearish tertolak')

    latest_ts = rows[-1].get('ts')
    detected = [dict(name=x['name'], direction=x['direction'], reason=x['reason'])
                for x in history if x['ts'] == latest_ts]
    i = len(rows) - 1
    return {
        'signal': next((x['direction'] for x in detected if x['name'] == 'REENTRY'), 'WAIT'),
        'detected': detected,
        'history': history[-250:],
        'last_candle_ts': latest_ts,
        'indicators': {k: round(v, 5) for k, v in {
            'bb_upper': upper[i], 'bb_mid': mid[i], 'bb_lower': lower[i],
            'ma5_high': ma5h[i], 'ma10_high': ma10h[i],
            'ma5_low': ma5l[i], 'ma10_low': ma10l[i], 'ema50': ma50[i]
        }.items()},
        'note': 'Aturan deteksi bersifat prototipe, perlu validasi manual sebelum digunakan trading.'
    }

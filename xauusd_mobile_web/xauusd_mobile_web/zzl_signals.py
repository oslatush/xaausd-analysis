def ema(values, period):
    result = []
    current = None
    multiplier = 2 / (period + 1)

    for value in values:
        current = (
            value if current is None
            else current + multiplier * (value - current)
        )
        result.append(current)

    return result


def analyze_zzl(rows):
    if len(rows) < 51:
        return {
            "signal": "WAIT",
            "trend": "INSUFFICIENT_DATA",
            "reason": "Minimal 51 candle diperlukan.",
            "history": [],
        }

    closes = [float(row["c"]) for row in rows]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    history = []

    for i in range(1, len(rows)):
        buy = ema20[i - 1] <= ema50[i - 1] and ema20[i] > ema50[i]
        sell = ema20[i - 1] >= ema50[i - 1] and ema20[i] < ema50[i]

        if buy or sell:
            history.append({
                "ts": rows[i]["ts"],
                "direction": "BUY" if buy else "SELL",
                "name": "ZZL",
            })

    signal = "WAIT"
    reason = "Tidak ada crossover baru pada candle terakhir."

    if history and history[-1]["ts"] == rows[-1]["ts"]:
        signal = history[-1]["direction"]
        reason = f"EMA20 crossover EMA50: {signal}"

    return {
        "signal": signal,
        "trend": "BULLISH" if ema20[-1] > ema50[-1] else "BEARISH",
        "reason": reason,
        "ema20": round(ema20[-1], 5),
        "ema50": round(ema50[-1], 5),
        "history": history,
    }

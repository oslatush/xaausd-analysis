
import os
import json
import sqlite3
import logging
import threading
import urllib.request
import urllib.parse
import urllib.error

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DB = os.getenv("DB_PATH", "./market.db")
LOCK = threading.RLock()

FRAMES = ("M1", "M5", "M15", "H1", "H4", "D1", "W1")
BBMA_FRAMES = ("M5", "M15", "H1", "H4", "D1", "W1")

BBMA_CONDITIONS = (
    "EXTREME",
    "MHV",
    "CSAK",
    "CSM",
    "REENTRY"
)

DEFAULT_SETTINGS = {
    "ema": {
        "enabled": False,
        "timeframes": []
    },
    "bbma": {
        "enabled": False,
        "timeframes": [],
        "conditions": []
    },
    "zzl": {
        "enabled": False,
        "timeframes": []
    }
}


class ScannerSettings(BaseModel):
    enabled: bool = False
    timeframes: list[str] = Field(default_factory=list)


class BBMASettings(ScannerSettings):
    conditions: list[str] = Field(default_factory=list)


class TelegramSettings(BaseModel):
    ema: ScannerSettings
    bbma: BBMASettings
    zzl: ScannerSettings


def db():
    conn = sqlite3.connect(DB, timeout=20)
    conn.execute("PRAGMA busy_timeout=20000")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS telegram_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            config TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS telegram_seen_candles (
            tf TEXT PRIMARY KEY,
            ts INTEGER NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS telegram_sent (
            scanner TEXT NOT NULL,
            tf TEXT NOT NULL,
            condition TEXT NOT NULL,
            direction TEXT NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (
                scanner,
                tf,
                condition,
                direction,
                ts
            )
        )
    """)

    conn.execute(
        """
        INSERT OR IGNORE INTO telegram_settings (id, config)
        VALUES (1, ?)
        """,
        (json.dumps(DEFAULT_SETTINGS),)
    )

    conn.commit()
    return conn


def get_settings():
    with db() as conn:
        row = conn.execute(
            "SELECT config FROM telegram_settings WHERE id = 1"
        ).fetchone()

        return json.loads(row[0])


def save_settings(config):
    with LOCK:
        with db() as conn:
            conn.execute(
                """
                UPDATE telegram_settings
                SET config = ?
                WHERE id = 1
                """,
                (json.dumps(config),)
            )
            conn.commit()


def make_router(get_role):
    router = APIRouter()

    def require_login(request):
        if get_role(request) != "all":
            raise HTTPException(
                status_code=401,
                detail="Unauthorized"
            )

    @router.get("/api/telegram/settings")
    def read_settings(request: Request):
        require_login(request)
        return get_settings()

    @router.put("/api/telegram/settings")
    def update_settings(
        body: TelegramSettings,
        request: Request
    ):
        require_login(request)

        config = body.model_dump()

        for scanner in ("ema", "bbma", "zzl"):
            allowed = (
                BBMA_FRAMES
                if scanner == "bbma"
                else FRAMES
            )

            selected = config[scanner]["timeframes"]

            if (
                len(selected) != len(set(selected))
                or any(tf not in allowed for tf in selected)
            ):
                raise HTTPException(
                    422,
                    f"Invalid {scanner} timeframes"
                )

        conditions = config["bbma"]["conditions"]

        if (
            len(conditions) != len(set(conditions))
            or any(
                condition not in BBMA_CONDITIONS
                for condition in conditions
            )
        ):
            raise HTTPException(
                422,
                "Invalid BBMA conditions"
            )

        save_settings(config)

        return {
            "ok": True,
            "settings": config
        }
        
    @router.post("/api/telegram/test")
    def test_telegram(request: Request):
        get_role(request)

        if send_telegram(
            "✅ TEST XAUUSD\n"
            "Telegram notification berhasil terhubung!"
        ):
            return {"ok": True, "message": "Telegram sent"}

        raise HTTPException(
            status_code=502,
            detail="Telegram send failed. Check Railway logs."
        )
    return router


def send_telegram(message):
    token = os.getenv(
        "TELEGRAM_BOT_TOKEN", ""
    ).strip()

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID", ""
    ).strip()

    if not token or not chat_id:
        logger.warning(
            "Telegram bot token or chat ID is missing"
        )
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=8
        ) as response:
            result = json.load(response)

        return bool(result.get("ok"))

    except (
        urllib.error.URLError,
        TimeoutError,
        ValueError
    ) as error:
        logger.warning(
            "Telegram notification failed: %s",
            error
        )
        return False


def format_message(
    scanner,
    timeframe,
    condition,
    direction,
    candle,
    reason
):
    candle_time = datetime.fromtimestamp(
        int(candle["ts"]),
        timezone.utc
    ).astimezone(
        timezone(timedelta(hours=7))
    )

    icon = "🟢" if direction == "BUY" else "🔴"

    title = (
        f"{scanner} {condition}"
        if condition != "CROSS"
        else scanner
    )

    return (
        f"{icon} XAUUSD {title} — {direction}\n"
        f"Timeframe: {timeframe}\n"
        f"Close: {float(candle['c']):,.2f}\n"
        f"Candle: {candle_time:%d-%m-%Y %H:%M} WIB\n"
        f"Reason: {reason}"
    )


def process_ingest(
    timeframe,
    rows,
    ema_analysis,
    bbma_analysis,
    zzl_analysis
):
    if not rows:
        return

    latest = rows[-1]
    ts = int(latest["ts"])

    with LOCK:
        with db() as conn:

            previous = conn.execute(
                """
                SELECT ts
                FROM telegram_seen_candles
                WHERE tf = ?
                """,
                (timeframe,)
            ).fetchone()

            # First synchronization: ignore historical signals.
            if previous is None:
                conn.execute(
                    """
                    INSERT INTO telegram_seen_candles (tf, ts)
                    VALUES (?, ?)
                    """,
                    (timeframe, ts)
                )
                conn.commit()
                return

            # Ignore repeated or older candles.
            if ts <= previous[0]:
                return

            config = get_settings()
            events = []

            # EMA scanner
            if (
                config["ema"]["enabled"]
                and timeframe in config["ema"]["timeframes"]
            ):
                result = ema_analysis(rows)

                if result.get("signal") in ("BUY", "SELL"):
                    events.append((
                        "EMA",
                        "CROSS",
                        result["signal"],
                        result.get("reason", "")
                    ))

            # BBMA scanner
            if (
                config["bbma"]["enabled"]
                and timeframe in config["bbma"]["timeframes"]
            ):
                result = bbma_analysis(rows)

                for signal in result.get("detected", []):
                    if (
                        signal.get("name")
                        in config["bbma"]["conditions"]
                        and signal.get("direction")
                        in ("BUY", "SELL")
                    ):
                        events.append((
                            "BBMA",
                            signal["name"],
                            signal["direction"],
                            signal.get("reason", "")
                        ))

            # ZZL scanner
            if (
                config["zzl"]["enabled"]
                and timeframe in config["zzl"]["timeframes"]
            ):
                result = zzl_analysis(rows)

                if result.get("signal") in ("BUY", "SELL"):
                    events.append((
                        "ZZL",
                        "CROSS",
                        result["signal"],
                        result.get("reason", "")
                    ))

            failed = False

            for scanner, condition, direction, reason in events:

                key = (
                    scanner,
                    timeframe,
                    condition,
                    direction,
                    ts
                )

                sent = conn.execute(
                    """
                    SELECT 1
                    FROM telegram_sent
                    WHERE scanner = ?
                      AND tf = ?
                      AND condition = ?
                      AND direction = ?
                      AND ts = ?
                    """,
                    key
                ).fetchone()

                if sent:
                    continue

                message = format_message(
                    scanner,
                    timeframe,
                    condition,
                    direction,
                    latest,
                    reason
                )

                if send_telegram(message):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO telegram_sent
                        (
                            scanner,
                            tf,
                            condition,
                            direction,
                            ts
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        key
                    )
                    conn.commit()
                else:
                    failed = True

            # Only advance if every notification succeeded.
            if not failed:
                conn.execute(
                    """
                    UPDATE telegram_seen_candles
                    SET ts = ?
                    WHERE tf = ?
                    """,
                    (ts, timeframe)
                )
                conn.commit()

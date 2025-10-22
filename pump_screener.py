# pump_screener.py
# Multi-exchange Pump/Dump Screener (BYBIT, BINANCE, BINGX, GATEIO)
# Форматирует красивые алерты в Telegram (Pump ✅ / Dump 🚨), фильтр >=5%, MEGA-стиль >10%

import os
import time
import math
import requests
from datetime import datetime, timezone
import ccxt

# =================== TELEGRAM ===================
TELEGRAM_TOKEN   = "8366933527:AAFMTI9ya9gKD-bD5jN_tr5LFYAYXmNnX0w"
TELEGRAM_CHAT_ID = "-1003176782660" 

# =================== Параметры скринера ===================
LOOP_SECONDS        = 20          # пауза между проходами
USE_USDT_ONLY       = True        # только USDT-пары
THRESH_1M           = 3.5         # порог инфо по 1m (оставляем для справки)
THRESH_5M           = 6.0         # порог инфо по 5m (оставляем для справки)
MIN_QUOTE_VOL_5M    = 10000       # минимальный котируемый объём за ~5m (в $)
COOLDOWN_SECONDS    = 900         # анти-спам на тикер/направление
ENABLED_EXCHANGES   = ["binance", "bybit", "bingx", "mexc", "gateio"]
MAX_SYMBOLS_PER_EX  = 300          # сканируем первые N символов

# Фильтр шума и усиленный стиль
MIN_CHANGE_PCT      = 5.0         # не шлём сигналы меньше 5%
MEGA_CHANGE_PCT     = 10.0        # «мега»-стиль для >10%

# =================== Утилиты ===================
def utc_hms() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def pct(a: float, b: float) -> float:
    try:
        return (a / b - 1.0) * 100.0
    except Exception:
        return 0.0

def fmt_price(x: float) -> str:
    # аккуратное форматирование цены: мелкое — больше знаков
    if x >= 100: return f"{x:.2f}"
    if x >= 1:   return f"{x:.4f}"
    if x >= 0.1: return f"{x:.5f}"
    return f"{x:.8f}"

def tv_symbol(sym: str) -> str:
    # для TradingView достаточно базового тикера без /USDT
    base = sym.split(":")[0].split("/")[0]
    return base

# =================== Telegram ===================
def tg_send(text: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print("[TG ERROR]", r.text)
    except Exception as e:
        print("[TG EXC]", e)

def tg_selfcheck() -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[CHECK] TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is empty.")
        return False
    try:
        me = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=10
        ).json()
        print("[CHECK getMe]", me)
        if not me.get("ok"):
            return False
        sm = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": "Screener self-check"},
            timeout=10
        ).json()
        print("[CHECK sendMessage]", sm)
        return bool(sm.get("ok"))
    except Exception as e:
        print("[CHECK EXC]", e)
        return False

# =================== Данные с бирж ===================
def estimate_quote_volume(ohlcv_rows) -> float:
    # берём последние 5 свечей 1m: price_now * sum(base_volume_last5)
    if not ohlcv_rows:
        return 0.0
    closes = [r[4] for r in ohlcv_rows if len(r) > 4 and r[4] is not None]
    vols   = [r[5] for r in ohlcv_rows if len(r) > 5 and r[5] is not None]
    if not closes or not vols:
        return 0.0
    price_now = closes[-1]
    base_vol5 = sum(vols[-5:])
    return float(price_now) * float(base_vol5)

def safe_fetch_ohlcv(ex, symbol, timeframe, limit):
    try:
        return ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except Exception:
        try:
            ex.load_markets(True)
            return ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as e:
            return []

def get_symbols_usdt(ex) -> list:
    markets = ex.load_markets()
    symbols = []
    for s, m in markets.items():
        # исключаем неактивные
        if m.get("active", True) is False:
            continue
        # только USDT
        if USE_USDT_ONLY and ("USDT" not in s.upper()):
            continue
        u = s.upper()
        # исключаем левередж-токены
        if any(x in u for x in ["UP/", "DOWN/", "BULL/", "BEAR/", "3S/", "3L/", "5S/", "5L/"]):
            continue
        if "/USDT" in s or "USDT/" in s:
            symbols.append(s)
    return sorted(symbols)

# =================== Красивый алерт ===================
def send_alert(symbol: str, ex_name: str, direction: str,
               m1: float, m5: float, move: float,
               p_from: float, p_to: float, qv5: float) -> None:
    """
    direction: 'Pump' или 'Dump'
    move: главный % сдвиг (максимум по модулю из 1m/5m)
    """
    base = symbol.split("/")[0]
    # Заголовок
    if direction == "Pump":
        header = "🟩✅ <b>PUMP ALERT</b>\n"
    else:
        header = "🟥🚨 <b>DUMP ALERT</b>\n"

    # Усиленный стиль для >10%
    bolt = "🔥🔥🔥 " if abs(move) >= MEGA_CHANGE_PCT else ""

    # Сообщение
    msg = (
        f"{bolt}{header}"
        f"<b>{base}</b> <i>на {ex_name.capitalize()}</i>\n"
        f"<b>Move:</b> {move:+.2f}%  (1m: {m1:+.2f}% | 5m: {m5:+.2f}%)\n"
        f"<b>Price:</b> {fmt_price(p_from)} → {fmt_price(p_to)}\n"
        f"<b>Vol(5m):</b> ${qv5:,.0f}\n"
        f"🕒 {utc_hms()} UTC\n"
        f"🔗 <a href='https://www.tradingview.com/chart/?symbol={tv_symbol(symbol)}'>Открыть график</a>"
    )
    tg_send(msg)

# =================== Ядро ===================
class Screener:
    def __init__(self):
        self.cooldowns = {}     # key: ex:symbol:dir -> unix
        self.ex = {}            # биржи
        self.symbols = {}       # кэш списка тикеров

    def build_exchanges(self):
        # создаём клиенты ccxt
        for name in ENABLED_EXCHANGES:
            try:
                if name == "binance":
                    # возможны региональные блоки -> ловим в run_once
                    self.ex[name] = ccxt.binance({"options": {"defaultType": "spot"}})
                elif name == "bybit":
                    self.ex[name] = ccxt.bybit()
                elif name == "bingx":
                    self.ex[name] = ccxt.bingx()
                elif name == "mexc":
                    self.ex[name] = ccxt.mexc()
                elif name == "gateio":
                    self.ex[name] = ccxt.gateio()
            except Exception as e:
                print(f"[exch error] {name} ->", e)

    def allowed(self, key: str) -> bool:
        return time.time() >= self.cooldowns.get(key, 0)

    def set_cooldown(self, key: str):
        self.cooldowns[key] = time.time() + COOLDOWN_SECONDS

    def run_once(self):
        for name, ex in self.ex.items():
            # грузим список тикеров
            syms = self.symbols.get(name)
            if not syms:
                try:
                    syms = get_symbols_usdt(ex)
                    if MAX_SYMBOLS_PER_EX:
                        syms = syms[:MAX_SYMBOLS_PER_EX]
                    self.symbols[name] = syms
                    print(f"[{utc_hms()}] {name}: loaded {len(syms)} symbols")
                except Exception as e:
                    # региональные блоки/451/403 и т.п.
                    print(f"[{utc_hms()}] {name}: load_markets error:", e)
                    continue

            for s in syms:
                rows = safe_fetch_ohlcv(ex, s, "1m", 6)
                if len(rows) < 6:
                    continue

                c_now = rows[-1][4]
                c_1m  = rows[-2][4]
                c_5m  = rows[-6][4]

                m1 = pct(c_now, c_1m)
                m5 = pct(c_now, c_5m)
                move = m1 if abs(m1) >= abs(m5) else m5

                qv5 = estimate_quote_volume(rows)

                # Фильтры
                if qv5 < MIN_QUOTE_VOL_5M:
                    continue
                if abs(move) < MIN_CHANGE_PCT:
                    continue

                direction = "Pump" if move > 0 else "Dump"
                key = f"{name}:{s}:{direction}"
                if not self.allowed(key):
                    continue

                self.set_cooldown(key)
                p_from = c_5m if abs(m5) >= abs(m1) else c_1m
                p_to   = c_now

                send_alert(s, name, direction, m1, m5, move, p_from, p_to, qv5)

# =================== main ===================
if __name__ == "__main__":
    if tg_selfcheck():
        tg_send("🤖 Screener started.")
    s = Screener()
    s.build_exchanges()
    while True:
        try:
            s.run_once()
        except KeyboardInterrupt:
            tg_send("🛑 Screener stopped.")
            print("[STOP] Bye.")
            break
        except Exception as e:
            print("[ERROR]", e)
        time.sleep(LOOP_SECONDS)


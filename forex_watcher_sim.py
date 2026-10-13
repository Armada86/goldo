"""Offline simulation of overlapping Broker F watcher processes (shadow mode) on a scripted tick path: no network, no database.

A starts first and trades; C starts 3 s later and exits at once (not needed); B starts 15 s in, waits as standby and takes over when A's 30 s run
ends. The scripted price has a one-tick wick under the 4158.00 breakout trigger (the B #76 shape), then a rally and a sell-off. Expected:
exactly one trade, opened ~0.1 s after the wick by A and closed by B after the handover. Run: python forex_watcher_sim.py (about a minute)."""
import sys, threading, time, logging
from datetime import datetime, timedelta, timezone
import pandas as pd

import forex_stream, broker_b, forex_watcher as fw
from forex_stream import PriceStream

logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(threadName)s %(name)s %(message)s", datefmt="%H:%M:%S")
logging.getLogger("forex-stream").setLevel(logging.WARNING)

T0 = time.time()
LOCK = threading.Lock()
SENT, TRADES, EVENTS = [], [], []
LEASE = {"holder": None, "expires": 0.0, "ends": None, "standby": None, "standby_exp": 0.0, "state": {}}
TRADE = {"open": None, "closed": []}
STREAMS = []

# ---- scripted price: ask path (bid = ask - 0.4) -------------------------------------------------------------------
def ask_at(t):
    if t < 6:  return 4159.2
    if 6 <= t < 6.5:  return 4157.82      # one-tick wick under the 4158.00 trigger (B #76 shape)
    if t < 9:  return 4159.3             # back above -> touch of the trigger
    if t < 20: return 4159.3 + (t - 9) * 0.9   # rally: +~10 over 11 s (trail should activate)
    if t < 30: return 4169.2
    if t < 36: return 4169.2 - (t - 30) * 2.5  # sell-off through the trailed stop
    return 4154.0

def source():
    while True:
        t = time.time() - T0
        if t > 55: return
        ask = ask_at(t)
        tick = {"ts": datetime.now(timezone.utc), "bid": round(ask - 0.4, 2), "ask": round(ask, 2)}
        for s in list(STREAMS):
            s._push(dict(tick))
        time.sleep(0.25)

# ---- fakes ---------------------------------------------------------------------------------------------------------
class FakeStream(PriceStream):
    def start(self):
        if self not in STREAMS: STREAMS.append(self)
        self.last_tick_at = time.monotonic()
    def stop(self):
        if self in STREAMS: STREAMS.remove(self)

class FakeClient:
    username = "u"; session_token = "t"
    def market_id(self): return 1
    def refresh_session(self): pass
    def get_bars(self, count, price_type, market_name=None):
        end = pd.Timestamp.now(tz="UTC").floor("min") - pd.Timedelta(minutes=2)
        idx = pd.date_range(end=end, periods=count, freq="min")
        base = 4160.0 + (0.4 if price_type == "ASK" else 0.0)
        return pd.DataFrame({"datetime": idx, "open": base, "high": base + 0.3, "low": base - 0.3, "close": base})
    def get_ticks(self, count=100, market_name=None): return []
    def get_open_positions(self): return []

def now_s(): return time.time() - T0
def utcnow(): return datetime.now(timezone.utc)

fw.ForexClient = FakeClient
fw.PriceStream = FakeStream
fw.init_db = lambda: None
fw.is_market_closed = lambda: False
fw._within_entry_window = lambda now: True
fw.trading_pause_reason = lambda now, b: None
fw.get_pending_forex_b_commands = lambda: []
fw.get_last_close_ts_forex_b = lambda: None
fw.get_recent_readings = lambda *a, **k: []
fw.get_block_rules = lambda: None
fw.fetch_gold_candles = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
fw.touch_forex_b_heartbeat = lambda mode, tick_ts=None: None
fw.get_forex_b_heartbeat = lambda: None
fw.send_telegram_message = lambda text: SENT.append((round(now_s(), 1), text))
fw.set_forex_b_stop_order = lambda *a, **k: None
fw.build_exit_context = lambda *a, **k: {}
fw.stop_loss_threshold = lambda: 4.0
fw.trailing_stop_params = lambda: (3.0, 2.0)
fw._fade_lock_level = lambda trade: None
fw.forex_b_level_history = lambda *a, **k: {"count": 0, "stopped_out": False, "last_open_ts": None}
broker_b._prior_forecasts = lambda f: []
FORECAST = {"id": 99, "forecast_date": datetime.now(fw.DISPLAY_TZ).date() if hasattr(fw, "DISPLAY_TZ") else None,
            "ts": utcnow() - timedelta(hours=1),
            "levels": {"session": "TA3", "price": 4159, "scenarios": [
                {"name": "bull_breakout", "trigger": 4158.00}]}, }
from broker_b import DISPLAY_TZ
FORECAST["forecast_date"] = datetime.now(DISPLAY_TZ).date()
fw.get_latest_ta_forecast = lambda: FORECAST

def get_open():
    with LOCK: return dict(TRADE["open"]) if TRADE["open"] else None
fw.get_open_forex_b_trade = get_open
def insert_trade(mode, rule_name, trade_type, trigger_price, fill_price, touch_ts, now, forecast_id, trigger_text, ctx, forex_order_id=None):
    with LOCK:
        TRADE["open"] = {"id": len(TRADES) + 1, "mode": mode, "rule_name": rule_name, "trade_type": trade_type,
                         "entry_price": fill_price, "open_ts": now, "forex_order_id": forex_order_id, "status": "Open"}
        TRADES.append((round(now_s(), 1), rule_name, trade_type, fill_price))
        EVENTS.append((round(now_s(), 2), "OPEN", threading.current_thread().name, fill_price))
        return TRADE["open"]["id"]
fw.insert_forex_b_trade = insert_trade
def close_trade(trade_id, exit_price, exit_ts, pnl, stop_level, reason, close_order_id, ctx):
    with LOCK:
        TRADE["closed"].append((trade_id, exit_price, pnl, reason)); TRADE["open"] = None
        EVENTS.append((round(now_s(), 2), "CLOSE", threading.current_thread().name, round(exit_price, 2), round(pnl, 2), reason))
fw.close_forex_b_trade = close_trade

# lease with the same semantics as the SQL
def l_acquire(holder, ends_at, state=None, ttl=20):
    with LOCK:
        now = time.time()
        if LEASE["holder"] in (None, holder) or LEASE["expires"] < now:
            LEASE.update(holder=holder, expires=now + ttl, ends=ends_at)
            if state is not None: LEASE["state"] = state
            if LEASE["standby"] == holder: LEASE["standby"] = None
            return {"state": LEASE["state"]}
        return None
def l_status():
    with LOCK:
        now = time.time()
        return {"holder": LEASE["holder"], "alive": LEASE["expires"] > now,
                "ends_in": (LEASE["ends"] - utcnow()).total_seconds() if LEASE["ends"] else None,
                "standby": LEASE["standby"], "standby_alive": bool(LEASE["standby"]) and LEASE["standby_exp"] > now}
def l_register(holder, ttl=20):
    with LOCK:
        now = time.time()
        if LEASE["standby"] in (None, holder) or LEASE["standby_exp"] < now:
            LEASE.update(standby=holder, standby_exp=now + ttl); return True
        return False
def l_release(holder, state=None):
    with LOCK:
        if LEASE["holder"] == holder:
            LEASE["expires"] = time.time() - 1; LEASE["ends"] = None
            if state is not None: LEASE["state"] = state
            EVENTS.append((round(now_s(), 2), "RELEASE", threading.current_thread().name))
fw.forex_legacy_watcher_alive = lambda: False
fw.forex_lease_acquire, fw.forex_lease_status = l_acquire, l_status
fw.forex_lease_register_standby, fw.forex_lease_release = l_register, l_release

import storage
storage.get_recent_readings = lambda *a, **k: []
storage.get_latest_ta_forecast = lambda: FORECAST
STEP_TIMES = []
_orig_step = fw.Watcher.step
def timed_step(self, now, fresh):
    t = time.perf_counter(); r = _orig_step(self, now, fresh); STEP_TIMES.append(time.perf_counter() - t); return r
fw.Watcher.step = timed_step
fw.STANDBY_LEAD_SECONDS = 20
fw.HARD_EXTRA_SECONDS = 10
fw.ENTRY_MAX_FEED_AGE_SECONDS = 20

def proc(name, delay, minutes):
    time.sleep(delay)
    fw.run(minutes, "shadow")
    EVENTS.append((round(now_s(), 2), "EXIT", name))

if __name__ == "__main__":
    threading.Thread(target=source, daemon=True, name="src").start()
    a = threading.Thread(target=proc, args=("A", 0.5, 0.5), name="A")      # 30 s run, starts at ~0.5 s
    b = threading.Thread(target=proc, args=("B", 15, 0.5), name="B")       # starts at 15 s: 15 s before A ends -> standby
    c = threading.Thread(target=proc, args=("C", 3, 0.5), name="C")        # starts at 3 s: A has ~27 s left (> 20 s lead) -> exits at once
    for t in (a, b, c): t.start()
    for t in (a, b, c): t.join(timeout=70)
    print("\n=== EVENTS ==="); [print(e) for e in sorted(EVENTS, key=lambda e: e[0])]
    print("\n=== TRADES opened ==="); [print(t) for t in TRADES]
    print("closed:", TRADE["closed"])
    import statistics
    print("step ms: mean %.1f p95 %.1f max %.1f n=%d" % (1000*statistics.mean(STEP_TIMES), 1000*sorted(STEP_TIMES)[int(.95*len(STEP_TIMES))], 1000*max(STEP_TIMES), len(STEP_TIMES)))
    print("\n=== Telegram ==="); [print(s) for s in SENT]

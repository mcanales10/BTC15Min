#!/usr/bin/env python3
"""
Simmer FastLoop Trading Skill (15-Minute Edition)
Trades Polymarket BTC 15-minute fast markets using Binance spot momentum.
"""

import os
import sys
import json
import argparse
import time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from simmer_sdk.skill import load_config, update_config, get_config_path

sys.stdout.reconfigure(line_buffering=True)

# =============================================================================
# Configuration Schema
# =============================================================================

CONFIG_SCHEMA = {
    "entry_threshold": {"default": 0.05, "type": float},
    "min_momentum_pct": {"default": 0.05, "type": float},
    "max_position": {"default": 10.0, "type": float},
    "signal_source": {"default": "binance", "type": str},
    "lookback_minutes": {"default": 15, "type": int},
    "min_time_remaining": {"default": 30, "type": int},
    "asset": {"default": "BTC", "type": str},
    "window": {"default": "15m", "type": str},
    "max_open_exposure": {"default": 20.0, "type": float},
    "take_profit_pct": {"default": 0.20, "type": float},
    "stop_loss_pct": {"default": 0.10, "type": float},
    "daily_loss_limit": {"default": 20.0, "type": float},
    "pause_hours_after_loss": {"default": 12, "type": int},
    "resolution_exit_seconds": {"default": 15, "type": int},
}

cfg = load_config(CONFIG_SCHEMA, __file__, slug="btc-15m-momentum-trader")

SCAN_INTERVAL_SECONDS = 30
MAX_SPREAD_PCT = 0.06
POLY_FEE_RATE = 0.25
POLY_FEE_EXPONENT = 2

# =============================================================================
# State Management (Paper, Spend, Guards)
# =============================================================================

def _get_path(name):
    from pathlib import Path
    return Path(__file__).parent / name

def _load_json(name, default):
    path = _get_path(name)
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default

def _save_json(name, data):
    with open(_get_path(name), "w") as f:
        json.dump(data, f, indent=2)

def _load_paper_state():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state = _load_json("paper_state.json", {})
    if state.get("date") != today:
        return {"date": today, "spent": 0.0, "trades": 0, "realized_pnl": 0.0, "open_positions": []}
    return state

# =============================================================================
# API Clients & Data Fetching
# =============================================================================

_client = None
def get_client(live=True):
    global _client
    if _client is None:
        from simmer_sdk import SimmerClient
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            print("Error: SIMMER_API_KEY environment variable not set")
            sys.exit(1)
        _client = SimmerClient(api_key=api_key, live=live)
    return _client

def _api_request(url, timeout=10):
    try:
        req = Request(url, headers={"User-Agent": "simmer-fastloop/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

CLOB_API = "https://clob.polymarket.com"

def fetch_live_midpoint(token_id):
    res = _api_request(f"{CLOB_API}/midpoint?token_id={quote(str(token_id))}")
    if res and not res.get("error"):
        try: return float(res["mid"])
        except: pass
    return None

def get_binance_momentum(symbol="BTCUSDT", lookback=15):
    """Pulls true spot momentum from Binance 1m klines."""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit={lookback}"
    res = _api_request(url)
    if not res or isinstance(res, dict): return None
    if len(res) < 2: return None

    try:
        price_then = float(res[0][4])
        price_now = float(res[-1][4])
        momentum_pct = ((price_now - price_then) / price_then) * 100
        direction = "up" if momentum_pct > 0 else "down"

        volumes = [float(c[5]) for c in res]
        avg_vol = sum(volumes) / len(volumes)
        latest_vol = float(res[-1][5])
        vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 1.0

        return {
            "momentum_pct": momentum_pct, "direction": direction,
            "price_now": price_now, "price_then": price_then,
            "volume_ratio": vol_ratio
        }
    except Exception:
        return None

# =============================================================================
# Trade Management
# =============================================================================

def manage_paper_positions(log):
    state = _load_paper_state()
    open_pos = state.get("open_positions", [])
    if not open_pos: return state

    remaining = []
    for pos in open_pos:
        clob = pos.get("clob_token_ids", [])
        yes_price = fetch_live_midpoint(clob[0]) if clob else None
        if yes_price is None:
            remaining.append(pos)
            continue

        current_price = yes_price if pos.get("side") == "yes" else (1 - yes_price)
        target = float(pos.get("target_price", 0.0))
        stop = float(pos.get("stop_price", 0.0))
        
        reason = None
        if current_price >= target > 0: reason = "take_profit"
        elif current_price <= stop < 1: reason = "stop_loss"

        if reason:
            shares = float(pos.get("shares", 0))
            entry = float(pos.get("entry_price", 0))
            gross = shares * (current_price - entry)
            # Simplified fee calc for paper simulation
            fee = shares * (entry * 0.01 + current_price * 0.01) 
            realized = gross - fee
            state["realized_pnl"] = state.get("realized_pnl", 0.0) + realized
            log(f"\n  ✅ [PAPER EXIT] Sold {shares:.1f} shares @ ${current_price:.3f} ({reason}, PnL: ${realized:.2f})", force=True)
        else:
            remaining.append(pos)

    state["open_positions"] = remaining
    _save_json("paper_state.json", state)
    return state

def manage_live_positions(positions, log):
    """Future framework for managing live exits via Simmer API."""
    for pos in positions:
        # In a fully live environment, we would check live midpoint
        # and trigger get_client().trade() to sell the position if TP/SL is hit.
        pass

# =============================================================================
# Main Strategy Loop
# =============================================================================

def run_fast_market_strategy(dry_run=True, quiet=False):
    def log(msg, force=False):
        if not quiet or force: print(msg)

    get_client(live=not dry_run)
    
    # 1. Manage existing positions
    paper_state = manage_paper_positions(log)
    
    # 2. Check if we are currently holding a position (Short-circuit logic)
    if dry_run:
        has_open_position = len(paper_state.get("open_positions", [])) > 0
    else:
        live_positions = get_client().get_positions()
        fast_pos = [p for p in live_positions if "up or down" in (p.get("question", "") or "").lower()]
        has_open_position = len(fast_pos) > 0
        manage_live_positions(fast_pos, log)

    if has_open_position:
        if not quiet:
            sys.stdout.write("\r  ⏱️ Active position detected. Monitoring second-by-second...          ")
            sys.stdout.flush()
        return True # Returns True to trigger 1-second sleep

    # 3. Discover new markets if no position is held
    log(f"\n🔍 Scanning {cfg['window']} {cfg['asset']} markets...")
    markets = get_client().get_fast_markets(asset=cfg['asset'], window=cfg['window'], limit=10)
    
    best = None
    for m in markets:
        if getattr(m, 'is_live_now', False):
            best = m
            break

    if not best:
        log("  No live tradeable markets found right now.")
        return False

    log(f"\n🎯 Selected: {best.question}")
    
    # Fetch price and momentum
    tokens = [best.polymarket_token_id] if getattr(best, 'polymarket_token_id', None) else []
    market_yes_price = fetch_live_midpoint(tokens[0]) if tokens else None
    if market_yes_price is None:
        log("  ⏸️ Cannot fetch CLOB price.")
        return False

    momentum = get_binance_momentum(symbol=f"{cfg['asset']}USDT", lookback=cfg['lookback_minutes'])
    if not momentum: return False

    log(f"  Binance {cfg['asset']} Momentum: {momentum['momentum_pct']:+.3f}% ({momentum['direction']})")

    if abs(momentum['momentum_pct']) < cfg['min_momentum_pct']:
        log("  ⏸️ Momentum too weak. Skipping.")
        return False

    # Execute Paper Trade
    side = "yes" if momentum['direction'] == "up" else "no"
    price = market_yes_price if side == "yes" else (1 - market_yes_price)
    
    if dry_run:
        target = round(price * (1 + cfg['take_profit_pct']), 6)
        stop = round(max(0.001, price * (1 - cfg['stop_loss_pct'])), 6)
        paper_state.setdefault("open_positions", []).append({
            "market_id": best.id,
            "side": side,
            "shares": cfg['max_position'] / price,
            "entry_price": price,
            "clob_token_ids": tokens,
            "target_price": target,
            "stop_price": stop
        })
        _save_json("paper_state.json", paper_state)
        log(f"\n  ✅ [PAPER ENTER] {side.upper()} @ ${price:.3f} (TP: ${target:.3f} | SL: ${stop:.3f})", force=True)
        return True # Trade entered, switch to monitoring speed

    return False

# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    print("⚡ Starting 15-Minute BTC Momentum Bot...")
    
    last_heartbeat = time.time()
    HEARTBEAT_INTERVAL = 900 # 15 minutes in seconds

    while True:
        try:
            # 1. Run the strategy
            is_monitoring = run_fast_market_strategy(dry_run=not args.live, quiet=args.quiet)
            
            # 2. Heartbeat Check
            current_time = time.time()
            if current_time - last_heartbeat >= HEARTBEAT_INTERVAL:
                timestamp = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
                print(f"💓 [{timestamp}] Heartbeat: Bot is alive and scanning...")
                last_heartbeat = current_time

            # 3. Dynamic Sleep
            sleep_time = 1 if is_monitoring else SCAN_INTERVAL_SECONDS
            if not is_monitoring and not args.quiet:
                print(f"\n⏳ Scanning again in {sleep_time}s...\n")
            
            time.sleep(sleep_time)

        except Exception as e:
            print(f"\nLoop error: {e}")
            time.sleep(SCAN_INTERVAL_SECONDS)

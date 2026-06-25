#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   OPTIONS MANAGER  v5.4  — PRODUCTION TRADING SYSTEM            ║
║                                                                  ║
║  🚀 NEW IN v5.4:                                                 ║
║   • Multi-broker API support (Zerodha, Angel, Upstox)           ║
║   • Google Sheets live dashboard syncing                        ║
║   • Email & SMS breach alerts                                   ║
║   • Historical backtesting engine                               ║
║   • Performance metrics & analytics tracking                    ║
║                                                                  ║
║  ✅ 10 Live Strategies: Iron Fly, Condor, Butterfly, Credit     ║
║     Spread, BWB, Strangle, Calendar, Debit Spread,             ║
║     Long Options, Debit Straddle                                ║
║                                                                  ║
║  📊 Real-time monitoring with breach alerts & remote dashboard  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import sys, json, math, time, signal, threading, os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import yfinance as yf

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ═══════════════════════════════════════════════════════════════════
#  v5.4 NEW MODULE IMPORTS
# ═══════════════════════════════════════════════════════════════════

# These are optional - system falls back gracefully if not available
try:
    from broker_api_handler import create_broker_connection
    HAS_BROKER_API = True
except ImportError:
    HAS_BROKER_API = False

try:
    from google_sheets_dashboard import GoogleSheetsDashboard, DashboardMonitor
    HAS_SHEETS = True
except ImportError:
    HAS_SHEETS = False

try:
    from alert_system import AlertManager
    HAS_ALERTS = True
except ImportError:
    HAS_ALERTS = False

try:
    from performance_analytics import TradeLog, PerformanceMetrics, PerformanceReport
    HAS_ANALYTICS = True
except ImportError:
    HAS_ANALYTICS = False

try:
    from backtesting_engine import BacktestEngine, BacktestScenario
    HAS_BACKTEST = True
except ImportError:
    HAS_BACKTEST = False

# ═══════════════════════════════════════════════════════════════════
#  v5.4 MODULE STATE
# ═══════════════════════════════════════════════════════════════════
_V54_CONFIG = {}  # Global v5.4 config set by main()

# ═══════════════════════════════════════════════════════════════════
#  COLOURS
# ═══════════════════════════════════════════════════════════════════
RESET="\033[0m"; BOLD="\033[1m"
GREEN="\033[92m"; YELLOW="\033[93m"; RED="\033[91m"
CYAN="\033[96m"; GRAY="\033[90m"; BLUE="\033[94m"; MAGENTA="\033[95m"

def C(t,c): return f"{c}{t}{RESET}"
def B(t):   return f"{BOLD}{t}{RESET}"
def hdr(txt,w=66):
    print(); print(C("═"*w,CYAN))
    print(C(f"   {txt}",CYAN+BOLD))
    print(C("═"*w,CYAN)); print()

STATE_FILE   = Path("options_state.json")
ARCHIVE_DIR  = Path("trade_archive")

# ═══════════════════════════════════════════════════════════════════
#  STATE
# ═══════════════════════════════════════════════════════════════════
def empty_state():
    return {
        "symbol":"^NSEI","vix_symbol":"^INDIAVIX","hv_window":20,
        "atm_iv":None,"hv":None,"ratio":None,"iv_rank":None,
        "vix_current":None,"vix_low52":None,"vix_high52":None,"zone_label":None,
        "strategy":None,
        "phase":"ANALYZE",   # ANALYZE | SETUP | LIVE | CLOSED
        # shared position fields
        "legs":{},           # key→{strike,type,action,lots,entry_premium,current_premium}
        "adjustments":[],
        "roll_count":{"call":0,"put":0},  # IC: max 1 roll per side
        "net_credit":0.0,
        "upper_be":None,"lower_be":None,
        "short_call":None,"short_put":None,   # IC short strikes for proximity check
        "entry_spot":None,"current_spot":None,
        "log":[],"pnl_snapshots":[],"entry_time":None,"lot_size":75,
    }

def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f: s=json.load(f)
            if s.get("phase") in ("LIVE","SETUP"):
                print(C(f"  ▶  Resuming — strategy={s.get('strategy')} phase={s.get('phase')}",YELLOW))
            return s
        except Exception: pass
    return empty_state()

def save_state(s):
    with open(STATE_FILE,"w") as f: json.dump(s,f,indent=2,default=str)

def log_event(s,msg):
    ts=datetime.now().strftime("%H:%M:%S")
    s["log"].append(f"[{ts}] {msg}")
    print(C(f"  LOG [{ts}] {msg}",GRAY))


# ═══════════════════════════════════════════════════════════════════
#  MARKET DATA
# ═══════════════════════════════════════════════════════════════════
_spot_cache={"v":None,"ts":0}

def fetch_spot(sym="^NSEI"):
    now=time.time()
    if now-_spot_cache["ts"]<30 and _spot_cache["v"]: return _spot_cache["v"]
    try:
        h=yf.Ticker(sym).history(period="1d",interval="1m")
        if not h.empty:
            v=round(float(h["Close"].iloc[-1]),2)
            _spot_cache.update({"v":v,"ts":now}); return v
    except Exception: pass
    return None

def fetch_15min_candles(sym="^NSEI",n=4):
    try:
        h=yf.Ticker(sym).history(period="5d",interval="15m")
        if not h.empty: return list(h["Close"].tail(n+1).round(2))
    except Exception: pass
    return []

def fetch_price_history(sym,days=120):
    end=datetime.today(); start=end-timedelta(days=days)
    h=yf.Ticker(sym).history(start=start.strftime("%Y-%m-%d"),end=end.strftime("%Y-%m-%d"),interval="1d")
    if h.empty: raise ValueError(f"No data for {sym}")
    return h["Close"]

def fetch_vix_data(sym="^INDIAVIX"):
    h=yf.Ticker(sym).history(period="1y",interval="1d")
    if h.empty: return None,None,None
    return round(float(h["Close"].iloc[-1]),2),round(float(h["Close"].min()),2),round(float(h["Close"].max()),2)

def historical_volatility(closes,window=20):
    if len(closes)<window+1: raise ValueError(f"Need {window+1} closes")
    lr=np.log(closes[-(window+1):]/closes[-(window+1):].shift(1)).dropna()
    return round(float(lr.std()*math.sqrt(252)*100),2)

def calc_iv_rank(cur,lo,hi):
    if hi==lo: return 50.0
    return round((cur-lo)/(hi-lo)*100,1)

# ── live premium fetch (Black-Scholes approximation via ATM vol) ──
def fetch_live_premium(strike, spot, opt_type, atm_iv_pct, dte_days=3):
    """
    Estimate current option premium using simplified Black-Scholes.
    atm_iv_pct: implied vol as percentage (e.g. 17.5)
    Returns estimated premium in points.
    """
    try:
        S = spot; K = strike
        T = max(dte_days / 365.0, 1/365.0)
        sigma = atm_iv_pct / 100.0
        r = 0.065   # India risk-free rate approx

        from math import log, sqrt, exp
        from statistics import NormalDist
        nd = NormalDist()

        d1 = (log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*sqrt(T))
        d2 = d1 - sigma*sqrt(T)

        if opt_type == "CE":
            price = S*nd.cdf(d1) - K*exp(-r*T)*nd.cdf(d2)
        else:
            price = K*exp(-r*T)*nd.cdf(-d2) - S*nd.cdf(-d1)

        return max(round(price, 2), 0.05)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════
#  IV/HV ANALYZER  (Step 1)
# ═══════════════════════════════════════════════════════════════════
def run_iv_hv_analysis(s):
    hdr("STEP 1 — IV / HV ANALYZER  (Live Data)")
    sym=s.get("symbol","^NSEI"); vsym=s.get("vix_symbol","^INDIAVIX"); hw=s.get("hv_window",20)

    print(C(f"  Fetching price history for {sym}…",GRAY))
    try:
        closes=fetch_price_history(sym); hv=historical_volatility(closes,hw)
    except Exception as e:
        print(C(f"  ✗ HV fetch failed: {e}",RED)); return None

    print(C(f"  Fetching VIX data ({vsym})…",GRAY))
    try:
        vc,vlo,vhi=fetch_vix_data(vsym)
        if vc is None: raise ValueError
    except Exception:
        print(C("  ⚠  VIX fetch failed — using HV proxy.",YELLOW))
        vc=hv; vlo=hv*0.6; vhi=hv*1.8

    print()
    spot=fetch_spot(sym)
    if spot: print(f"  Live {sym} Spot : {C(B(str(spot)),GREEN)}")
    print()

    try:
        atm_iv=float(input(C("  Enter ATM IV % from your broker's option chain\n  (e.g. 17.7) : ",CYAN)).strip())
    except (ValueError,EOFError):
        print(C("  ✗ Invalid.",RED)); return None

    ratio=round(atm_iv/hv,3) if hv else 0
    rank=calc_iv_rank(vc,vlo,vhi)
    s.update({"atm_iv":atm_iv,"hv":hv,"ratio":ratio,"iv_rank":rank,
              "vix_current":vc,"vix_low52":vlo,"vix_high52":vhi,"current_spot":spot})

    zone=build_zone(ratio,rank)
    c=zone["color"]

    print()
    print(C("  ── Volatility Metrics ─────────────────────────────",CYAN))
    print(f"  ATM IV (manual)     : {C(f'{atm_iv:.2f}%',YELLOW)}")
    print(f"  Historical Vol (HV) : {C(f'{hv:.2f}%',YELLOW)}  ({hw}-day annualised)")
    print(f"  VIX / India VIX     : {C(str(vc),YELLOW)}")
    print(f"  52-week VIX range   : {vlo}  →  {vhi}")
    print()
    print(C("  ── Key Outputs ────────────────────────────────────",CYAN))
    print(f"  IV / HV Ratio       : {C(B(str(ratio)),c)}")
    print(f"  IV Rank             : {C(B(f'{rank:.1f}%'),c)}")
    print()
    print(C("  ── Strategy Signal ────────────────────────────────",CYAN))
    print(f"  Zone                : {C(B(zone['label']),c)}")
    print(f"  Strategies to use:")
    for i,st in enumerate(zone["strategies_display"],1):
        tag = C("  ● LIVE",GREEN) if st["key"] in IMPLEMENTED else C("  ○ Need to Enter - Rule Book",YELLOW)
        print(f"    {C('▸',c)} [{i}] {st['name']}{tag}")
    print(f"  Note:")
    words=zone["note"].split(); line=[]; lines=[]
    for w in words:
        if sum(len(x)+1 for x in line)+len(w)>55: lines.append(" ".join(line)); line=[w]
        else: line.append(w)
    if line: lines.append(" ".join(line))
    for l in lines: print(f"    {l}")
    print()
    print(C("═"*52,CYAN))
    print(C("  SUMMARY",BOLD))
    print(f"  IV/HV Ratio    = {C(B(str(ratio)),c)}")
    print(f"  IV Rank        = {C(B(f'{rank:.1f}%'),c)}")
    print(f"  Strategy zone  = {C(B(zone['label']),c)}")
    print(C("═"*52,CYAN)); print()

    s["zone_label"]=zone["label"]
    save_state(s)
    return zone


# ═══════════════════════════════════════════════════════════════════
#  ZONE BUILDER
# ═══════════════════════════════════════════════════════════════════
def _s(key,name,desc,impl=False): return {"key":key,"name":name,"desc":desc,"implemented":impl}

def build_zone(ratio,rank):
    if ratio>1.4:
        if rank>=50:
            return {"label":"🔴  Extreme Premium — Aggressive Sell (HIGH CONVICTION)","color":RED,
                    "note":f"IV/HV={ratio} & IV Rank={rank}% both confirm overpriced premiums. Maximum theta edge. Wings non-negotiable.",
                    "strategies_display":[
                        _s("ironfly","Iron Fly (short ATM straddle + OTM wings)","ATM straddle + hedge wings",True),
                        _s("ironcondor","Iron Condor with tight wings","OTM call spread + OTM put spread",True),
                        _s("bwb","Broken Wing Butterfly (BWB)","Asymmetric 1:2:1 — net credit + directional bias",True),
                    _s("butterfly","Butterfly / Batman (single-side, 3-leg)","Sell 2 ATM + buy OTM + buy ITM",True),
                        _s("bwb","Broken Wing Butterfly (BWB)","Asymmetric 1:2:1 — net credit + directional bias",True),
                    _s("strangle","Short Strangle (Premium Matching)","Sell OTM Call + Put, equal premiums",True),
                    _s("calendar","Calendar Spread (VIX-Based)","Near/far expiry, theta + vega play, all VIX zones",True),
                    ]}
        else:
            return {"label":"🔴  High Premium — Sell (ratio-driven, rank moderate)","color":RED,
                    "note":f"IV/HV={ratio} strongly signals overpriced options. IV Rank={rank}% moderate. Sell at normal size.",
                    "strategies_display":[
                        _s("ironfly","Iron Fly (short ATM straddle + OTM wings)","ATM straddle + hedge wings",True),
                        _s("ironcondor","Iron Condor — slightly wider wings","OTM call spread + OTM put spread",True),
                        _s("bwb","Broken Wing Butterfly (BWB)","Asymmetric 1:2:1 — net credit + directional bias",True),
                    _s("butterfly","Butterfly / Batman (single-side, 3-leg)","Sell 2 ATM + buy OTM + buy ITM",True),
                    ]}
    if 1.2<=ratio<=1.4:
        conv="high" if rank>=50 else "moderate"
        return {"label":f"🟡  Elevated Premium — Lean Short Vol ({conv} conviction)","color":YELLOW,
                "note":f"IV/HV={ratio} shows premiums above historical norm. IV Rank={rank}%. Good edge for sellers — use defined-risk spreads and do not oversize.",
                "strategies_display":[
                    _s("ironcondor","Iron Condor (wider wings than aggressive zone)","OTM call spread + OTM put spread",True),
                    _s("credit_spread","Credit Put Spread (Bullish/Neutral)","Sell OTM put, buy lower put",True),
                    _s("credit_spread","Credit Call Spread (Bearish/Neutral)","Sell OTM call, buy higher call",True),
                    _s("ironfly","Iron Fly (if conviction is high)","ATM straddle + hedge wings",True),
                ]}
    if 1.0<=ratio<1.2:
        return {"label":"⚪  Neutral — No Statistical Edge","color":GRAY,
                "note":f"IV/HV={ratio} — premiums near fair value. No edge for sellers or buyers. Best action: stay flat.",
                "strategies_display":[
                    _s("debit_spread","Vertical Debit Spread (only with strong directional view)","Buy ATM, sell OTM"),
                    _s("none","Stay flat — best action is no action","Wait for ratio to move"),
                ]}
    if 0.9<=ratio<1.0:
        return {"label":"🔵  Cheap Premium — Lean Long Vol","color":BLUE,
                "note":f"IV/HV={ratio} — options below historical norm. Sellers have no cushion.",
                "strategies_display":[
                    _s("debit_spread","Directional Debit Spread (call or put per view)","Buy ATM, sell OTM"),
                    _s("long_options","Long Calls or Long Puts","Outright directional buy"),
                ]}
    conv="HIGH CONVICTION" if rank<30 else "moderate conviction"
    return {"label":f"🟢  Very Cheap Premium — Aggressive Buy ({conv})","color":GREEN,
            "note":f"IV/HV={ratio}, IV Rank={rank}%. Options historically cheap. Buyers have strong R/R.",
            "strategies_display":[
                _s("long_options","Long Calls or Long Puts (directional)","Outright buy"),
                _s("debit_spread","Debit Straddle or Debit Strangle","Buy ATM call + put"),
                _s("calendar","Calendar Spread (buy back-month, sell front)","Vega long"),
            ]}


# ═══════════════════════════════════════════════════════════════════
#  EXPIRY SELECTION  (defined early — used by all strategy setups)
# ═══════════════════════════════════════════════════════════════════
def _fetch_nifty_expiries_live():
    """
    Fetch live Nifty expiry dates from Yahoo Finance option chain.
    Falls back to generating Tuesdays if fetch fails.
    Nifty weekly expiry = every Tuesday.
    Monthly expiry = last Tuesday of the month.
    """
    import calendar
    today = datetime.now().date()
    expiries = []

    # Try Yahoo Finance live option chain
    try:
        ticker = yf.Ticker("^NSEI")
        raw = ticker.options          # tuple of date strings "YYYY-MM-DD"
        if raw:
            for ds in raw[:12]:
                try:
                    dt = datetime.strptime(ds, "%Y-%m-%d").date()
                    if dt >= today:
                        dte = (dt - today).days
                        # check if last Tuesday of month
                        last_tue = max(
                            week[1] for week in calendar.monthcalendar(dt.year, dt.month)
                            if week[1] != 0
                        )
                        lbl = "Monthly" if dt.day == last_tue else "Weekly"
                        expiries.append((dt, dte, lbl))
                except: pass
        if expiries:
            return expiries[:8]
    except Exception:
        pass

    # Fallback: generate next 8 Tuesdays (Nifty weekly = Tuesday)
    d = today
    while len(expiries) < 8:
        d += timedelta(days=1)
        if d.weekday() == 1:   # Tuesday = 1
            dte = (d - today).days
            last_tue = max(
                week[1] for week in calendar.monthcalendar(d.year, d.month)
                if week[1] != 0
            )
            lbl = "Monthly" if d.day == last_tue else "Weekly"
            expiries.append((d, dte, lbl))
    return expiries

def select_expiry():
    """
    Show available Nifty expiry dates (live from Yahoo or generated Tuesdays).
    Returns (date_string, dte_int).
    """
    print()
    print(C("  Fetching Nifty expiry dates…", GRAY))
    expiries = _fetch_nifty_expiries_live()

    print(C("  ── Select Nifty Expiry (Weekly=Tuesday, Monthly=Last Tuesday) ──", CYAN))
    print(f"  {'#':<4} {'Date':<14} {'DTE':<8} {'Type':<10} {'Day'}")
    print(f"  {'-'*52}")
    for i, (dt, dte, lbl) in enumerate(expiries, 1):
        tag     = C("Monthly", MAGENTA) if lbl == "Monthly" else C("Weekly ", CYAN)
        dte_col = GREEN if dte <= 4 else YELLOW if dte <= 10 else GRAY
        day_str = dt.strftime("%A")
        print(f"  {i:<4} {dt.strftime('%Y-%m-%d'):<14} {C(str(dte)+' days', dte_col):<18} {tag:<18} {C(day_str, GRAY)}")
    print()
    try:
        ch = int(input(C("  Select expiry (1-8) [1=nearest]: ", CYAN)).strip() or "1")
        if ch < 1 or ch > len(expiries): ch = 1
    except: ch = 1

    chosen_dt, chosen_dte, chosen_lbl = expiries[ch-1]
    print(C(f"  ✅  Expiry: {chosen_dt}  ({chosen_dte} DTE — {chosen_lbl}  {chosen_dt.strftime('%A')})", GREEN))
    return str(chosen_dt), chosen_dte

IMPLEMENTED={"ironfly","ironcondor","butterfly","credit_spread","bwb","strangle","calendar","debit_spread","long_options","debit_straddle"}


# ═══════════════════════════════════════════════════════════════════
#  STRATEGY SELECTION  (Step 2)
# ═══════════════════════════════════════════════════════════════════
def strategy_selection(s,zone,v54_config=None):
    strats=zone["strategies_display"]; c=zone["color"]
    print(C("  ── Select a Strategy ──────────────────────────────",CYAN)); print()
    for i,st in enumerate(strats,1):
        if st["key"] in IMPLEMENTED: tag=C("  ● LIVE",GREEN)
        elif st["key"]=="none":      tag=C("  ─ No action",GRAY)
        else:                        tag=C("  ○ Need to Enter - Rule Book",YELLOW)
        print(f"  {C(f'[{i}]',c)} {st['name']}{tag}")
    print(); print(f"  {C('[0]',GRAY)} Re-run analysis"); print()

    try:
        choice=int(input(C("  Enter number to select: ",CYAN)).strip())
    except (ValueError,EOFError):
        print(C("  ✗ Invalid.",RED)); return

    if choice==0:
        s["phase"]="ANALYZE"; save_state(s); return
    if choice<1 or choice>len(strats):
        print(C("  ✗ Out of range.",RED)); return

    sel=strats[choice-1]
    if sel["key"]=="none":
        print(C("\n  ✅  Staying flat today.",GRAY)); return
    if sel["key"] not in IMPLEMENTED:
        name=sel["name"].split("(")[0].strip()
        print()
        print(C("  ╔══════════════════════════════════════════════════════╗",YELLOW))
        print(C("  ║                                                      ║",YELLOW))
        print(C("  ║   Need to Enter - Rule Book                          ║",YELLOW+BOLD))
        print(C("  ║                                                      ║",YELLOW))
        print(C(f"  ║   Strategy : {name:<40}║",YELLOW))
        print(C("  ║   Add its complete Rule Book to enable this.         ║",YELLOW))
        print(C("  ║                                                      ║",YELLOW))
        print(C("  ╚══════════════════════════════════════════════════════╝",YELLOW)); print()
        if input(C("  Choose different? (y/n): ",CYAN)).strip().lower()=="y":
            strategy_selection(s,zone,v54_config)
        return

    s["strategy"]=sel["key"]; s["phase"]="SETUP"
    log_event(s,f"Strategy selected: {sel['name']}"); save_state(s)
    if sel["key"]=="ironfly":         ironfly_setup(s)
    elif sel["key"]=="ironcondor":    ironcondor_setup(s)
    elif sel["key"]=="butterfly":     butterfly_setup(s)
    elif sel["key"]=="credit_spread": credit_spread_setup(s)
    elif sel["key"]=="bwb":           bwb_setup(s)
    elif sel["key"]=="strangle":      strangle_setup(s)
    elif sel["key"]=="calendar":      calendar_setup(s)
    elif sel["key"]=="debit_spread":  debit_spread_setup(s)
    elif sel["key"]=="long_options":  long_options_setup(s)
    elif sel["key"]=="debit_straddle": debit_straddle_setup(s)


# ═══════════════════════════════════════════════════════════════════
#  LIVE P&L  — fetch current premiums via Black-Scholes
# ═══════════════════════════════════════════════════════════════════
def update_live_premiums(s, spot):
    """
    For every open leg, estimate current premium using BS approximation
    and store in leg["current_premium"].  Also returns total live P&L per lot.
    """
    atm_iv  = s.get("atm_iv", 17.0)
    entry   = s.get("entry_time")
    
    # ── Calculate current DTE correctly ──────────────────────────────
    # Use actual dte_at_entry (e.g., 5 for 16-Jun, 12 for 23-Jun)
    dte_at_entry = s.get("dte_at_entry", 5)  # DTE when trade was entered
    dte = 3   # fallback default

    if entry:
        try:
            elapsed = (datetime.now() - datetime.fromisoformat(entry)).days
            dte = max(1, dte_at_entry - elapsed)  # Current DTE = entry DTE - days passed
        except Exception:
            dte = max(1, dte_at_entry - 1)  # fallback: reduce by 1 day

    pnl = 0.0
    for leg in s["legs"].values():
        if leg is None: continue
        leg_exp = _leg_expiry(s, leg)
        leg_dte = _leg_dte(leg_exp, dte)
        live = fetch_live_premium(leg["strike"], spot, leg["type"], atm_iv, leg_dte)
        if live is not None:
            leg["current_premium"] = live
        val = leg["current_premium"]
        pnl += (leg["entry_premium"]-val) if leg["action"]=="sell" else (val-leg["entry_premium"])

    for adj in s["adjustments"]:
        if adj.get("closed"): continue
        adj_exp = adj.get("expiry") or s.get("expiry_date", "")
        adj_dte = _leg_dte(adj_exp, dte)
        live = fetch_live_premium(adj["strike"], spot, adj["type"], atm_iv, adj_dte)
        if live is not None:
            adj["current_premium"] = live
        val = adj.get("current_premium", adj["entry_premium"])
        pnl += (adj["entry_premium"]-val) if adj["action"]=="sell" else (val-adj["entry_premium"])

    return round(pnl, 2)

def option_intrinsic(strike,spot,opt_type):
    return max(0.0,spot-strike) if opt_type=="CE" else max(0.0,strike-spot)

def _format_expiry(exp_str):
    if not exp_str:
        return "—"
    try:
        return datetime.strptime(exp_str, "%Y-%m-%d").strftime("%d%b%Y").upper()
    except Exception:
        return exp_str

def _leg_dte(exp_str, fallback=3):
    if not exp_str:
        return fallback
    try:
        return max(1, (datetime.strptime(exp_str, "%Y-%m-%d").date() - datetime.now().date()).days)
    except Exception:
        return fallback

def _leg_expiry(s, leg):
    if leg.get("expiry"):
        return leg["expiry"]
    if s.get("strategy") == "calendar":
        strike = leg.get("strike")
        if leg.get("action") == "buy" and strike in (s.get("cal_far_call"), s.get("cal_far_put")):
            return s.get("cal_far_expiry") or s.get("expiry_date", "")
    return s.get("expiry_date", "")


# ═══════════════════════════════════════════════════════════════════
#  POSITION SUMMARY  (shared)
# ═══════════════════════════════════════════════════════════════════
def show_position_summary(s):
    spot=s.get("current_spot") or s.get("entry_spot",0)
    pnl =update_live_premiums(s,spot)
    lots=1
    for leg in s["legs"].values():
        if leg: lots=leg.get("lots",1); break
    lsize=s["lot_size"]

    strat_label = {"ironfly":"Iron Fly","ironcondor":"Iron Condor","butterfly":"Butterfly",
                   "credit_spread":"Credit Spread","bwb":"BWB","calendar":"Calendar Spread"}.get(s.get("strategy",""),"Position")
    is_cal = s.get("strategy") == "calendar"
    print()
    print(C(f"  ── {strat_label} Position Summary ──────────────────────",CYAN))
    if is_cal:
        print(f"  {'Leg':<20} {'Expiry':<10} {'Strike':<8} {'Act':<6} {'Entry':>8}  {'Live':>8}  {'P&L':>8}")
        print(f"  {'-'*72}")
    else:
        print(f"  {'Leg':<22} {'Strike':<8} {'Act':<6} {'Entry':>8}  {'Live':>8}  {'P&L':>8}")
        print(f"  {'-'*64}")

    for key,leg in s["legs"].items():
        if leg:
            ac=RED if leg["action"]=="sell" else GREEN
            leg_pnl=(leg["entry_premium"]-leg["current_premium"]) if leg["action"]=="sell" else (leg["current_premium"]-leg["entry_premium"])
            pc=GREEN if leg_pnl>=0 else RED
            label=key.replace("_"," ").title()
            exp_fmt = _format_expiry(_leg_expiry(s, leg))
            if is_cal:
                print(f"  {label:<20} {exp_fmt:<10} {leg['strike']:<8} "
                      f"{C(leg['action'].upper()[:4],ac):<15} "
                      f"{leg['entry_premium']:>8.1f}  {leg['current_premium']:>8.1f}  "
                      f"{C(f'{leg_pnl:+.1f}',pc):>16}")
            else:
                print(f"  {label:<22} {leg['strike']:<8} "
                      f"{C(leg['action'].upper()[:4],ac):<15} "
                      f"{leg['entry_premium']:>8.1f}  {leg['current_premium']:>8.1f}  "
                      f"{C(f'{leg_pnl:+.1f}',pc):>16}")

    for i,adj in enumerate(s["adjustments"],1):
        if not adj.get("closed"):
            ac=RED if adj["action"]=="sell" else GREEN
            adj_pnl=(adj["entry_premium"]-adj.get("current_premium",adj["entry_premium"])) if adj["action"]=="sell" else (adj.get("current_premium",adj["entry_premium"])-adj["entry_premium"])
            pc=GREEN if adj_pnl>=0 else RED
            print(f"  {'Adj'+str(i)+' '+adj['type']:<22} {adj['strike']:<8} "
                  f"{C(adj['action'].upper()[:4],ac):<15} "
                  f"{adj['entry_premium']:>8.1f}  {adj.get('current_premium',adj['entry_premium']):>8.1f}  "
                  f"{C(f'{adj_pnl:+.1f}',pc):>16}")

    print()
    nc=s["net_credit"]
    pnl_c=GREEN if pnl>=0 else RED
    pct=round(pnl/nc*100,1) if nc else 0
    print(f"  Net credit collected : {C(f'Rs {nc:.0f}',GREEN)}")
    print(f"  Upper BE             : {C(str(s['upper_be']),YELLOW)}")
    print(f"  Lower BE             : {C(str(s['lower_be']),YELLOW)}")
    exp_str=s.get("expiry_date","")
    dte_ps="?" 
    if exp_str:
        try: dte_ps=(datetime.strptime(exp_str,"%Y-%m-%d").date()-datetime.now().date()).days
        except: pass
    print(f"  Current Spot         : {C(str(spot),CYAN)}")
    print(f"  Expiry               : {C(str(exp_str) if exp_str else 'Not set',MAGENTA)}  ({C(str(dte_ps)+' DTE',YELLOW if isinstance(dte_ps,int) and dte_ps<=3 else GRAY)})")
    total_rs = round(pnl*lots*lsize,0)
    print(f"  Live P&L / lot       : {C(f'Rs {pnl:.0f}  ({pct:+.1f}% of credit)',pnl_c)}")
    print(f"  Live P&L TOTAL       : {C(f'Rs {total_rs:.0f}  ({lots} lot x {lsize})',pnl_c+BOLD)}")
    print(f"  Max profit target    : {C(f'Rs {nc*0.65:.0f}  (65% of credit)',CYAN)}")
    print(f"  Hard stop loss       : {C(f'Rs {-2*nc:.0f}  (2x credit)',RED)}")
    if s.get("ratio"):
        zone=build_zone(s["ratio"],s.get("iv_rank",50))
        print(f"  IV/HV / IV Rank      : {C(str(s['ratio']),zone['color'])} / {C(str(s['iv_rank'])+'%',zone['color'])}")
    print()
    return pnl


# ═══════════════════════════════════════════════════════════════════
#  PAYOFF CHART  (shared — works for both strategies)
# ═══════════════════════════════════════════════════════════════════
def draw_payoff_chart(s,filename=None):
    if not HAS_MPL: print(C("  ⚠  matplotlib not available.",YELLOW)); return
    strat=s.get("strategy","ironfly")
    if strat=="calendar":
        return draw_calendar_payoff_chart(s,filename)
    if filename is None:
        filename=f"{strat}_payoff.png"

    # determine plot range
    strikes=[leg["strike"] for leg in s["legs"].values() if leg]
    if not strikes: return
    lo=min(strikes)-300; hi=max(strikes)+300
    xs=np.linspace(lo,hi,600)
    ys=[]
    for spot in xs:
        pnl=0.0
        for leg in s["legs"].values():
            if leg is None: continue
            intr=option_intrinsic(leg["strike"],spot,leg["type"])
            pnl+=(leg["entry_premium"]-intr) if leg["action"]=="sell" else (intr-leg["entry_premium"])
        for adj in s["adjustments"]:
            if adj.get("closed"): continue
            intr=option_intrinsic(adj["strike"],spot,adj["type"])
            pnl+=(adj["entry_premium"]-intr) if adj["action"]=="sell" else (intr-adj["entry_premium"])
        ys.append(pnl)
    ys=np.array(ys)

    fig,ax=plt.subplots(figsize=(12,5))
    fig.patch.set_facecolor("#1a1a2e"); ax.set_facecolor("#16213e")
    ax.fill_between(xs,ys,0,where=ys>=0,color="#00c896",alpha=0.3,label="Profit zone")
    ax.fill_between(xs,ys,0,where=ys<0, color="#ff4757",alpha=0.3,label="Loss zone")
    ax.plot(xs,ys,color="#00c896",linewidth=2)
    ax.axhline(0,color="#ffffff",linewidth=0.5,linestyle="--",alpha=0.4)

    # ── Safe breakeven line plotting with validation ──────────────
    for be,lbl in [(s.get("upper_be"),"Upper BE"),(s.get("lower_be"),"Lower BE")]:
        if be is None:
            continue  # Skip if BE not set (e.g., calendar spreads)
        try:
            ax.axvline(be,color="#ffd700",linewidth=1.2,linestyle="--",alpha=0.9)
            ypos=max(ys)*0.8 if max(ys)>0 else 10
            ax.text(be,ypos,f"{lbl}\n{be:.0f}",color="#ffd700",fontsize=8,ha="center")
        except Exception:
            pass  # Skip plotting if BE is invalid

    if s.get("current_spot"):
        sp=s["current_spot"]
        ax.axvline(sp,color="#74b9ff",linewidth=2,alpha=0.9)
        ypos=min(ys)*0.5 if min(ys)<0 else -5
        ax.text(sp,ypos,f"Spot\n{sp:.0f}",color="#74b9ff",fontsize=8,ha="center")

    # profit target & stop lines
    nc=s["net_credit"]
    ax.axhline(nc*0.65,color="#00c896",linewidth=0.8,linestyle=":",alpha=0.7)
    ax.axhline(-2*nc,  color="#ff4757",linewidth=0.8,linestyle=":",alpha=0.7)
    ax.text(lo+50,nc*0.65+2,"65% target",color="#00c896",fontsize=7)
    ax.text(lo+50,-2*nc-5,"Stop 2x",color="#ff4757",fontsize=7)

    for leg in s["legs"].values():
        if leg:
            col="#ff6b6b" if leg["action"]=="sell" else "#a29bfe"
            ax.axvline(leg["strike"],color=col,linewidth=0.7,linestyle=":",alpha=0.5)
    for adj in s["adjustments"]:
        if not adj.get("closed"):
            ax.axvline(adj["strike"],color="#fd79a8",linewidth=0.7,linestyle="-.",alpha=0.6)

    if s.get("ratio"):
        zone=build_zone(s["ratio"],s.get("iv_rank",50))
        ax.annotate(f"IV/HV={s['ratio']}  IVR={s.get('iv_rank','-')}%\n{zone['label']}",
                    xy=(0.01,0.97),xycoords="axes fraction",fontsize=7,color="#cccccc",
                    verticalalignment="top",
                    bbox=dict(boxstyle="round,pad=0.3",facecolor="#0f3460",alpha=0.8))

    strat_label="Iron Fly" if strat=="ironfly" else "Iron Condor"
    adj_n=len([a for a in s["adjustments"] if not a.get("closed")])
    ax.set_title(f"{strat_label}  |  {adj_n} active adj  |  Live P&L updated  |  {datetime.now().strftime('%H:%M:%S')}",
                 color="#ffffff",fontsize=11)
    ax.set_xlabel("Nifty Spot",color="#aaaaaa"); ax.set_ylabel("P&L per lot (Rs)",color="#aaaaaa")
    ax.tick_params(colors="#aaaaaa")
    for sp2 in ax.spines.values(): sp2.set_edgecolor("#333333")
    ax.legend(facecolor="#1a1a2e",labelcolor="#cccccc",fontsize=9)
    plt.tight_layout()
    plt.savefig(filename,dpi=120,bbox_inches="tight",facecolor=fig.get_facecolor())
    plt.close()
    print(C(f"  📊 Payoff chart saved → {filename}",CYAN))


def draw_calendar_payoff_chart(s, filename=None):
    """
    Calendar Spread Payoff — CORRECTED tent shape.
    Profit: centered at sold strike, bounded by wings.
    Max loss: spread width - net premium (on far sides).
    """
    if not HAS_MPL:
        print(C("  ⚠  matplotlib not available.", YELLOW))
        return

    if filename is None:
        filename = "calendar_payoff.png"

    near_call = s.get("cal_near_call", 0)
    near_put = s.get("cal_near_put", 0)
    far_call = s.get("cal_far_call", 0)
    far_put = s.get("cal_far_put", 0)
    net_prem = s.get("cal_net_premium", 0)

    if near_call == 0 or near_put == 0:
        return

    lo = min(far_put, near_put) - 300
    hi = max(far_call, near_call) + 300
    xs = np.linspace(lo, hi, 600)
    ys = []

    for spot_price in xs:
        call_pnl = 0
        if spot_price >= near_call:
            call_pnl = -(spot_price - near_call)
        else:
            dist_from_call = near_call - spot_price
            call_pnl = min(net_prem * 0.5, dist_from_call * 0.15)

        put_pnl = 0
        if spot_price <= near_put:
            put_pnl = -(near_put - spot_price)
        else:
            dist_from_put = spot_price - near_put
            put_pnl = min(net_prem * 0.5, dist_from_put * 0.15)

        total_pnl = call_pnl + put_pnl
        ys.append(total_pnl)

    ys = np.array(ys)

    fig, ax = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    profit_zone = ys >= 0
    ax.fill_between(xs, ys, 0, where=profit_zone, color="#00c896", alpha=0.3, label="Profit zone")
    ax.fill_between(xs, ys, 0, where=~profit_zone, color="#ff4757", alpha=0.3, label="Loss zone")
    ax.plot(xs, ys, color="#00c896", linewidth=2.5, label="Calendar P&L")
    ax.axhline(0, color="#ffffff", linewidth=0.5, linestyle="--", alpha=0.4)

    ax.axvline(near_call, color="#ff6b6b", linewidth=1.5, linestyle="--", alpha=0.7)
    ax.axvline(near_put, color="#a29bfe", linewidth=1.5, linestyle="--", alpha=0.7)
    ax.text(near_call, max(ys) * 0.85, f"Sold Call\n{near_call}", color="#ff6b6b", fontsize=8, ha="center")
    ax.text(near_put, max(ys) * 0.85, f"Sold Put\n{near_put}", color="#a29bfe", fontsize=8, ha="center")

    ax.axvline(far_call, color="#fd79a8", linewidth=1, linestyle=":", alpha=0.5)
    ax.axvline(far_put, color="#fd79a8", linewidth=1, linestyle=":", alpha=0.5)
    ax.text(far_call, min(ys) * 0.7, f"Buy Call\n{far_call}", color="#fd79a8", fontsize=7, ha="center")
    ax.text(far_put, min(ys) * 0.7, f"Buy Put\n{far_put}", color="#fd79a8", fontsize=7, ha="center")

    if s.get("current_spot"):
        sp = s["current_spot"]
        ax.axvline(sp, color="#74b9ff", linewidth=2, alpha=0.9)
        ypos_spot = max(ys) * 0.5
        ax.text(sp, ypos_spot, f"Spot\n{sp:.0f}", color="#74b9ff", fontsize=9, ha="center")

    if net_prem > 0:
        profit_target = net_prem * 0.45
        ax.axhline(profit_target, color="#00c896", linewidth=0.8, linestyle=":", alpha=0.7)
        ax.text(lo + 100, profit_target + 10,
                f"Exit target 45-65% ({profit_target:.0f})", color="#00c896", fontsize=7)

    ax.axhline(-abs(net_prem), color="#ff4757", linewidth=0.8, linestyle=":", alpha=0.7)
    ax.text(lo + 100, -abs(net_prem) - 15,
            f"Hard stop loss (100% Rs{abs(net_prem):.0f})", color="#ff4757", fontsize=7)

    cal_type = s.get("cal_type", "CALENDAR")
    vix_zone = s.get("cal_vix_zone", "AVERAGE")
    adj_n = len([a for a in s.get("adjustments", []) if not a.get("closed")])
    dte_near = s.get("dte_at_entry", 7)

    ax.set_title(
        f"CALENDAR SPREAD ({cal_type})  |  VIX: {vix_zone}  |  "
        f"Near {dte_near}DTE  |  {adj_n} adj  |  {datetime.now().strftime('%H:%M:%S')}",
        color="#ffffff", fontsize=11, fontweight="bold"
    )
    ax.set_xlabel("Nifty Spot Price", color="#aaaaaa")
    ax.set_ylabel("P&L per lot (Rs)", color="#aaaaaa")
    ax.tick_params(colors="#aaaaaa")

    for sp2 in ax.spines.values():
        sp2.set_edgecolor("#333333")

    ax.legend(loc="upper right", facecolor="#1a1a2e", labelcolor="#cccccc", fontsize=9)
    ax.grid(True, alpha=0.15, color="#ffffff")

    plt.tight_layout()
    plt.savefig(filename, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(C(f"  📊 Calendar payoff chart saved → {filename}", CYAN))


# ═══════════════════════════════════════════════════════════════════
#  IRON FLY — SETUP
# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
#  EXPIRY SELECTION  (shared utility — called from all setups)
# ═══════════════════════════════════════════════════════════════════
def ironfly_setup(s):
    hdr("IRON FLY — SETUP")
    spot=s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI"))
    if spot:
        sugg=int(round(spot/50)*50)
        print(f"  Live Spot : {C(B(str(spot)),GREEN)}   Suggested ATM : {C(str(sugg),YELLOW)}")
    else: sugg=None
    print()

    try:
        raw=input(C(f"  ATM strike to SELL CE + PE [{sugg or 'e.g. 24000'}]: ",CYAN)).strip()
        atm=int(raw) if raw else sugg
    except: print(C("  ✗ Invalid.",RED)); return

    print(); print(C("  Wing Width Guide:",GRAY))
    for d,w in [("1-2 DTE","100-150"),("3-4 DTE","200-250"),("6 DTE (Thu→Tue)","300-400"),("7-8 DTE","400-500")]:
        print(f"    {d:<18} wing = {w}")
    print()
    try: wing=int(input(C("  Wing width [300]: ",CYAN)).strip() or "300")
    except: wing=300
    try: lots=int(input(C("  Lots [1]: ",CYAN)).strip() or "1")
    except: lots=1

    # SELECT EXPIRY FIRST (before asking for premiums!)
    expiry_date, dte = select_expiry()

    print()
    print(C(f"  ⚠️  IMPORTANT: Enter premiums for expiry: {C(expiry_date, YELLOW+BOLD)}", RED))
    print(C(f"  Use option chain prices from this expiry date only!", RED+BOLD))
    print()
    try:
        ce_p=float(input(C(f"  CE sell premium at {atm} CE (for {expiry_date}) : Rs ",CYAN)).strip())
        pe_p=float(input(C(f"  PE sell premium at {atm} PE (for {expiry_date}) : Rs ",CYAN)).strip())
    except: print(C("  ✗ Invalid.",RED)); return

    long_ce=atm+wing; long_pe=atm-wing
    tot=ce_p+pe_p; max_hc=round(tot*0.30,2)
    print()
    print(C(f"  Total credit : Rs {tot:.0f}   Max hedge cost (30%) : Rs {max_hc:.0f}",YELLOW))
    print(f"  Long CE hedge : {C(str(long_ce),CYAN)}   Long PE hedge : {C(str(long_pe),CYAN)}")
    print()
    if wing<150: print(C(f"  ⚠  Rule H3: wing {wing} < 150 min!",RED))
    elif wing>400: print(C(f"  ⚠  Rule H3: wing {wing} > 400 max.",YELLOW))

    try:
        lce_p=float(input(C(f"  LONG CE premium at {long_ce} : Rs ",CYAN)).strip())
        lpe_p=float(input(C(f"  LONG PE premium at {long_pe} : Rs ",CYAN)).strip())
    except: print(C("  ✗ Invalid.",RED)); return

    hc=lce_p+lpe_p
    if hc>max_hc:
        print(C(f"  ⚠  Hedge Rs{hc:.0f} > 30% rule Rs{max_hc:.0f}",YELLOW))
        if input(C("  Continue? (y/n): ",CYAN)).strip().lower()!="y": return

    nc=round(tot-hc,2); ube=round(atm+nc,2); lbe=round(atm-nc,2)
    s.update({"atm_strike":atm,"wing_width":wing,"net_credit":nc,
              "upper_be":ube,"lower_be":lbe,"entry_spot":spot or atm,
              "current_spot":spot or atm,"entry_time":datetime.now().isoformat(),
              "phase":"LIVE","roll_count":{"call":0,"put":0},"adjustments":[],"pnl_snapshots":[]})
    s["legs"]={"short_ce":{"strike":atm,"type":"CE","action":"sell","lots":lots,"entry_premium":ce_p,"current_premium":ce_p},
               "short_pe":{"strike":atm,"type":"PE","action":"sell","lots":lots,"entry_premium":pe_p,"current_premium":pe_p},
               "long_ce": {"strike":long_ce,"type":"CE","action":"buy","lots":lots,"entry_premium":lce_p,"current_premium":lce_p},
               "long_pe": {"strike":long_pe,"type":"PE","action":"buy","lots":lots,"entry_premium":lpe_p,"current_premium":lpe_p}}

    s["expiry_date"]=expiry_date; s["dte_at_entry"]=dte
    log_event(s,f"IronFly ATM={atm} wing={wing} credit=Rs{nc:.0f} BE={lbe}-{ube} expiry={expiry_date}")
    save_state(s)
    print(); print(C("═"*66,GREEN)); print(C("  ✅  IRON FLY ENTERED",GREEN+BOLD)); print(C("═"*66,GREEN))
    show_position_summary(s); draw_payoff_chart(s)
    print(C("  Starting live 15-min monitor… CTRL+C to save & exit.",CYAN))
    time.sleep(1); live_monitor_loop(s)


# ═══════════════════════════════════════════════════════════════════
#  IRON CONDOR — SETUP  (full rule-book from document)
# ═══════════════════════════════════════════════════════════════════
def ironcondor_setup(s):
    hdr("IRON CONDOR — SETUP  (Theta Gainers Rule Book)")

    spot=s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI"))
    vc=s.get("vix_current",17)

    if spot:
        print(f"  Live Spot   : {C(B(str(spot)),GREEN)}")
        print(f"  India VIX   : {C(str(vc),YELLOW)}")
        print()

        # ── Entry condition checks (Section 2) ───────────────────
        print(C("  ── Entry Condition Check (Section 2) ─────────────",CYAN))
        vix_ok = 13 <= vc <= 20
        print(f"  VIX 13-20   : {C('✅ OK',GREEN) if vix_ok else C(f'❌ VIX={vc} — outside safe range (13-20)',RED)}")
        if vc > 22:
            print(C("  ⛔  Rule: DO NOT ENTER — VIX > 22. Gap risk too high.",RED+BOLD))
            if input(C("  Override and continue? (y/n): ",CYAN)).strip().lower()!="y": return
        elif vc < 12:
            print(C("  ⚠  Rule: VIX < 12 — premium too low. Risk/reward unfavourable.",YELLOW))
            if input(C("  Continue anyway? (y/n): ",CYAN)).strip().lower()!="y": return

        # suggested short strikes (~2-2.5% OTM)
        sc_sugg=round(spot*1.022/50)*50   # ~2.2% above
        sp_sugg=round(spot*0.978/50)*50   # ~2.2% below
        # suggested long strikes (150-200 pts beyond short)
        lc_sugg=sc_sugg+200
        lp_sugg=sp_sugg-200

        print()
        print(C("  Strike Suggestion (Section 3 — 2-2.5% OTM):",CYAN))
        print(f"  Short Call  : ~{C(str(sc_sugg),YELLOW)}  ({round((sc_sugg-spot)/spot*100,1)}% above spot)")
        print(f"  Short Put   : ~{C(str(sp_sugg),YELLOW)}  ({round((spot-sp_sugg)/spot*100,1)}% below spot)")
        print(f"  Long Call   : ~{C(str(lc_sugg),GRAY)}  (200 pts above short call)")
        print(f"  Long Put    : ~{C(str(lp_sugg),GRAY)}  (200 pts below short put)")
        print()
    else:
        sc_sugg=sp_sugg=lc_sugg=lp_sugg=None

    # ── Enter all 4 strikes ──────────────────────────────────────
    print(C("  Enter Strikes (Rule: Short Put 2-2.5% below, Short Call 2-2.5% above):",YELLOW))
    try:
        sc=int(input(C(f"  Short Call strike [{sc_sugg}]: ",CYAN)).strip() or str(sc_sugg))
        sp=int(input(C(f"  Short Put  strike [{sp_sugg}]: ",CYAN)).strip() or str(sp_sugg))
        lc=int(input(C(f"  Long  Call strike [{lc_sugg}]: ",CYAN)).strip() or str(lc_sugg))
        lp=int(input(C(f"  Long  Put  strike [{lp_sugg}]: ",CYAN)).strip() or str(lp_sugg))
    except: print(C("  ✗ Invalid.",RED)); return

    # validate hedge width (150-200 rule)
    call_hedge_w=lc-sc; put_hedge_w=sp-lp
    if call_hedge_w<150: print(C(f"  ⚠  Rule: Long Call hedge only {call_hedge_w}pts from Short — min 150pts.",YELLOW))
    if put_hedge_w<150:  print(C(f"  ⚠  Rule: Long Put hedge only {put_hedge_w}pts from Short — min 150pts.",YELLOW))
    if call_hedge_w>250: print(C(f"  ⚠  Rule: Call hedge {call_hedge_w}pts — may be too far (150-200 recommended).",YELLOW))
    if put_hedge_w>250:  print(C(C(f"  ⚠  Rule: Put hedge {put_hedge_w}pts — may be too far (150-200 recommended).",YELLOW),""))

    try: lots=int(input(C("  Lots [1]: ",CYAN)).strip() or "1")
    except: lots=1

    print()
    print(C(f"  ⚠️  IMPORTANT: Enter premiums for expiry date: {C(expiry_date, YELLOW+BOLD)}", RED))
    print(C(f"  Do NOT use premiums from other expiry dates!", RED+BOLD))
    print()
    print(C("  Enter premiums collected/paid:",YELLOW))
    try:
        sc_p=float(input(C(f"  SHORT CALL  {sc}  premium collected (for {expiry_date}) : Rs ",CYAN)).strip())
        sp_p=float(input(C(f"  SHORT PUT   {sp}  premium collected (for {expiry_date}) : Rs ",CYAN)).strip())
        lc_p=float(input(C(f"  LONG  CALL  {lc}  premium paid (for {expiry_date})      : Rs ",CYAN)).strip())
        lp_p=float(input(C(f"  LONG  PUT   {lp}  premium paid (for {expiry_date})      : Rs ",CYAN)).strip())
    except: print(C("  ✗ Invalid.",RED)); return

    nc=round((sc_p+sp_p)-(lc_p+lp_p),2)

    # Section 3 rule: min Rs1500 net credit
    if nc*lots*s["lot_size"] < 1500:
        print(C(f"\n  ⚠  Rule (Section 3): Net credit Rs{nc*lots*s['lot_size']:.0f} < Rs1500 minimum.",YELLOW))
        print(C("  Risk/reward is unfavourable. Consider skipping.",YELLOW))
        if input(C("  Enter anyway? (y/n): ",CYAN)).strip().lower()!="y": return

    ube=round(sc+nc,2); lbe=round(sp-nc,2)
    stop_loss=-2*nc; profit_target=nc*0.65
    max_loss_per_lot=round(call_hedge_w-nc,2)   # wing width minus credit

    print()
    print(C("  ── Trade Summary ──────────────────────────────────",CYAN))
    print(f"  Short Call  : {sc}  @ Rs{sc_p:.1f}   Long Call  : {lc}  @ Rs{lc_p:.1f}")
    print(f"  Short Put   : {sp}  @ Rs{sp_p:.1f}   Long Put   : {lp}  @ Rs{lp_p:.1f}")
    print(f"  Net Credit  : {C(f'Rs {nc:.1f} per lot',GREEN)}")
    print(f"  Upper BE    : {C(str(ube),YELLOW)}")
    print(f"  Lower BE    : {C(str(lbe),YELLOW)}")
    print(f"  Profit target (65%)  : Rs {profit_target:.1f}/lot")
    print(f"  Hard stop (2x credit): Rs {stop_loss:.1f}/lot  ← MANDATORY")
    print(f"  Max loss (if both legs ITM): Rs {max_loss_per_lot:.1f}/lot")
    print()

    # Section 5: Greeks reminder
    print(C("  ── Greeks to Monitor (Section 5) ─────────────────",CYAN))
    print(f"  Delta  : keep │Δ│ < 15.  If > 20 → MUST act (roll or hedge).")
    print(f"  Theta  : aim > Rs 2000/day.  If < Rs 500 → trade overextended.")
    print(f"  Gamma  : keep < 0.05.  High near expiry — close by 2-3 DTE.")
    print(f"  Vega   : negative (want IV to fall).  If IV spikes 15%+ → exit.")
    print()

    conf=input(C("  Confirm entry (y/n): ",CYAN)).strip().lower()
    if conf!="y": return

    s.update({"net_credit":nc,"upper_be":ube,"lower_be":lbe,
              "short_call":sc,"short_put":sp,
              "entry_spot":spot or sc,"current_spot":spot or sc,
              "entry_time":datetime.now().isoformat(),
              "phase":"LIVE","roll_count":{"call":0,"put":0},
              "adjustments":[],"pnl_snapshots":[]})
    s["legs"]={"short_call":{"strike":sc,"type":"CE","action":"sell","lots":lots,"entry_premium":sc_p,"current_premium":sc_p},
               "short_put": {"strike":sp,"type":"PE","action":"sell","lots":lots,"entry_premium":sp_p,"current_premium":sp_p},
               "long_call":  {"strike":lc,"type":"CE","action":"buy", "lots":lots,"entry_premium":lc_p,"current_premium":lc_p},
               "long_put":   {"strike":lp,"type":"PE","action":"buy", "lots":lots,"entry_premium":lp_p,"current_premium":lp_p}}

    expiry_date,dte=select_expiry()
    s["expiry_date"]=expiry_date; s["dte_at_entry"]=dte
    log_event(s,f"IronCondor SC={sc} SP={sp} LC={lc} LP={lp} credit=Rs{nc:.1f} BE={lbe}-{ube} expiry={expiry_date}")
    save_state(s)
    print(); print(C("═"*66,GREEN)); print(C("  ✅  IRON CONDOR ENTERED",GREEN+BOLD)); print(C("═"*66,GREEN))
    show_position_summary(s); draw_payoff_chart(s)
    print(C("  Starting live 15-min monitor… CTRL+C to save & exit.",CYAN))
    time.sleep(1); live_monitor_loop(s)



# ═══════════════════════════════════════════════════════════════════
#  BUTTERFLY — SETUP  (Theta Gainers Rule Book — all 9 sections)
# ═══════════════════════════════════════════════════════════════════
def butterfly_setup(s):
    hdr("BUTTERFLY STRATEGY — SETUP  (Theta Gainers Rule Book)")

    spot = s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI"))
    vc   = s.get("vix_current",17)

    # ── Entry condition checks [CR-01] ───────────────────────────
    print(C("  ── Step 1: Market Condition Check [CR-01] ─────────",CYAN))
    if spot:
        print(f"  Live Spot   : {C(B(str(spot)),GREEN)}")
        print(f"  India VIX   : {C(str(vc),YELLOW)}")
    print()
    print(C("  Is market SIDEWAYS / RANGE-BOUND?",YELLOW+BOLD))
    print(C("  [DN-02] Butterfly ONLY works in sideways markets. NEVER in trend.",RED))
    print(C("  [DN-06] AVOID if major event (Budget/RBI/Fed) within 2 days.",RED))
    print(C("  [OV-04] NEVER trade on daily expiry — weekly/monthly ONLY.",RED))
    print()
    print("   ✅ Checks to confirm before proceeding:")
    print("   □  Clear range between support and resistance")
    print("   □  No strong breakout or breakdown underway")
    print("   □  No major event within 2 days of expiry")
    print("   □  Weekly or monthly expiry selected (NOT daily)")
    print()
    if input(C("  All checks passed? (y/n): ",CYAN)).strip().lower()!="y":
        print(C("  ⛔  Do NOT enter butterfly in current conditions.",RED+BOLD)); return

    # ── Butterfly type [TY-01..04] ────────────────────────────────
    print()
    print(C("  ── Step 2: Select Butterfly Type [TY-01..04] ──────",CYAN))
    print(f"  {C('[1]',GREEN)} Neutral Butterfly   — center at ATM, both sides 200 pts  [TY-01]")
    print(f"  {C('[2]',YELLOW)} Bullish Butterfly   — upper hedge 100 pts, lower 200 pts [TY-02]")
    print(f"  {C('[3]',YELLOW)} Bearish Butterfly   — lower hedge 100 pts, upper 200 pts [TY-03]")
    print(f"  {C('[4]',CYAN)} Batman / Double     — two butterflies combined, wide zone [TY-04]")
    print()
    try:
        btype = int(input(C("  Select type (1-4): ",CYAN)).strip())
    except: btype=1
    if btype not in [1,2,3,4]: btype=1
    type_labels={1:"Neutral",2:"Bullish",3:"Bearish",4:"Batman"}
    print(C(f"  Selected: {type_labels[btype]} Butterfly",GREEN))

    # Batman condition check [BM-01]
    if btype==4:
        print()
        print(C("  Batman Conditions [BM-01] — confirm ALL:",YELLOW+BOLD))
        print("   □  Market swinging 300-400 pts intraday")
        print("   □  Single butterfly feels too tight")
        print("   □  Market still RANGE-BOUND (not trending)")
        print("   □  3-5 days of expiry remaining")
        print(C("  [DN-05] NEVER create Batman in a trending market.",RED))
        if input(C("  All Batman conditions met? (y/n): ",CYAN)).strip().lower()!="y":
            print(C("  Reverting to Neutral Butterfly.",YELLOW)); btype=1

    # ── Option side [CR-04] ───────────────────────────────────────
    print()
    print(C("  ── Step 3: Select Option Side [CR-04] ─────────────",CYAN))
    print(f"  {C('[P]',GREEN)} Put Butterfly  — Sell 2x ATM PE + Buy ITM PE + Buy OTM PE")
    print(f"  {C('[C]',CYAN)} Call Butterfly — Sell 2x ATM CE + Buy OTM CE + Buy ITM CE")
    print()
    side = input(C("  Side (P/C): ",CYAN)).strip().upper()
    if side not in ["P","C"]: side="P"
    opt_type = "PE" if side=="P" else "CE"

    # ── ATM strike and hedge distances [CR-02, CR-03] ─────────────
    print()
    print(C("  ── Step 4: Strike Selection [CR-02, CR-03] ────────",CYAN))
    if spot:
        atm_sugg = int(round(spot/50)*50)
        print(f"  Live spot {spot} → suggested ATM : {C(str(atm_sugg),YELLOW)}")
    else: atm_sugg=None
    try:
        raw=input(C(f"  Center (ATM) strike to SELL 2x [{atm_sugg}]: ",CYAN)).strip()
        atm=int(raw) if raw else atm_sugg
    except: print(C("  ✗ Invalid.",RED)); return

    # hedge distances based on type
    if btype==1:   # Neutral
        d_lower=200; d_upper=200
    elif btype==2: # Bullish — upper tighter
        d_lower=200; d_upper=100
    elif btype==3: # Bearish — lower tighter
        d_lower=100; d_upper=200
    else:          # Batman — standard first butterfly
        d_lower=200; d_upper=200

    # For PUT butterfly: OTM = lower strike, ITM = upper strike
    # For CALL butterfly: OTM = upper strike, ITM = lower strike
    if side=="P":
        otm_strike = atm - d_lower
        itm_strike = atm + d_upper
    else:
        otm_strike = atm + d_upper
        itm_strike = atm - d_lower

    print()
    print(C("  Suggested structure [CR-04]:",CYAN))
    print(f"  SELL 2x ATM   : {C(str(atm)+' '+opt_type, YELLOW)}")
    print(f"  BUY  1x OTM   : {C(str(otm_strike)+' '+opt_type, GRAY)}")
    print(f"  BUY  1x ITM   : {C(str(itm_strike)+' '+opt_type, GRAY)}")
    print()
    print(C("  [CR-03] Larger hedge distance = wider range, lower reward.",GRAY))
    print(C("  [CR-03] Smaller distance = narrower range, higher reward.",GRAY))
    print()

    # allow override
    try:
        raw=input(C(f"  OTM strike [{otm_strike}] (ENTER to accept): ",CYAN)).strip()
        otm_strike=int(raw) if raw else otm_strike
        raw=input(C(f"  ITM strike [{itm_strike}] (ENTER to accept): ",CYAN)).strip()
        itm_strike=int(raw) if raw else itm_strike
    except: pass

    # ── Lots ──────────────────────────────────────────────────────
    print()
    print(C("  [PS-04] Size Discipline: Max loss ≤ 2% of total capital.",YELLOW))
    try:
        cap=float(input(C("  Total trading capital Rs (for size check, ENTER to skip): ",CYAN)).strip() or "0")
    except: cap=0
    try: lots=int(input(C("  Number of lots [1]: ",CYAN)).strip() or "1")
    except: lots=1

    # ── Premiums ──────────────────────────────────────────────────
    print()
    print(C("  ── Step 5: Enter Premiums [CR-05] ─────────────────",CYAN))
    try:
        atm_p  = float(input(C(f"  SELL ATM {atm} {opt_type} premium (×2 lots) : Rs ",CYAN)).strip())
        otm_p  = float(input(C(f"  BUY  OTM {otm_strike} {opt_type} premium     : Rs ",CYAN)).strip())
        itm_p  = float(input(C(f"  BUY  ITM {itm_strike} {opt_type} premium     : Rs ",CYAN)).strip())
    except: print(C("  ✗ Invalid.",RED)); return

    # net credit/debit (2 sells - 2 buys)
    net = round(2*atm_p - otm_p - itm_p, 2)
    max_profit = round((itm_strike-atm_strike if side=="P" else atm_strike-otm_strike)-abs(net), 2) if False else round(abs(net), 2)

    # BE points: spot where net P&L = 0
    # For standard butterfly: BE_upper = ATM + net_credit, BE_lower = ATM - net_credit (approx)
    if net > 0:  # credit structure (preferred)
        ube = round(atm + net, 2)
        lbe = round(atm - net, 2)
    else:        # debit structure
        ube = round(atm - net, 2)   # net is negative
        lbe = round(atm + net, 2)

    wing_width = abs(itm_strike - atm)

    # [OV-03] Risk-Reward check
    max_loss_approx  = round(abs(otm_p + itm_p - 2*atm_p), 2)
    max_profit_approx= wing_width - max_loss_approx
    rr = round(max_profit_approx/max_loss_approx, 2) if max_loss_approx>0 else 0

    print()
    print(C("  ── Step 5: Risk-Reward Verification [CR-05, OV-03] ─",CYAN))
    print(f"  Net credit/debit    : {C(f'Rs {net:.1f} ({"credit" if net>0 else "debit"})',GREEN if net>=0 else YELLOW)}")
    print(f"  Wing width          : {wing_width} pts")
    print(f"  Max loss (approx)   : {C(f'Rs {max_loss_approx:.0f}',RED)}")
    print(f"  Max profit (approx) : {C(f'Rs {max_profit_approx:.0f}',GREEN)}")
    print(f"  Risk-Reward         : {C(f'1 : {rr:.1f}',GREEN if rr>=3 else YELLOW)}")

    if rr < 3:
        print(C(f"  ⚠  [OV-03] R/R {rr:.1f} below 1:3 minimum. Review structure.",YELLOW))
    else:
        print(C(f"  ✅  [OV-03] R/R ≥ 1:3 — structure acceptable.",GREEN))

    if cap > 0:
        loss_pct = max_loss_approx*lots*s["lot_size"]/cap*100
        print(f"  Max loss % of capital: {C(f'{loss_pct:.1f}%',GREEN if loss_pct<=2 else RED)}")
        if loss_pct > 2:
            print(C(f"  ⚠  [PS-04] Exceeds 2% capital rule. Reduce to {int(cap*0.02/(max_loss_approx*s['lot_size']))} lots.",RED))

    # ── OI alignment reminder [CR-06] ────────────────────────────
    print()
    print(C("  ── Step 6: Open Interest Alignment [CR-06] ────────",CYAN))
    print(C("  Check on your broker: Does butterfly profit zone cover high OI strikes?",YELLOW))
    print(C("  High OI Call = resistance (upper boundary)",GRAY))
    print(C("  High OI Put  = support   (lower boundary)",GRAY))
    print(C("  Ideal: your BEs sit beyond those OI zones.",GRAY))
    print()

    conf=input(C("  Confirm entry (y/n): ",CYAN)).strip().lower()
    if conf!="y": return

    # ── Batman: add second butterfly ─────────────────────────────
    batman_legs={}
    if btype==4:
        print()
        print(C("  ── Batman: Second Butterfly [BM-02] ────────────────",CYAN))
        print(C("  Second butterfly center = near upper edge of first butterfly.",YELLOW))
        b2_center_sugg = itm_strike   # upper edge of first
        print(f"  Suggested 2nd center : {C(str(b2_center_sugg),YELLOW)}")
        try:
            raw=input(C(f"  2nd butterfly center [{b2_center_sugg}]: ",CYAN)).strip()
            b2_atm=int(raw) if raw else b2_center_sugg
            b2_otm = b2_atm - 200 if side=="P" else b2_atm + 200
            b2_itm = b2_atm + 200 if side=="P" else b2_atm - 200
            print(f"  2nd butterfly: SELL 2x {b2_atm} | BUY {b2_otm} | BUY {b2_itm}")
            b2_atm_p=float(input(C(f"  SELL {b2_atm} {opt_type} ×2 premium : Rs ",CYAN)).strip())
            b2_otm_p=float(input(C(f"  BUY  {b2_otm} {opt_type} premium    : Rs ",CYAN)).strip())
            b2_itm_p=float(input(C(f"  BUY  {b2_itm} {opt_type} premium    : Rs ",CYAN)).strip())
            batman_legs={
                "b2_short": {"strike":b2_atm,"type":opt_type,"action":"sell","lots":lots,"entry_premium":b2_atm_p,"current_premium":b2_atm_p,"qty":2},
                "b2_otm":   {"strike":b2_otm,"type":opt_type,"action":"buy", "lots":lots,"entry_premium":b2_otm_p,"current_premium":b2_otm_p},
                "b2_itm":   {"strike":b2_itm,"type":opt_type,"action":"buy", "lots":lots,"entry_premium":b2_itm_p,"current_premium":b2_itm_p},
            }
            b2_net=round(2*b2_atm_p-b2_otm_p-b2_itm_p,2)
            total_net=round(net+b2_net,2)
            # Batman BE: wider
            ube=round(max(b2_itm,itm_strike)+total_net,2)
            lbe=round(min(b2_otm,otm_strike)-total_net,2)
            net=total_net
            print(C(f"  Batman combined net: Rs{total_net:.1f}  BEs: {lbe}–{ube}",GREEN))
            print(C("  [BM-03] Max profit reduces vs single; safe zone widens by 300-400 pts.",GRAY))
        except:
            print(C("  ✗ Invalid. Proceeding as single butterfly.",YELLOW))
            batman_legs={}; btype=1

    # ── store state ───────────────────────────────────────────────
    s.update({
        "atm_strike":atm, "wing_width":wing_width,
        "net_credit":net, "upper_be":ube, "lower_be":lbe,
        "butterfly_type":type_labels[btype],
        "butterfly_side":opt_type, "batman":(btype==4),
        "entry_spot":spot or atm, "current_spot":spot or atm,
        "entry_time":datetime.now().isoformat(),
        "phase":"LIVE", "roll_count":{"call":0,"put":0},
        "adjustments":[], "pnl_snapshots":[],
        "max_loss_approx":max_loss_approx,
        "max_profit_approx":max_profit_approx,
    })
    s["legs"]={
        "short_atm1":{"strike":atm,"type":opt_type,"action":"sell","lots":lots,"entry_premium":atm_p,"current_premium":atm_p,"qty":2},
        "short_atm2":{"strike":atm,"type":opt_type,"action":"sell","lots":lots,"entry_premium":atm_p,"current_premium":atm_p,"qty_label":"(2nd of 2)"},
        "long_otm":  {"strike":otm_strike,"type":opt_type,"action":"buy","lots":lots,"entry_premium":otm_p,"current_premium":otm_p},
        "long_itm":  {"strike":itm_strike,"type":opt_type,"action":"buy","lots":lots,"entry_premium":itm_p,"current_premium":itm_p},
    }
    if batman_legs: s["legs"].update(batman_legs)

    expiry_date,dte=select_expiry()
    s["expiry_date"]=expiry_date; s["dte_at_entry"]=dte
    log_event(s,f"Butterfly {type_labels[btype]} {opt_type}: ATM={atm} OTM={otm_strike} ITM={itm_strike} net=Rs{net:.1f} BE={lbe}-{ube} expiry={expiry_date}")
    save_state(s)

    print(); print(C("═"*66,GREEN))
    print(C(f"  ✅  {type_labels[btype].upper()} BUTTERFLY ENTERED",GREEN+BOLD))
    print(C("═"*66,GREEN))
    print()
    print(C("  [MG-01] DO NOTHING as long as price stays within range.",CYAN+BOLD))
    print(C("  [MG-02] Monitor at 3 PM near-expiry only. Avoid over-monitoring.",CYAN))
    print(C("  [PS-01] This is a PEACE OF MIND strategy. Theta works for you.",CYAN))
    print()
    show_position_summary(s); draw_payoff_chart(s)
    print(C("  Starting live 15-min monitor… CTRL+C to save & exit.",CYAN))
    time.sleep(1); live_monitor_loop(s)


# ═══════════════════════════════════════════════════════════════════
#  BUTTERFLY — BREACH / ADJUSTMENT GUIDANCE
# ═══════════════════════════════════════════════════════════════════
def guide_bf_breach(s, spot, side):
    up = (side=="upper")
    ube=s["upper_be"]; lbe=s["lower_be"]
    nc=s.get("net_credit",0)
    max_loss=s.get("max_loss_approx",abs(nc))
    batman=s.get("batman",False)
    opt_type=s.get("butterfly_side","PE")
    btype=s.get("butterfly_type","Neutral")

    print()
    print(C(f"  ╔{'═'*58}╗",RED+BOLD))
    print(C(f"  ║  ⚠  BUTTERFLY {'UPPER' if up else 'LOWER'} BREAKEVEN BREACH {'='*(25 if up else 24)}║",RED+BOLD))
    print(C(f"  ╚{'═'*58}╝",RED+BOLD))
    print()

    dist = round(spot - ube,1) if up else round(lbe - spot,1)
    print(f"  Spot {spot}  BE {ube if up else lbe}  →  {C(str(dist)+' pts beyond BE',RED)}")
    print()

    # Batman-specific: check if BOTH peaks breached [BM-04]
    if batman:
        print(C("  [BM-04] Batman: Only act if BOTH peaks are breached.",YELLOW+BOLD))
        print(C("  If price is between peaks — this is NORMAL. DO NOTHING.",YELLOW))
        print()

    # [MG-01] DO NOTHING reminder
    print(C("  ── Rule [MG-01]: Do Nothing Check ─────────────────",CYAN))
    print(C("  Is price JUST touching the BE or significantly past it?",YELLOW))
    print(C("  Small MTM loss is NORMAL — DO NOT panic exit. [DN-03]",YELLOW))
    print()

    # [MG-04/05] Adjustment: sell OTM spread on OPPOSITE side
    if up:
        spread_label="OTM Put Spread"
        action_desc="Sell far OTM PUT spread to collect premium and recover gradually"
        eg_sell="Sell low PE strike"
        eg_buy ="Buy even lower PE strike"
    else:
        spread_label="OTM Call Spread"
        action_desc="Sell far OTM CALL spread to collect premium and recover gradually"
        eg_sell="Sell high CE strike"
        eg_buy ="Buy even higher CE strike"

    recovery_pts=round(max_loss/25,0)
    print(C(f"  ── Adjustment [MG-0{'4' if up else '5'}]: {spread_label} Recovery ──────────",CYAN))
    print(f"  Max loss = Rs{max_loss:.0f}  →  Need to collect {recovery_pts} pts total")
    print(f"  {C('Strategy:',YELLOW)} {action_desc}")
    print(f"  {C('Per tranche:',GRAY)} Collect ~{round(recovery_pts/3,0):.0f} pts per execution")
    print()
    print(C("  ⚠  DO THIS IN TRANCHES — not all at once! [MG-04]",RED))
    print(C("  ⚠  Market may REVERSE — original position profits if it does! [MG-05]",YELLOW))
    print()

    # [MG-06] Exit check
    print(C("  ── Exit Check [MG-06] ──────────────────────────────",CYAN))
    print(C("  DO NOT exit because of small mid-week MTM loss.",GRAY))
    print(f"  {C('EXIT only if:',RED)}")
    print(f"  □  Price blown through BOTH wings (max loss zone)")
    print(f"  □  Clear sustained trend established")
    print(f"  □  Cost to adjust > potential recovery")
    print()

    # [SC-06] Full acceptance
    print(C("  ── Scenario F [SC-06] — If trend is clear ──────────",CYAN))
    print(C("  Accept the DEFINED max loss. DO NOT average. DO NOT add legs.",RED+BOLD))
    print(C("  The defined loss was your risk — honor it. [PS-03]",GRAY))
    print()

    print(C("  ── Take Action ─────────────────────────────────────",CYAN))
    print("   H = Hold (do nothing — [MG-01] rule)")
    print(f"   S = Sell {spread_label} tranche  (gradual recovery — [MG-04/05])")
    print("   N = Convert to No-Loss setup  (if 30-50% profitable — [MG-03])")
    print("   X = Accept max loss and exit  ([SC-06])")
    print()
    ch=input(C("  Action (H/S/N/X): ",CYAN)).strip().upper()

    if ch=="H":
        print(C("  ✅  Holding. Theta works. Monitor at 3 PM near expiry. [MG-02]",GREEN))

    elif ch=="S":
        print()
        print(C(f"  Recording {spread_label} tranche [MG-04/05]:",YELLOW))
        try:
            s_strike=int(input(C(f"  {eg_sell} strike: ",CYAN)).strip())
            s_prem  =float(input(C(f"  Premium collected Rs: ",CYAN)).strip())
            b_strike=int(input(C(f"  {eg_buy} strike: ",CYAN)).strip())
            b_prem  =float(input(C(f"  Premium paid Rs: ",CYAN)).strip())
        except: print(C("  ✗ Invalid.",RED)); return
        tranche_credit=round(s_prem-b_prem,2)
        adj_type="OTM Put Spread" if up else "OTM Call Spread"
        s["adjustments"].append({
            "type":f"Recovery {adj_type}",
            "sell_strike":s_strike,"sell_prem":s_prem,
            "buy_strike":b_strike,"buy_prem":b_prem,
            "tranche_credit":tranche_credit,
            "action":"spread","closed":False,
            "ts":datetime.now().isoformat()
        })
        log_event(s,f"BF Recovery tranche: sell {s_strike} @ Rs{s_prem:.0f} / buy {b_strike} @ Rs{b_prem:.0f} = Rs{tranche_credit:.0f} credit")
        save_state(s); draw_payoff_chart(s)
        print(C(f"  ✅  Tranche recorded. Credit collected: Rs{tranche_credit:.1f}",GREEN))
        print(C(f"  Total tranches so far: {len(s['adjustments'])}",GRAY))

    elif ch=="N":
        print()
        print(C("  No-Loss Conversion [MG-03, SC-05]:",YELLOW+BOLD))
        print(C("  1. Buy back some ATM options at profit",GRAY))
        print(C("  2. Restructure remaining legs so worst case = breakeven",GRAY))
        print(C("  3. 'Full Green' mode — zero loss possible",GRAY))
        print()
        try:
            buyback_qty=int(input(C("  How many ATM lots to buy back: ",CYAN)).strip())
            buyback_prem=float(input(C(f"  Buyback price for ATM {s.get('atm_strike','')} {opt_type}: Rs ",CYAN)).strip())
        except: print(C("  ✗ Invalid.",RED)); return
        s["adjustments"].append({
            "type":"No-Loss Conversion","action":"buyback",
            "strike":s.get("atm_strike",0),"qty":buyback_qty,
            "close_premium":buyback_prem,"closed":False,
            "ts":datetime.now().isoformat()
        })
        log_event(s,f"BF No-Loss: bought back {buyback_qty} ATM @ Rs{buyback_prem:.0f}")
        save_state(s)
        print(C("  ✅  No-Loss conversion recorded. Monitor remaining structure.",GREEN))

    elif ch=="X":
        print()
        print(C("  ── Accepting Defined Max Loss [SC-06] ───────────",RED+BOLD))
        print(C("  DO NOT add more options. DO NOT average. Exit cleanly.",RED))
        print()
        if input(C("  Confirm full exit (y/n): ",CYAN)).strip().lower()=="y":
            expiry_close_wizard(s)
    else:
        print(C("  ✗ Invalid choice.",RED))



# ═══════════════════════════════════════════════════════════════════
#  BROKER-STYLE POSITION TABLE  (matches your broker's UI format)
#  Columns: Position | Entry Price | Current Price | Exit Price | P&L
# ═══════════════════════════════════════════════════════════════════
def print_broker_table(s, spot, pnl_per_lot, pct, lots, strat):
    """
    Print a broker-style position table every 15-min scan.
    Matches the format: Position | Entry Price | Current Price | Exit Price | P&L
    """
    lsize    = s.get("lot_size", 75)
    nc       = s.get("net_credit", 0)
    exp_str  = s.get("expiry_date", "")
    dte_now  = "?"
    if exp_str:
        try:
            dte_now = (datetime.strptime(exp_str,"%Y-%m-%d").date()-datetime.now().date()).days
        except: pass

    # ── header bar ────────────────────────────────────────────────
    strat_label = {"ironfly":"Iron Fly","ironcondor":"Iron Condor",
                   "butterfly":"Butterfly","credit_spread":"Credit Spread",
                   "calendar":"Calendar Spread"}.get(strat,"Position")
    now_str  = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    dte_col  = YELLOW if isinstance(dte_now,int) and dte_now<=3 else GRAY

    if strat == "calendar":
        far_exp = s.get("cal_far_expiry", "")
        far_dte = _leg_dte(far_exp) if far_exp else "?"
        exp_hdr = (f"Near: {exp_str} ({dte_now} DTE)  │  "
                   f"Far: {far_exp} ({far_dte} DTE)")
    else:
        exp_hdr = f"Expiry: {exp_str}  │  {dte_now} DTE"

    print()
    print(C("  ═"*33, CYAN))
    print(C(f"  ⚡  {strat_label}  │  Spot: {spot}  │  {exp_hdr}  │  {now_str}", CYAN+BOLD))
    print(C("  ═"*33, CYAN))
    print()

    # ── column widths ─────────────────────────────────────────────
    C1,C2,C3,C4,C5 = 28, 13, 15, 11, 12

    # header row (matches broker UI exactly)
    def row(p,ep,cp,xp,pl, header=False):
        hc = BOLD if header else ""
        pc_pl = GREEN if not header and isinstance(pl,(int,float)) and pl>=0 else (RED if not header and isinstance(pl,(int,float)) else GRAY)
        return (f"  {C(str(p),hc+CYAN if header else RESET):<{C1+9}}"
                f"  {C(str(ep),hc+GRAY if header else YELLOW):<{C2+9}}"
                f"  {C(str(cp),hc+GRAY if header else CYAN):<{C3+9}}"
                f"  {C(str(xp),hc+GRAY if header else GRAY):<{C4+9}}"
                f"  {C(str(pl),hc+GRAY if header else pc_pl+BOLD)}")

    print(row("Position","Entry Price","Current Price","Exit Price","P&L", header=True))
    print(f"  {'─'*80}")

    # ── build rows from all open legs ────────────────────────────
    total_pnl_rs = 0.0
    leg_rows = []

    for key, leg in s["legs"].items():
        if not leg or leg.get("closed"): continue
        qty      = leg.get("lots", lots)
        sign     = "-" if leg["action"]=="sell" else "+"
        qty_str  = f"{sign}{qty}x"
        leg_exp  = _leg_expiry(s, leg)
        exp_fmt  = _format_expiry(leg_exp)
        pos_lbl  = f"{qty_str} {exp_fmt} {leg['strike']}{leg['type']}"

        entry_p  = round(leg["entry_premium"], 2)
        curr_p   = round(leg.get("current_premium", entry_p), 2)
        exit_p   = 0   # 0 = not exited yet (matches broker display)

        if leg["action"] == "sell":
            leg_pnl = round((entry_p - curr_p) * qty * lsize, 2)
        else:
            leg_pnl = round((curr_p - entry_p) * qty * lsize, 2)

        total_pnl_rs += leg_pnl
        leg_rows.append((pos_lbl, entry_p, curr_p, exit_p, leg_pnl))

    # adjustments
    for i, adj in enumerate(s.get("adjustments",[]),1):
        if adj.get("closed"): continue
        if "strike" not in adj: continue
        sign     = "-" if adj["action"]=="sell" else "+"
        adj_exp  = adj.get("expiry") or exp_str
        exp_fmt  = _format_expiry(adj_exp)
        pos_lbl  = f"{sign}1x {exp_fmt} {adj['strike']}{adj.get('opt_type','')}"
        entry_p  = round(adj["entry_premium"],2)
        curr_p   = round(adj.get("current_premium",entry_p),2)
        if adj["action"]=="sell":
            adj_pnl=round((entry_p-curr_p)*lsize,2)
        else:
            adj_pnl=round((curr_p-entry_p)*lsize,2)
        total_pnl_rs+=adj_pnl
        leg_rows.append((pos_lbl, entry_p, curr_p, 0, adj_pnl))

    # print all leg rows
    for pos_lbl, ep, cp, xp, lp in leg_rows:
        print(row(pos_lbl, ep, cp, xp, round(lp,2)))

    # ── totals row (matches broker UI) ────────────────────────────
    print(f"  {'─'*80}")
    total_col = GREEN+BOLD if total_pnl_rs>=0 else RED+BOLD
    # blank columns, then "Total P&L" label and value
    print(f"  {'':>{C1+C2+C3+2}}  {'':>{C4-2}}  {C('Total P&L',BOLD):<18}  {C(str(round(total_pnl_rs,2)),total_col)}")

    # ── summary footer ────────────────────────────────────────────
    print()
    be_up=s.get("upper_be","─"); be_lo=s.get("lower_be","─")
    pnl_lot = round(pnl_per_lot, 2)
    pnl_c   = GREEN if pnl_lot>=0 else RED

    print(f"  {'Upper BE':<16}: {C(str(be_up),YELLOW)}    {'Lower BE':<12}: {C(str(be_lo),YELLOW)}")
    print(f"  {'P&L / lot':<16}: {C(f'Rs {pnl_lot}  ({pct:+.1f}%)',pnl_c)}")
    print(f"  {'P&L TOTAL':<16}: {C(f'Rs {total_pnl_rs:.2f}  ({lots} lot × {lsize})',pnl_c+BOLD)}")
    print(f"  {'Net Credit':<16}: {C(f'Rs {nc:.1f}',GREEN)}    {'Max Profit':<12}: {C(f'Rs {nc*0.65:.0f} (65%)',CYAN)}")
    print(f"  {'Hard Stop':<16}: {C(f'Rs {-2*nc:.0f} (2x credit)',RED)}")

    # range status bar
    ube = s.get("upper_be")
    lbe = s.get("lower_be")
    if strat == "calendar":
        ube = ube if ube is not None else s.get("cal_near_call")
        lbe = lbe if lbe is not None else s.get("cal_near_put")
    dist_up = (ube - spot) if ube is not None else None
    dist_lo = (spot - lbe) if lbe is not None else None
    if isinstance(dist_up,(int,float)) and isinstance(dist_lo,(int,float)):
        if dist_up > 0 and dist_lo > 0:
            total_range = dist_up + dist_lo
            filled = int(dist_lo / total_range * 20)
            bar = "█"*filled + "░"*(20-filled)
            range_c = GREEN if min(dist_up,dist_lo)>80 else (YELLOW if min(dist_up,dist_lo)>30 else RED)
            print(f"  Range position : {C('['+bar+']',range_c)}  ↓{dist_lo:.0f}pts from lower  ↑{dist_up:.0f}pts to upper")
    print(C("  📊  Prices from Yahoo Finance (~15min delayed)", GRAY))
    print()

# ═══════════════════════════════════════════════════════════════════
#  BREACH DETECTION  (Iron Condor uses short strikes, not BE, for proximity)
# ═══════════════════════════════════════════════════════════════════
def check_breach(s,candles):
    if not candles: return None
    now_t=datetime.now()
    if now_t.hour==9 and now_t.minute<30: return None

    latest=candles[-1]
    ube=s["upper_be"]; lbe=s["lower_be"]
    strat=s.get("strategy","ironfly")

    # Iron Condor uses SHORT STRIKE proximity (within 50 pts) as trigger (Section 6)
    if strat=="ironcondor":
        sc=s.get("short_call"); sp=s.get("short_put")
        # breached short strike — emergency (spot at or past short)
        if sc and latest>=sc:                    return "upper_strong"
        if sp and latest<=sp:                    return "lower_strong"
        # within 50 pts of short strike — early warning
        if sc and 0<sc-latest<=50:               return "near_upper"
        if sp and 0<latest-sp<=50:               return "near_lower"
        # BE-level fallback
        if latest>ube:                           return "upper_strong"
        if latest<lbe:                           return "lower_strong"
        if ube-latest<80:                        return "near_upper"
        if latest-lbe<80:                        return "near_lower"
        return None

    # Calendar Spread — breach if sold strike moved > 1.5x credit
    if strat=="calendar":
        sold_strike=s.get("cal_sold_strike",0)
        sold_premium=s.get("cal_sold_prem",0)
        if sold_premium>0 and sold_strike>0:
            dist_from_sold=abs(latest-sold_strike)
            max_breach_dist=sold_premium*1.5
            if dist_from_sold<=max_breach_dist*0.5: return "near_breach"
            if dist_from_sold>=max_breach_dist: return "upper_strong" if latest>sold_strike else "lower_strong"
        return None

    # Strangle — both sides dangerous when breached
    if strat=="strangle":
        put_strike=s.get("short_put",0); call_strike=s.get("short_call",0)
        if put_strike and latest<=put_strike+20:  return "lower_strong"
        if put_strike and 0<latest-put_strike<=80: return "near_lower"
        if call_strike and latest>=call_strike-20: return "upper_strong"
        if call_strike and 0<call_strike-latest<=80: return "near_upper"
        return None

    # BWB — same BE logic but wide-wing side is the danger
    if strat=="bwb":
        bwb_bias=s.get("bwb_bias","bullish")
        wide=s.get("bwb_wide_strike",0)
        if bwb_bias=="bullish":  # put BWB — danger on downside (wide wing)
            if wide and latest<=wide+20: return "lower_strong"
            if wide and 0<latest-wide<=80: return "near_lower"
            if latest<lbe:               return "lower_confirmed"
            if latest-lbe<80:            return "near_lower"
        else:                           # call BWB — danger on upside
            if wide and latest>=wide-20: return "upper_strong"
            if wide and 0<wide-latest<=80: return "near_upper"
            if latest>ube:               return "upper_confirmed"
            if ube-latest<80:            return "near_upper"
        return None

    # Credit Spread — breach = sold strike approached/hit
    if strat=="credit_spread":
        sold=s.get("sold_strike",0); cs_type=s.get("cs_type","call")
        if cs_type=="call":
            if sold and latest>=sold:       return "upper_strong"
            if sold and 0<sold-latest<=50:  return "near_upper"
            if latest>ube:                  return "upper_strong"
            if ube-latest<80:               return "near_upper"
        else:
            if sold and latest<=sold:       return "lower_strong"
            if sold and 0<latest-sold<=50:  return "near_lower"
            if latest<lbe:                  return "lower_strong"
            if latest-lbe<80:               return "near_lower"
        return None

    # Butterfly — same BE-based logic as Iron Fly
    if strat=="butterfly":
        if latest>ube+30: return "upper_strong"
        if latest<lbe-30: return "lower_strong"
        if latest>ube: return "upper_confirmed"
        if latest<lbe: return "lower_confirmed"
        if ube-latest<80: return "near_upper"
        if latest-lbe<80: return "near_lower"
        return None

    # Iron Fly — BE-based
    if latest>ube+20: return "upper_strong"
    if latest<lbe-20: return "lower_strong"
    if latest>ube:
        diff=latest-ube
        if diff<=5 and len(candles)>=2:
            return "upper_confirmed" if candles[-2]>ube else "upper_marginal"
        return "upper_confirmed"
    if latest<lbe:
        diff=lbe-latest
        if diff<=5 and len(candles)>=2:
            return "lower_confirmed" if candles[-2]<lbe else "lower_marginal"
        return "lower_confirmed"
    if ube-latest<50: return "near_upper"
    if latest-lbe<50: return "near_lower"
    return None

def check_reversal(s,candles):
    if len(candles)<2: return False
    inside=lambda c: s["lower_be"]<c<s["upper_be"]
    return inside(candles[-2]) and inside(candles[-1])


# ═══════════════════════════════════════════════════════════════════
#  IRON CONDOR — ADJUSTMENT GUIDANCE  (Sections 6, 7, 10)
# ═══════════════════════════════════════════════════════════════════
def guide_ic_breach(s,spot,side):
    up=(side=="upper")
    sc=s.get("short_call"); sp_s=s.get("short_put")
    rc=s.get("roll_count",{"call":0,"put":0})
    side_str="SHORT CALL (upside)" if up else "SHORT PUT (downside)"
    short_strike=sc if up else sp_s
    dist=abs(spot-short_strike) if short_strike else 0

    print()
    print(C(f"  ╔{'═'*58}╗",RED+BOLD))
    print(C(f"  ║  🚨  {side_str} UNDER PRESSURE{' '*(58-len(side_str)-24)}║",RED+BOLD))
    print(C(f"  ╚{'═'*58}╝",RED+BOLD))
    print()
    print(f"  Spot vs Short strike : {C(str(spot),CYAN)} vs {C(str(short_strike),YELLOW)}  →  {C(str(dist)+' pts away',RED if dist<50 else YELLOW)}")
    print()

    roll_key="call" if up else "put"
    already_rolled=rc.get(roll_key,0)>=1

    # Section 6 Adjustment Decision Tree
    print(C("  ── Section 6 Decision Tree ─────────────────────────",CYAN))
    if spot>=(short_strike or 0) and up:
        print(C("  ❌  SHORT CALL BREACHED (spot ≥ short call)",RED+BOLD))
        print(C("  Rule: Close the breached spread (both legs together — Rule 9).",RED))
        print(C("  Do NOT roll after breach. Emergency close.",RED))
        _ic_emergency_close(s,spot,side)
        return
    elif spot<=(short_strike or float('inf')) and not up:
        print(C("  ❌  SHORT PUT BREACHED (spot ≤ short put)",RED+BOLD))
        print(C("  Rule: Close the breached spread (both legs together — Rule 9).",RED))
        _ic_emergency_close(s,spot,side)
        return

    if already_rolled:
        print(C("  ❌  Rule 3: Already rolled this side once. Cannot roll again.",RED+BOLD))
        print(C("  Rule: If second roll needed → EXIT entire position instead.",RED))
        print()
        guide_ic_exit(s,spot,"Second breach after roll — full exit required.")
        return

    print(C("  Adjustment Options:",YELLOW+BOLD))
    print()
    if dist<=100 and dist>50:
        print(f"  {C('Option A — Roll the Spread (Section 6, Adjustment 1)',CYAN)}")
        if up:
            new_sc=round((spot+150)/50)*50
            new_lc=new_sc+200
            print(f"  Close Short Call {sc} + Long Call {s['legs'].get('long_call',{}).get('strike','?')}")
            print(f"  Re-sell Short Call ~{C(str(new_sc),GREEN)} (100-150 pts further)")
            print(f"  Re-buy  Long  Call ~{C(str(new_lc),GREEN)} (200 pts further from new short)")
        else:
            new_sp=round((spot-150)/50)*50
            new_lp=new_sp-200
            print(f"  Close Short Put {sp_s} + Long Put {s['legs'].get('long_put',{}).get('strike','?')}")
            print(f"  Re-sell Short Put ~{C(str(new_sp),GREEN)} (100-150 pts further)")
            print(f"  Re-buy  Long  Put ~{C(str(new_lp),GREEN)} (200 pts further from new short)")
        print(f"  Net debit acceptable: max Rs 500")
        print(f"  Rule 3: {C('Max 1 roll per side. Do NOT roll again.',RED)}")
        print()
    if dist>100:
        print(f"  {C('Option B — Convert to Strangle (Slow trend — Adjustment 2)',CYAN)}")
        opp_leg="long_put" if up else "long_call"
        opp_val=s["legs"].get(opp_leg,{}).get("current_premium",0) if s["legs"].get(opp_leg) else 0
        print(f"  Close opposite-side Long hedge → collect ~Rs{opp_val:.0f}")
        print(f"  Use credit to sell the threatened short strike further out.")
        print(f"  Set hard stop: if market moves 100 pts more against you → close all.")
        print(f"  {C('Only in SLOW-trending markets. NOT for impulsive spikes.',RED)}")
        print()

    # Time check
    now_t=datetime.now()
    if now_t.hour==9 and now_t.minute<30:
        print(C("  ⚠  Rule: No adjustments before 9:30 AM (opening noise).",YELLOW))
    if now_t.hour*60+now_t.minute>14*60+45:
        print(C("  ⚠  Rule: Late session — increased gamma/spread risk.",YELLOW))

    print(C("  ── Record Your Adjustment ──────────────────────────",CYAN))
    print("   R = Roll the spread (Adjustment 1)")
    print("   S = Convert to strangle (Adjustment 2)")
    print("   E = Emergency close of this spread")
    print("   X = Skip / monitor only")
    print()
    ch=input(C("  Action (R/S/E/X): ",CYAN)).strip().upper()

    if ch=="R":   _ic_roll_spread(s,spot,side)
    elif ch=="S": _ic_strangle_convert(s,spot,side)
    elif ch=="E": _ic_emergency_close(s,spot,side)
    elif ch=="X": print(C("  ↩  Monitoring only.",GRAY))
    else:         print(C("  ✗ Invalid.",RED))


def _ic_roll_spread(s,spot,side):
    """Roll threatened side 100-150 pts further (Adjustment 1)."""
    up=(side=="upper")
    roll_key="call" if up else "put"
    rc=s.get("roll_count",{"call":0,"put":0})

    if rc.get(roll_key,0)>=1:
        print(C("  ❌  Rule 3: Max 1 roll per side. Must exit instead.",RED+BOLD)); return

    old_short_leg="short_call" if up else "short_put"
    old_long_leg ="long_call"  if up else "long_put"
    old_short=s["legs"].get(old_short_leg); old_long=s["legs"].get(old_long_leg)

    if not old_short or not old_long:
        print(C("  ✗ Leg data missing.",RED)); return

    print()
    print(C(f"  Rolling {'Call' if up else 'Put'} spread (Adjustment 1) — max 1 roll per side",YELLOW+BOLD))
    print(f"  Close: Short {'Call' if up else 'Put'} {old_short['strike']} + Long {'Call' if up else 'Put'} {old_long['strike']}")
    print()
    try:
        close_short=float(input(C(f"  Buyback price for Short {'Call' if up else 'Put'} {old_short['strike']} : Rs ",CYAN)).strip())
        close_long =float(input(C(f"  Sell price for Long {'Call' if up else 'Put'} {old_long['strike']}  : Rs ",CYAN)).strip())
    except: print(C("  ✗ Invalid.",RED)); return

    debit=close_short-close_long
    if debit>500: print(C(f"  ⚠  Roll costs Rs{debit:.0f} — rule says max Rs500 debit.",YELLOW))

    if up:
        new_sc_sugg=round((old_short["strike"]+150)/50)*50
        new_lc_sugg=new_sc_sugg+200
    else:
        new_sp_sugg=round((old_short["strike"]-150)/50)*50
        new_lp_sugg=new_sp_sugg-200

    print()
    try:
        if up:
            new_s=int(input(C(f"  New Short Call strike [{new_sc_sugg}]: ",CYAN)).strip() or str(new_sc_sugg))
            new_l=int(input(C(f"  New Long  Call strike [{new_lc_sugg}]: ",CYAN)).strip() or str(new_lc_sugg))
            new_sp_val=float(input(C(f"  New Short Call {new_s} premium collected : Rs ",CYAN)).strip())
            new_lp_val=float(input(C(f"  New Long  Call {new_l} premium paid      : Rs ",CYAN)).strip())
        else:
            new_s=int(input(C(f"  New Short Put strike [{new_sp_sugg}]: ",CYAN)).strip() or str(new_sp_sugg))
            new_l=int(input(C(f"  New Long  Put strike [{new_lp_sugg}]: ",CYAN)).strip() or str(new_lp_sugg))
            new_sp_val=float(input(C(f"  New Short Put {new_s} premium collected : Rs ",CYAN)).strip())
            new_lp_val=float(input(C(f"  New Long Put {new_l} premium paid      : Rs ",CYAN)).strip())
    except: print(C("  ✗ Invalid.",RED)); return

    # close old legs
    old_short["closed"]=True; old_short["close_premium"]=close_short
    old_long["closed"] =True; old_long["close_premium"] =close_long

    # add new legs as adjustments
    new_short_key="short_call" if up else "short_put"
    new_long_key ="long_call"  if up else "long_put"
    s["legs"][new_short_key]={"strike":new_s,"type":"CE" if up else "PE","action":"sell",
                               "lots":old_short["lots"],"entry_premium":new_sp_val,"current_premium":new_sp_val,"rolled":True}
    s["legs"][new_long_key] ={"strike":new_l,"type":"CE" if up else "PE","action":"buy",
                               "lots":old_short["lots"],"entry_premium":new_lp_val,"current_premium":new_lp_val,"rolled":True}

    # update short strike reference
    if up: s["short_call"]=new_s
    else:  s["short_put"]=new_s

    # recalculate BE
    new_nc=s["net_credit"]+new_sp_val-new_lp_val-debit
    sc=s.get("short_call"); sp=s.get("short_put")
    s["net_credit"]=round(new_nc,2)
    s["upper_be"]=round(sc+new_nc,2) if sc else s["upper_be"]
    s["lower_be"]=round(sp-new_nc,2) if sp else s["lower_be"]

    rc[roll_key]=rc.get(roll_key,0)+1
    s["roll_count"]=rc
    log_event(s,f"IC Roll {side}: {old_short['strike']}→{new_s}, net_credit now Rs{new_nc:.1f}")
    save_state(s); draw_payoff_chart(s)
    print(C(f"  ✅  Roll done. New short {'call' if up else 'put'}={new_s}. {C('No more rolls on this side.',RED)}",GREEN))


def _ic_strangle_convert(s,spot,side):
    """Adjustment 2 — convert to strangle."""
    up=(side=="upper")
    opp_leg="long_put" if up else "long_call"
    opp=s["legs"].get(opp_leg)
    if not opp:
        print(C("  ✗ Opposite long leg missing.",RED)); return
    print()
    print(C("  Strangle Conversion (Adjustment 2):",YELLOW+BOLD))
    print(f"  Closing opposite Long {'Put' if up else 'Call'} {opp['strike']} to collect credit.")
    print(C("  ⚠  HARD STOP: if spot moves 100 pts more against you → close ALL.",RED))
    try:
        close_p=float(input(C(f"  Sell price for Long {'Put' if up else 'Call'} {opp['strike']}: Rs ",CYAN)).strip())
    except: print(C("  ✗ Invalid.",RED)); return
    opp["closed"]=True; opp["close_premium"]=close_p
    log_event(s,f"IC Strangle convert: closed {opp_leg} {opp['strike']} @ Rs{close_p:.0f}")
    save_state(s); draw_payoff_chart(s)
    print(C("  ✅  Strangle conversion done. Monitor CLOSELY.",GREEN))


def _ic_emergency_close(s,spot,side):
    """Emergency close of one spread — both legs together (Rule 9)."""
    up=(side=="upper")
    print()
    print(C("  ── Emergency Close (Rule 2 + Rule 9) ──────────────",RED+BOLD))
    print(C("  Close BOTH legs of the threatened spread together.",RED))
    short_key="short_call" if up else "short_put"
    long_key ="long_call"  if up else "long_put"
    short=s["legs"].get(short_key); lng=s["legs"].get(long_key)
    if not short: print(C("  ✗ No leg data.",RED)); return
    print(f"  Short {'Call' if up else 'Put'} {short['strike']} current premium: Rs{short['current_premium']:.1f}")
    if lng: print(f"  Long  {'Call' if up else 'Put'} {lng['strike']}  current premium: Rs{lng['current_premium']:.1f}")
    try:
        cp_s=float(input(C(f"  Buyback Short {'Call' if up else 'Put'} {short['strike']} at Rs: ",CYAN)).strip())
        cp_l=float(input(C(f"  Sell Long {'Call' if up else 'Put'} {lng['strike'] if lng else '?'} at Rs: ",CYAN)).strip()) if lng else 0
    except: print(C("  ✗ Invalid.",RED)); return

    short["closed"]=True; short["close_premium"]=cp_s
    if lng: lng["closed"]=True; lng["close_premium"]=cp_l

    spread_loss=short["entry_premium"]-cp_s + (cp_l-lng["entry_premium"] if lng else 0)
    pc=GREEN if spread_loss>=0 else RED
    print(C(f"  Spread P&L: Rs {spread_loss:.1f}",pc))

    # check if other side still has value
    opp_short="short_put" if up else "short_call"
    opp_short_leg=s["legs"].get(opp_short)
    if opp_short_leg and not opp_short_leg.get("closed"):
        print()
        opp_pnl=opp_short_leg["entry_premium"]-opp_short_leg.get("current_premium",opp_short_leg["entry_premium"])
        print(C(f"  Opposite side ({opp_short}) P&L so far: Rs{opp_pnl:.1f}",GREEN if opp_pnl>=0 else RED))
        if input(C("  Close opposite side too? (y/n): ",CYAN)).strip().lower()=="y":
            _ic_close_other_side(s,spot,"put" if up else "call")

    log_event(s,f"IC Emergency close {'call' if up else 'put'} spread @ Rs{cp_s:.0f}/{cp_l:.0f}")
    save_state(s); draw_payoff_chart(s)
    print(C("  ✅  Spread closed. Analyse and re-enter next week.",GREEN))


def _ic_close_other_side(s,spot,which):
    sk="short_call" if which=="call" else "short_put"
    lk="long_call"  if which=="call" else "long_put"
    sl=s["legs"].get(sk); ll=s["legs"].get(lk)
    try:
        if sl and not sl.get("closed"):
            cp=float(input(C(f"  Buyback Short {'Call' if which=='call' else 'Put'} {sl['strike']}: Rs ",CYAN)).strip())
            sl["closed"]=True; sl["close_premium"]=cp
        if ll and not ll.get("closed"):
            cp=float(input(C(f"  Sell Long {'Call' if which=='call' else 'Put'} {ll['strike']}: Rs ",CYAN)).strip())
            ll["closed"]=True; ll["close_premium"]=cp
    except: pass
    log_event(s,f"IC other side {which} closed")


def guide_ic_exit(s,spot,reason=""):
    """Profit or loss exit guidance (Section 7)."""
    print()
    print(C("  ── Exit Guidance (Section 7) ──────────────────────",CYAN+BOLD))
    nc=s["net_credit"]
    pnl=update_live_premiums(s,spot)
    pct=round(pnl/nc*100,1) if nc else 0
    pc=GREEN if pnl>=0 else RED
    print(f"  Current P&L  : {C(f'Rs {pnl:.1f}  ({pct:+.1f}% of credit)',pc)}")
    print(f"  Net credit   : Rs {nc:.1f}")
    print()
    if reason: print(C(f"  Reason: {reason}",YELLOW)); print()

    if pnl>=nc*0.80:
        print(C("  🎯  80%+ of credit captured — EXIT WITHOUT QUESTION. Rule 6.",GREEN+BOLD))
    elif pnl>=nc*0.65:
        print(C("  ✅  65-80% captured — RECOMMENDED EXIT window. Rule 6.",GREEN))
    elif pnl>=nc*0.50:
        print(C("  ⚠  50% captured — optional exit if market is stable and >7 DTE.",YELLOW))
    elif pnl<=-2*nc:
        print(C("  🚨  HARD STOP HIT (2x credit). MANDATORY FULL CLOSE. No exceptions.",RED+BOLD))
    elif pnl<=-1.5*nc:
        print(C("  🚨  1.5x credit loss — ADJUST per Section 6 or close.",RED))
    elif pnl<=-1.0*nc:
        print(C("  ⚠  1x credit loss — yellow alert. Monitor closely, consider hedge.",YELLOW))

    print()
    print("  Exit options:")
    print("   F = Full position close (both sides)")
    print("   P = Partial close (profitable side only)")
    print("   K = Keep / no action")
    print()
    ch=input(C("  Action (F/P/K): ",CYAN)).strip().upper()
    if ch=="F": _ic_full_close(s,spot)
    elif ch=="P": _ic_partial_close(s,spot)
    elif ch=="K": print(C("  ↩  Keeping position.",GRAY))
    else: print(C("  ✗ Invalid.",RED))


def _ic_full_close(s,spot):
    print()
    print(C("  ── Full Position Close ────────────────────────────",RED+BOLD))
    print(C("  Rule 9: Close both legs of each spread together.",YELLOW))
    total_pnl=0.0
    for key,leg in s["legs"].items():
        if leg and not leg.get("closed"):
            try:
                cp=float(input(C(f"  {'Buyback' if leg['action']=='sell' else 'Sell'} {leg['type']} {leg['strike']}: Rs ",CYAN)).strip())
                p=(leg["entry_premium"]-cp) if leg["action"]=="sell" else (cp-leg["entry_premium"])
                leg["closed"]=True; leg["close_premium"]=cp; total_pnl+=p
            except: pass
    pc=GREEN if total_pnl>=0 else RED
    print(C(f"\n  Total P&L / lot : Rs {total_pnl:.1f}",pc))
    print(C(f"  Total P&L (all lots): Rs {total_pnl*s['lot_size']:.0f}",pc))
    log_event(s,f"IC Full close. P&L Rs{total_pnl:.1f}/lot")
    s["phase"]="CLOSED"; save_state(s)
    archive_trade(s)
    STATE_FILE.unlink(missing_ok=True)
    print(C("  ✅  Position fully closed and archived.",GREEN))
    deploy_new_trade()


def _ic_partial_close(s,spot):
    open_legs=[(k,v) for k,v in s["legs"].items() if v and not v.get("closed")]
    for i,(k,leg) in enumerate(open_legs,1):
        pc2=GREEN if leg["action"]=="buy" else RED
        print(f"  {i}. {k} — {leg['type']} {leg['strike']} @ Rs{leg['current_premium']:.1f}  {C(leg['action'],pc2)}")
    try:
        sel=int(input(C("  Select leg to close (number): ",CYAN)).strip())-1
        k,leg=open_legs[sel]
        cp=float(input(C(f"  Close price Rs: ",CYAN)).strip())
        leg["closed"]=True; leg["close_premium"]=cp
        log_event(s,f"IC Partial close: {k} {leg['strike']} @ Rs{cp:.0f}")
        save_state(s); draw_payoff_chart(s)
        print(C("  ✅  Leg closed.",GREEN))
    except: print(C("  ✗ Invalid.",RED))


# ═══════════════════════════════════════════════════════════════════
#  IRON FLY — ADJUSTMENT GUIDANCE  (original rule book)
# ═══════════════════════════════════════════════════════════════════
def guide_if_breach(s,spot,side):
    up=(side=="upper")
    side_label="UPPER — CALL SIDE" if up else "LOWER — PUT SIDE"
    print()
    print(C(f"  ╔{'═'*54}╗",RED+BOLD))
    print(C(f"  ║  ⚠  {side_label} BREAKEVEN BREACH{' '*(54-len(side_label)-21)}║",RED+BOLD))
    print(C(f"  ╚{'═'*54}╝",RED+BOLD))
    print()

    rc=s.get("roll_count",{"call":0,"put":0})
    if rc.get("call" if up else "put",0)>=2:
        print(C("  ❌  Max 2 opposite-side sells used — STOP adjusting.",RED+BOLD))
        guide_if_worst_case(s,spot); return

    atm_p=(s["legs"].get("short_ce",{}).get("entry_premium",100)+s["legs"].get("short_pe",{}).get("entry_premium",100))/2
    hd_lo=max(200,round(atm_p*1.5)); hd_hi=min(400,max(round(atm_p*2.0),hd_lo+50))

    if up:
        h_lo=round((spot+hd_lo)/50)*50; h_hi=round((spot+hd_hi)/50)*50
        safe_s=round((spot-50)/50)*50; hedge_label="BUY CE HEDGE"; opp="SELL PE"; opp_t="PE"
    else:
        h_lo=round((spot-hd_hi)/50)*50; h_hi=round((spot-hd_lo)/50)*50
        safe_s=round((spot+50)/50)*50; hedge_label="BUY PE HEDGE"; opp="SELL CE"; opp_t="CE"

    max_hc=s["legs"].get("short_pe" if up else "short_ce",{}).get("entry_premium",100)*0.30
    print(C("  Rule Book Actions:",YELLOW+BOLD)); print()
    print(f"  {C('STEP 1 — '+hedge_label,CYAN)}")
    print(f"  Ideal range: {C(str(h_lo),GREEN)} – {C(str(h_hi),GREEN)}   Max cost: Rs{max_hc:.0f}  [Rule H2]")
    print()
    print(f"  {C('STEP 2 — '+opp+' (safe side)',CYAN)}")
    print(f"  Sell near: {C(str(safe_s),GREEN)} {opp_t}  — 1 lot only  [Rule O4]")
    print()
    now_t=datetime.now()
    if now_t.hour*60+now_t.minute>14*60+45: print(C("  ⚠  Past 2:45 PM — NO sell allowed.",RED))
    print()
    print("   H=Hedge buy  S=Opp sell  R=Roll hedge  X=Skip")
    ch=input(C("  Action: ",CYAN)).strip().upper()
    if ch=="H":  _if_record_hedge(s,spot,side)
    elif ch=="S": _if_record_opp_sell(s,spot,side)
    elif ch=="R": _if_record_roll(s,spot,side)
    elif ch=="X": print(C("  ↩  No action.",GRAY))


def _if_record_hedge(s,spot,side):
    try:
        strike=int(input(C("  Hedge strike: ",CYAN)).strip())
        prem=float(input(C("  Premium paid Rs: ",CYAN)).strip())
    except: print(C("  ✗ Invalid.",RED)); return
    dist=abs(strike-spot)
    if dist<150: print(C(f"  ⚠  Rule H3: {dist:.0f} pts < 150 min!",RED))
    ot="CE" if side=="upper" else "PE"
    s["adjustments"].append({"type":f"Hedge {ot}","strike":strike,"action":"buy",
                              "entry_premium":prem,"current_premium":prem,"lots":1,
                              "opt_type":ot,"closed":False,"ts":datetime.now().isoformat()})
    log_event(s,f"IF Hedge {ot} {strike} @ Rs{prem:.0f}")
    save_state(s); draw_payoff_chart(s)
    print(C(f"  ✅  Hedge recorded.",GREEN))


def _if_record_opp_sell(s,spot,side):
    rc=s.get("roll_count",{"call":0,"put":0})
    key="call" if side=="upper" else "put"
    if rc.get(key,0)>=2:
        print(C("  ❌  Max 2 sells on this side.",RED+BOLD)); return
    now_t=datetime.now()
    if now_t.hour*60+now_t.minute>14*60+45:
        print(C("  ❌  Past 2:45 PM. No sell.",RED+BOLD)); return
    ot="PE" if side=="upper" else "CE"
    try:
        strike=int(input(C(f"  Strike ({ot}): ",CYAN)).strip())
        prem=float(input(C("  Premium Rs: ",CYAN)).strip())
    except: print(C("  ✗ Invalid.",RED)); return
    s["adjustments"].append({"type":f"OppSell {ot}","strike":strike,"action":"sell",
                              "entry_premium":prem,"current_premium":prem,"lots":1,
                              "opt_type":ot,"closed":False,"ts":datetime.now().isoformat()})
    rc[key]=rc.get(key,0)+1; s["roll_count"]=rc
    log_event(s,f"IF OppSell {ot} {strike} @ Rs{prem:.0f}  [{rc[key]}/2]")
    save_state(s); draw_payoff_chart(s)
    print(C(f"  ✅  Sell recorded [{rc[key]}/2].",GREEN))


def _if_record_roll(s,spot,side):
    ot="CE" if side=="upper" else "PE"
    try:
        old_s=int(input(C("  Old hedge strike: ",CYAN)).strip())
        old_v=float(input(C("  Current value Rs: ",CYAN)).strip())
        new_s=int(input(C("  New hedge strike: ",CYAN)).strip())
        new_p=float(input(C("  New premium Rs: ",CYAN)).strip())
    except: print(C("  ✗ Invalid.",RED)); return
    if abs(new_s-spot)<150:
        print(C(f"  ❌  New hedge < 150 pts from spot!",RED)); return
    for adj in reversed(s["adjustments"]):
        if adj.get("opt_type")==ot and not adj.get("closed") and adj["action"]=="buy":
            adj["closed"]=True; adj["close_premium"]=old_v; break
    s["adjustments"].append({"type":f"Rolled {ot}","strike":new_s,"action":"buy",
                              "entry_premium":new_p,"current_premium":new_p,"lots":1,
                              "opt_type":ot,"closed":False,"ts":datetime.now().isoformat()})
    log_event(s,f"IF Roll {ot} {old_s}→{new_s}")
    save_state(s); draw_payoff_chart(s)
    print(C(f"  ✅  Roll done.",GREEN))


def guide_if_worst_case(s,spot):
    print()
    print(C("  ══ WORST CASE PROTOCOL ══════════════════════════════",RED+BOLD))
    nc=s["net_credit"]; pnl=update_live_premiums(s,spot)
    print(f"  P&L/lot: {C(f'Rs {pnl:.0f}',RED)}   Max loss: {C(f'Rs {-2*nc:.0f}',YELLOW)}")
    if pnl<=-2*nc:
        print(C("  🚨  RULE F4: Max loss hit. FULL EXIT NOW.",RED+BOLD))
    print(); print("   F=Full exit  K=Keep"); print()
    if input(C("  Action (F/K): ",CYAN)).strip().upper()=="F":
        try:
            ep=float(input(C("  Final P&L Rs: ",CYAN)).strip())
        except: ep=0
        s["phase"]="CLOSED"; log_event(s,f"IF Full exit. P&L Rs{ep:.0f}")
        save_state(s); archive_trade(s)
        STATE_FILE.unlink(missing_ok=True)
        print(C("  ✅  Closed and archived.",GREEN))
        deploy_new_trade()


# ═══════════════════════════════════════════════════════════════════
#  REVERSAL  (shared)
# ═══════════════════════════════════════════════════════════════════
def guide_reversal(s,spot):
    strat=s.get("strategy","ironfly")
    print(); print(C("  ── REVERSAL: 2 candles back inside range ─────────",GREEN+BOLD))
    if strat=="bwb":
        print(f"  BWB Scenario A: Market grinding back to bias — hold, theta+delta working.")
        print(f"  Scenario B: If back in safe zone → consider partial profit booking.")
        return
    if strat=="butterfly":
        print(f"  MG-01: If price back inside range → DO NOTHING. Theta works.")
        print(f"  MG-03: If 30-50% profit → start No-Loss conversion.")
        return
    if strat=="ironcondor":
        print(f"  Section 7: At 50% profit → consider exiting the adjustment.")
        print(f"  Rule 9: Close both legs of a spread together.")
        guide_ic_exit(s,spot,"Reversal detected")
    else:
        print(f"  T2: Close OPPOSITE SELL first.  T3: Keep hedge until 50+ pts inside.")
        open_adjs=[(i,a) for i,a in enumerate(s["adjustments"]) if not a.get("closed")]
        if not open_adjs: print(C("  No open adjustments.",GRAY)); return
        if input(C("  Close an adjustment? (y/n): ",CYAN)).strip().lower()!="y": return
        for i,(idx,adj) in enumerate(open_adjs):
            print(f"  {i+1}. {adj['type']} {adj['strike']} @ Rs{adj['entry_premium']:.0f}")
        try:
            sel=int(input(C("  Select: ",CYAN)).strip())-1
            idx,adj=open_adjs[sel]
            cp=float(input(C("  Close price Rs: ",CYAN)).strip())
            adj["closed"]=True; adj["close_premium"]=cp
            log_event(s,f"IF Reversal close: {adj['type']} {adj['strike']} @ Rs{cp:.0f}")
            save_state(s); draw_payoff_chart(s)
        except: print(C("  ✗ Invalid.",RED))



# ═══════════════════════════════════════════════════════════════════
#  AUTO 50% PROFIT TRIGGER ON ADJUSTMENT SELLS  [Rule T4 / Iron Fly Rule Book]
#  When an opposite-side sell reaches 50% of entry premium → auto-prompt close
# ═══════════════════════════════════════════════════════════════════
def check_adj_50pct_trigger(s, spot):
    """
    Scans all open adjustment SELL legs.
    If current_premium <= 50% of entry_premium → fires auto-prompt.
    Rule T4: Sold PE at Rs120 → exit at Rs60.
    """
    for i, adj in enumerate(s.get("adjustments", []), 1):
        if adj.get("closed"): continue
        if adj.get("action") != "sell": continue
        ep   = adj.get("entry_premium", 0)
        cp   = adj.get("current_premium", ep)
        if ep <= 0: continue
        pct_remaining = cp / ep * 100

        if pct_remaining <= 50:
            print()
            print(C("  ╔══════════════════════════════════════════════════════╗", GREEN+BOLD))
            print(C(f"  ║  🎯  ADJ SELL #{i} HIT 50% PROFIT TARGET  [Rule T4]  ║", GREEN+BOLD))
            print(C("  ╚══════════════════════════════════════════════════════╝", GREEN+BOLD))
            print()
            print(f"  Adjustment : {adj['type']}  {adj.get('opt_type','')} {adj['strike']}")
            print(f"  Entry prem : Rs {ep:.1f}")
            print(f"  Current    : Rs {cp:.1f}  ({100-pct_remaining:.0f}% profit captured)")
            print(C("  Rule T4: Exit at 50% — do not wait for zero.", YELLOW+BOLD))
            print()
            print("   C = Close this adjustment now (recommended)")
            print("   K = Keep holding")
            print("   E = Emergency exit ENTIRE position")
            print()
            try:
                ch = input(C("  Action (C/K/E): ", CYAN)).strip().upper()
            except: ch = "K"

            if ch == "C":
                try:
                    close_p = float(input(C(f"  Close price for {adj['type']} {adj['strike']} Rs: ", CYAN)).strip())
                    adj["closed"]       = True
                    adj["close_premium"] = close_p
                    realized = ep - close_p
                    log_event(s, f"Adj sell closed at 50% target: {adj['type']} {adj['strike']} "
                                 f"entry Rs{ep:.0f} close Rs{close_p:.0f} profit Rs{realized:.0f}")
                    save_state(s)
                    draw_payoff_chart(s)
                    print(C(f"  ✅  Adjustment closed. Profit Rs{realized:.1f}", GREEN))
                except:
                    print(C("  ✗ Invalid.", RED))

            elif ch == "E":
                emergency_exit_market(s)

            # else K — keep holding, continue scan


def emergency_adj_exit(s, spot):
    """
    Emergency close of ALL open adjustment legs only (keeps original position).
    Use when adjustments are losing but original structure is still intact.
    """
    hdr("⚡  EMERGENCY ADJUSTMENT EXIT")
    print(C("  Closing all open adjustment legs. Original position kept.", YELLOW+BOLD))
    print()

    open_adjs = [(i, a) for i, a in enumerate(s.get("adjustments", []), 1)
                 if not a.get("closed")]

    if not open_adjs:
        print(C("  No open adjustments to close.", GRAY)); return

    total_adj_pnl = 0.0
    for i, adj in open_adjs:
        est = adj.get("current_premium", adj["entry_premium"])
        print(f"  #{i}  {adj['type']:20}  {adj.get('opt_type','')} {adj['strike']}"
              f"  Entry Rs{adj['entry_premium']:.1f}  Est Rs{est:.1f}")
        try:
            mp = float(input(C("  Market price Rs [ENTER=estimate]: ", RED)).strip() or str(round(est,1)))
        except: mp = est

        adj["closed"]        = True
        adj["close_premium"] = mp
        pnl_adj = (adj["entry_premium"] - mp) if adj["action"] == "sell" else (mp - adj["entry_premium"])
        total_adj_pnl += pnl_adj
        pc = GREEN if pnl_adj >= 0 else RED
        print(C(f"  Adj P&L: Rs {pnl_adj:+.1f}", pc)); print()

    log_event(s, f"Emergency adj exit. Adj P&L Rs{total_adj_pnl:.1f}")
    save_state(s)
    draw_payoff_chart(s)
    pc = GREEN if total_adj_pnl >= 0 else RED
    print(C(f"  ✅  All adjustments closed. Total adj P&L: Rs {total_adj_pnl:+.1f}", pc))
    print(C("  Original position continues. Monitor carefully.", YELLOW))



# ═══════════════════════════════════════════════════════════════════
#  LIVE OPTION PRICE FETCHER  (Yahoo Finance — 15min delayed)
# ═══════════════════════════════════════════════════════════════════
def fetch_live_option_prices(s, spot):
    """
    Fetch current option premiums from Yahoo Finance option chain.
    NOTE: Yahoo Finance does NOT provide reliable data for Indian NIFTY options.
    This function is DISABLED and prices are kept at entry levels.
    
    FOR LIVE TRADING: Use broker_api_handler.py with Zerodha/Angel/Upstox
    OR manually update prices using: update_premiums_manual(s)
    """
    # Yahoo Finance doesn't support Indian options — disabled
    pass


def update_premiums_manual(s):
    """
    MANUAL PRICE UPDATE for live trading.
    When live prices differ from entry, user can update them manually.
    Call this during monitoring to sync with broker's live prices.
    
    Usage: During live monitor, press P to update prices
    """
    print()
    print(C("  ── Manual Premium Update ──────────────────────────", CYAN+BOLD))
    print(C("  Enter CURRENT market prices for each leg (from your broker)", YELLOW))
    print()
    
    for leg_name, leg in s.get("legs", {}).items():
        if not leg or leg.get("closed"):
            continue
        
        strike = leg["strike"]
        opt_type = leg["type"]
        action = leg["action"]
        entry_p = leg["entry_premium"]
        current_p = leg.get("current_premium", entry_p)
        leg_exp = _format_expiry(_leg_expiry(s, leg))

        label = f"{action.upper()} {opt_type} {strike} ({leg_exp})"
        col = RED if action == "sell" else GREEN
        
        print(f"  {C(label, col)}")
        print(f"    Entry: Rs {entry_p:.2f}  |  Last updated: Rs {current_p:.2f}")
        
        try:
            new_p = float(input(C(f"    Current market price Rs [press ENTER to keep {current_p}]: ", CYAN)).strip() or str(current_p))
            if new_p > 0:
                leg["current_premium"] = round(new_p, 2)
                change = new_p - entry_p
                col2 = GREEN if (action=="sell" and change<0) or (action=="buy" and change>0) else RED
                print(C(f"    ✓ Updated to Rs {new_p:.2f}  ({change:+.2f})", col2))
        except:
            print(C("    ✗ Invalid input — keeping Rs {current_p:.2f}", YELLOW))
        print()
    
    log_event(s, "Manual price update completed")
    save_state(s)
    print(C("  ✅ Prices updated. Continue monitoring.", GREEN))



# ═══════════════════════════════════════════════════════════════════
#  LIVE MONITORING LOOP  (shared)
# ═══════════════════════════════════════════════════════════════════
def live_monitor_loop(s, v54_config=None):
    """
    Live monitoring loop with v5.4 modules integration.
    
    Args:
        s: Trading state dict
        v54_config: v5.4 configuration with broker, dashboard, alerts, analytics
    """
    if v54_config is None:
        v54_config = {}
    
    POLL=15*60; strat=s.get("strategy","ironfly")

    _emergency_flag.clear()

    def on_ctrl_c(sig,frame):
        print(); print(C("\n  CTRL+C — saving state…",YELLOW))
        save_state(s)
        
        # v5.4: Log final trade if analytics enabled
        if HAS_ANALYTICS and v54_config.get('analytics'):
            try:
                log = v54_config['analytics']['log']
                if s.get('phase') == 'LIVE':
                    log.add_trade({
                        'id': f"{strat}_{datetime.now().isoformat()}",
                        'strategy': strat.replace('_', ' ').title(),
                        'entry_date': s.get('entry_time', '').split('T')[0] if s.get('entry_time') else str(datetime.now().date()),
                        'entry_time': s.get('entry_time', '').split('T')[1][:8] if s.get('entry_time') else '',
                        'exit_date': str(datetime.now().date()),
                        'exit_time': datetime.now().strftime('%H:%M:%S'),
                        'entry_price': s.get('net_credit', 0),
                        'exit_price': s.get('current_spot', 0),
                        'position_size': 1,
                        'status': 'LIVE_EXIT',
                    })
            except Exception as e:
                print(C(f"  ⚠️   Analytics log failed: {e}", YELLOW))
        
        print(C(f"  Saved to {STATE_FILE}  (run again to resume)",GREEN))
        sys.exit(0)
    signal.signal(signal.SIGINT,on_ctrl_c)

    # start CTRL+E watcher thread (Unix only)
    try:
        t=threading.Thread(target=_watch_ctrl_e,daemon=True); t.start()
    except Exception: pass

    iteration=0
    _entry_logged = False  # Track if entry has been logged
    
    while True:
        iteration+=1
        now=datetime.now()
        mkt_open=(9*60+15)<=(now.hour*60+now.minute)<=(15*60+30)
        
        # v5.4: Log trade entry on first iteration
        if not _entry_logged and s.get('phase') == 'LIVE' and HAS_ANALYTICS:
            try:
                from performance_analytics import TradeLog
                log = TradeLog()
                lots = 1
                for leg in s.get("legs", {}).values():
                    if leg:
                        lots = leg.get("lots", 1)
                        break
                log.add_trade({
                    'id': f"{strat}_{s.get('entry_time', datetime.now().isoformat())}",
                    'strategy': strat.replace('_', ' ').title(),
                    'entry_date': s.get('entry_time', '').split('T')[0] if s.get('entry_time') else str(datetime.now().date()),
                    'entry_time': s.get('entry_time', '').split('T')[1][:8] if s.get('entry_time') else '',
                    'entry_price': s.get('net_credit', 0),
                    'position_size': lots,
                    'status': 'ENTRY',
                })
                _entry_logged = True
            except Exception as e:
                log_event(s, f"Entry logging failed: {e}")

        print(); print(C(f"  ── Scan #{iteration}  {now.strftime('%Y-%m-%d %H:%M:%S')} ──────────",CYAN))

        # ═══ v5.4: Use broker API if available, fallback to Yahoo Finance ═══
        if v54_config.get('broker'):
            try:
                spot = v54_config['broker'].fetch_spot_price()
                if spot:
                    print(C(f"  📡  Live spot (via {v54_config['broker'].broker}): {spot}", CYAN))
            except Exception:
                spot = fetch_spot(s.get("symbol","^NSEI"))
        else:
            spot=fetch_spot(s.get("symbol","^NSEI"))
        
        if spot is None:
            print(C("  ⚠  Spot fetch failed.",YELLOW))
            
            # v5.4: Send alert on data failure
            if HAS_ALERTS and v54_config.get('alerts'):
                try:
                    v54_config['alerts'].send_warning_alert(
                        f"[{strat}] Spot price fetch failed at {now.strftime('%H:%M:%S')}"
                    )
                except Exception:
                    pass
            
            _tick_sleep(60 if mkt_open else 300); continue

        s["current_spot"]=spot
        # ── Fetch live option prices from Yahoo Finance or Broker API ──────────
        fetch_live_option_prices(s, spot)
        lots_scan=1
        for _lg in s.get("legs",{}).values():
            if _lg: lots_scan=_lg.get("lots",1); break
        candles=fetch_15min_candles(s.get("symbol","^NSEI"))

        # ── LIVE P&L — update current premiums via BS ─────────────
        pnl=update_live_premiums(s,spot)
        nc=s["net_credit"]
        pc=GREEN if pnl>=0 else RED
        pct=round(pnl/nc*100,1) if nc else 0

        print_broker_table(s, spot, pnl, pct, lots_scan, strat)

        # ═══ v5.4: Update Google Sheets Dashboard ═══
        if HAS_SHEETS and v54_config.get('dashboard'):
            try:
                v54_config['dashboard'].sync_now()
            except Exception as e:
                pass  # Silent fail - don't interrupt monitoring

        # ── strategy-specific alerts ───────────────────────────────
        if strat=="calendar":
            nc2=s.get("net_premium",0)
            if pnl>=nc2*0.40:
                print(C(f"\n  🎯  Calendar: 40-50% profit — book exit recommended",GREEN+BOLD))
                if HAS_ALERTS and v54_config.get('alerts'):
                    try:
                        v54_config['alerts'].send_profit_target_alert(
                            strategy=strat,
                            pnl=pnl,
                            target_pct=40,
                            spot=spot
                        )
                    except Exception:
                        pass
            if pnl<=-nc2:
                print(C(f"\n  ⚠  Calendar: Loss at 1x credit — review adjustment",YELLOW+BOLD))
                if HAS_ALERTS and v54_config.get('alerts'):
                    try:
                        v54_config['alerts'].send_stop_loss_alert(
                            strategy=strat,
                            pnl=pnl,
                            spot=spot
                        )
                    except Exception:
                        pass
            vc_now=s.get("vix_current",17)
            vc_entry=s.get("cal_vix_entry",17)
            if vc_now>vc_entry*1.3: print(C(f"\n  📈  Calendar: VIX spiked {round((vc_now/vc_entry-1)*100)}% — monitor near leg",YELLOW))

        if strat=="strangle":
            nc2=s.get("net_credit",0); stop_loss=nc2*3
            if pnl>=nc2*0.50:
                print(C(f"\n  🎯  Strangle: 50% profit target — EXIT RECOMMENDED",GREEN+BOLD))
                if HAS_ALERTS and v54_config.get('alerts'):
                    try:
                        v54_config['alerts'].send_profit_target_alert(
                            strategy=strat,
                            pnl=pnl,
                            target_pct=50,
                            spot=spot
                        )
                    except Exception:
                        pass
            elif pnl>=nc2*0.40:
                print(C(f"\n  ✅  Strangle: 40% profit — close eligible",GREEN))
            if pnl<=-stop_loss:
                print(C(f"\n  🚨  HARD STOP: 3x credit loss — CLOSE IMMEDIATELY",RED+BOLD))
                if HAS_ALERTS and v54_config.get('alerts'):
                    try:
                        v54_config['alerts'].send_stop_loss_alert(
                            strategy=strat,
                            pnl=pnl,
                            spot=spot,
                            severity='CRITICAL'
                        )
                    except Exception:
                        pass
            put_s=s.get("short_put",0); call_s=s.get("short_call",0)
            if put_s: print(f"  Short Put {put_s}: {C(str(round(abs(spot-put_s),0))+' pts away',YELLOW if abs(spot-put_s)<80 else GRAY)}")
            if call_s: print(f"  Short Call {call_s}: {C(str(round(abs(spot-call_s),0))+' pts away',YELLOW if abs(spot-call_s)<80 else GRAY)}")

        if strat=="bwb":
            nc2=s.get("net_credit",0); mp=s.get("bwb_max_profit",nc2*4)
            profit_pct=round(pnl/mp*100,1) if mp else 0
            if pnl>=mp*0.70:    print(C(f"\n  🎯  BWB: 70%+ of max profit — EXIT (Golden Rule 4)",GREEN+BOLD))
            elif pnl>=mp*0.50:  print(C(f"\n  ✅  BWB: 50% profit — book partial now (Scenario A)",GREEN))
            elif pnl>=mp*0.30:  print(C(f"\n  📈  BWB: 30% profit — theta+delta working well",CYAN))
            if pnl<=-s.get("bwb_max_loss",nc2*2)*0.50: print(C(f"\n  ⚠  BWB: 50% of max loss — ADJUST NOW (Scenario C)",YELLOW+BOLD))
            if s.get("bwb_max_loss") and pnl<=-s["bwb_max_loss"]: print(C(f"\n  🚨  BWB: MAX LOSS HIT — HARD STOP (Section 13)",RED+BOLD))

        if strat=="butterfly":
            nc2=s.get("net_credit",0)
            if pnl>=nc2*2:      print(C("\n  🎯  Butterfly: 30-50% of max profit — No-Loss conversion! [MG-03]",GREEN+BOLD))
            elif pnl<=-nc2*0.8: print(C("\n  ⚠  Butterfly: Approaching max loss — monitor closely [MG-06]",YELLOW))

        if strat=="ironcondor":
            if pnl>=nc*0.80:   print(C("\n  🎯  Rule 6: 80%+ captured — EXIT RECOMMENDED",GREEN+BOLD))
            elif pnl>=nc*0.65: print(C("\n  ✅  Rule 6: 65% profit target reached",GREEN))
            if pnl<=-1.5*nc:   print(C("\n  🚨  1.5x loss — ADJUST or CLOSE (Section 7)",RED+BOLD))
            if pnl<=-2*nc:     print(C("\n  🚨  HARD STOP 2x credit — MANDATORY EXIT",RED+BOLD))

        if strat=="credit_spread":
            nc2=s.get("net_credit",0); sl_lvl=s.get("stop_loss_level",nc2*1.5)
            if pnl>=nc2*0.70:  print(C("\n  🎯  70% profit target — EXIT (Profit Rule 2)",GREEN+BOLD))
            elif pnl>=nc2*0.50:print(C("\n  ✅  50% profit target — consider exit (Profit Rule 1)",GREEN))
            if pnl<=-sl_lvl:   print(C("\n  🚨  STOP LOSS HIT 1.5x credit — EXIT NOW (Loss Rule 1)",RED+BOLD))

        if s.get("ratio"):
            zone=build_zone(s["ratio"],s.get("iv_rank",50))
            print(C(f"  IV/HV: {s['ratio']}  IVR: {s.get('iv_rank','-')}%  Zone: {zone['label'][:35]}",zone["color"]))

        s["pnl_snapshots"].append({"ts":now.isoformat(),"spot":spot,"pnl":pnl})

        if not mkt_open:
            print(C("  Market closed. Next check in 5 min.",GRAY))
            save_state(s); _tick_sleep(300); continue

        breach=check_breach(s,candles) if candles else None
        reversal=check_reversal(s,candles) if candles and len(candles)>=2 else False

        if breach=="upper_marginal":
            print(C("\n  ⚡ MARGINAL UPPER BREACH — awaiting confirmation.",YELLOW))
            
            # v5.4: Send warning alert
            if HAS_ALERTS and v54_config.get('alerts'):
                try:
                    v54_config['alerts'].send_breach_alert(
                        strategy=strat,
                        direction='UPPER',
                        severity='WARNING',
                        spot=spot,
                        be_level=s.get('upper_be')
                    )
                except Exception:
                    pass
        
        elif breach in ("upper_confirmed","upper_strong"):
            tag="STRONG" if breach=="upper_strong" else "CONFIRMED"
            print(C(f"\n  🚨 UPPER BREACH — {tag}",RED+BOLD))
            save_state(s)
            
            # v5.4: Send CRITICAL alert
            if HAS_ALERTS and v54_config.get('alerts'):
                try:
                    v54_config['alerts'].send_breach_alert(
                        strategy=strat,
                        direction='UPPER',
                        severity='CRITICAL' if breach=="upper_strong" else 'WARNING',
                        spot=spot,
                        be_level=s.get('upper_be'),
                        pnl=pnl
                    )
                except Exception:
                    pass
            
            if strat=="ironcondor":       guide_ic_breach(s,spot,"upper")
            elif strat=="butterfly":      guide_bf_breach(s,spot,"upper")
            elif strat=="credit_spread":  guide_cs_breach(s,spot,"upper")
            elif strat=="bwb":            guide_bwb_breach(s,spot,"upper")
            elif strat=="strangle":       guide_strangle_adj(s,spot,"upper")
            elif strat=="calendar":       guide_calendar_adj(s,spot,"upper")
            else:                         guide_if_breach(s,spot,"upper")
        
        elif breach=="lower_marginal":
            print(C("\n  ⚡ MARGINAL LOWER BREACH — awaiting confirmation.",YELLOW))
            
            # v5.4: Send warning alert
            if HAS_ALERTS and v54_config.get('alerts'):
                try:
                    v54_config['alerts'].send_breach_alert(
                        strategy=strat,
                        direction='LOWER',
                        severity='WARNING',
                        spot=spot,
                        be_level=s.get('lower_be')
                    )
                except Exception:
                    pass
        
        elif breach in ("lower_confirmed","lower_strong"):
            tag="STRONG" if breach=="lower_strong" else "CONFIRMED"
            print(C(f"\n  🚨 LOWER BREACH — {tag}",RED+BOLD))
            save_state(s)
            
            # v5.4: Send CRITICAL alert
            if HAS_ALERTS and v54_config.get('alerts'):
                try:
                    v54_config['alerts'].send_breach_alert(
                        strategy=strat,
                        direction='LOWER',
                        severity='CRITICAL' if breach=="lower_strong" else 'WARNING',
                        spot=spot,
                        be_level=s.get('lower_be'),
                        pnl=pnl
                    )
                except Exception:
                    pass
            
            if strat=="ironcondor":       guide_ic_breach(s,spot,"lower")
            elif strat=="butterfly":      guide_bf_breach(s,spot,"lower")
            elif strat=="credit_spread":  guide_cs_breach(s,spot,"lower")
            elif strat=="bwb":            guide_bwb_breach(s,spot,"lower")
            elif strat=="strangle":       guide_strangle_adj(s,spot,"lower")
            elif strat=="calendar":       guide_calendar_adj(s,spot,"lower")
            else:                         guide_if_breach(s,spot,"lower")
        elif breach=="near_breach":
            sold_strike=s.get("cal_sold_strike",0)
            dist=abs(spot-sold_strike) if sold_strike else 0
            print(C(f"\n  ⚠  Calendar: approaching breach zone — {dist:.0f}pts from sold strike {sold_strike}",YELLOW))
        elif breach=="near_upper":
            ube=s.get("upper_be") or (s.get("cal_near_call") if strat=="calendar" else None)
            if ube is not None:
                dist=ube-spot
                sc=s.get("short_call")
                sc_dist=sc-spot if sc else dist
                print(C(f"\n  ⚠  Approaching UPPER: {dist:.0f}pts to BE"+(f", {sc_dist:.0f}pts to Short Call {sc}" if sc else ""),YELLOW))
        elif breach=="near_lower":
            lbe=s.get("lower_be") or (s.get("cal_near_put") if strat=="calendar" else None)
            if lbe is not None:
                dist=spot-lbe
                sp2=s.get("short_put")
                sp_dist=spot-sp2 if sp2 else dist
                print(C(f"\n  ⚠  Approaching LOWER: {dist:.0f}pts to BE"+(f", {sp_dist:.0f}pts to Short Put {sp2}" if sp2 else ""),YELLOW))
        else:
            ube=s.get("upper_be") or (s.get("cal_near_call") if strat=="calendar" else None)
            lbe=s.get("lower_be") or (s.get("cal_near_put") if strat=="calendar" else None)
            if ube is not None and lbe is not None:
                print(C(f"  ✅ Safe  ↑{ube-spot:.0f}pts to upper  ↓{spot-lbe:.0f}pts to lower",GREEN))
            else:
                print(C("  ✅ Safe",GREEN))

        if reversal and s.get("adjustments"): guide_reversal(s,spot)

        # ── AUTO 50% profit trigger on adjustment sells [Rule T4 / Golden Rule] ─
        check_adj_50pct_trigger(s, spot)

        # IC: exit guidance triggers
        if strat=="ironcondor" and (pnl>=-2*nc):
            if pnl>=nc*0.65 or pnl<=-1.5*nc:
                if input(C("  Open exit menu? (y/n): ",CYAN)).strip().lower()=="y":
                    guide_ic_exit(s,spot)

        # IF: max loss
        if strat=="ironfly" and pnl<=-2*nc:
            print(C(f"\n  🚨 Max loss Rule F4 triggered!",RED+BOLD))
            guide_if_worst_case(s,spot)

        # draw updated chart every 4 scans
        if iteration%4==0:
            if strat == "calendar":
                draw_calendar_payoff_chart(s)
            else:
                draw_payoff_chart(s)

        save_state(s)

        # ── CTRL+E emergency exit check ──────────────────────────
        if _emergency_flag.is_set():
            emergency_exit_market(s)
            return

        # ── Expiry wizard trigger (Tuesday after 3 PM) ───────────
        now2=datetime.now()
        exp_today = s.get("expiry_date","")==""
        try: exp_today = (s.get("expiry_date","") == str(datetime.now().date()))
        except: pass
        if (now2.weekday()==1 or exp_today) and now2.hour>=15:
            print()
            print(C("  ⏰  EXPIRY DAY — 3 PM+",YELLOW+BOLD))
            print(C("  Time to close the position. Launch expiry wizard?",YELLOW))
            try:
                ch2=input(C("  Launch wizard (y/n) or E for emergency exit: ",CYAN)).strip().upper()
                if ch2=="Y":  expiry_close_wizard(s); return
                elif ch2=="E": emergency_exit_market(s); return
            except: pass

        print(C(f"\n  Waiting for next 15-min candle close… CTRL+C=save&exit  CTRL+E=emergency exit",GRAY))
        _wait_for_candle_close()


def _tick_sleep(sec):
    for _ in range(sec): time.sleep(1)


def _wait_for_candle_close():
    """
    Sleep until the NEXT 15-minute candle boundary closes.
    15-min candles close at :00, :15, :30, :45 each hour.
    Also wakes up every second so CTRL+C and CTRL+E remain responsive.
    """
    now = datetime.now()
    # seconds already elapsed in current 15-min block
    elapsed = (now.minute % 15) * 60 + now.second
    # seconds to wait until next candle close (add 5s buffer for data propagation)
    remaining = (15 * 60 - elapsed) + 5
    print(C(f"  ⏱  Next candle closes in {remaining//60}m {remaining%60}s  "
            f"(at {(now + timedelta(seconds=remaining)).strftime('%H:%M:%S')})", GRAY))
    _tick_sleep(remaining)

# ═══════════════════════════════════════════════════════════════════
#  ARCHIVE  — save closed trade to trade_archive/ folder
# ═══════════════════════════════════════════════════════════════════
def archive_trade(s):
    """Save a snapshot of the closed state to trade_archive/."""
    ARCHIVE_DIR.mkdir(exist_ok=True)
    dt    = (s.get("entry_time","") or datetime.now().isoformat())[:10]
    strat = s.get("strategy","unknown")
    idx   = 1
    while True:
        fname = ARCHIVE_DIR / f"trade_{dt}_{strat}_{idx:03d}.json"
        if not fname.exists(): break
        idx += 1
    s["archived_at"] = datetime.now().isoformat()
    with open(fname,"w") as f: json.dump(s,f,indent=2,default=str)
    print(C(f"  📁  Trade archived → {fname}",CYAN))


# ═══════════════════════════════════════════════════════════════════
#  EXPIRY CLOSE WIZARD
# ═══════════════════════════════════════════════════════════════════
def expiry_close_wizard(s):
    """
    Guided leg-by-leg close at expiry.
      W = Expired worthless  (premium = 0)
      B = Bought back        (enter actual buyback price)
    Calculates final P&L → archives → deploy_new_trade.
    """
    spot  = s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI")) or 0
    strat = s.get("strategy","ironfly")
    slabel= "Iron Fly" if strat=="ironfly" else "Iron Condor"
    hdr(f"EXPIRY CLOSE WIZARD — {slabel}")
    print(C("  Walk through each leg. All legs expire today.",YELLOW))
    print(C("  W = Expired worthless (no action, zero cost)",GRAY))
    print(C("  B = Bought back / sold out (enter actual price)",GRAY))
    print()

    total_pnl=0.0
    lots=1
    for leg in s["legs"].values():
        if leg: lots=leg.get("lots",1); break

    for key,leg in s["legs"].items():
        if not leg or leg.get("closed"): continue
        est=leg.get("current_premium",leg["entry_premium"])
        lbl=key.replace("_"," ").title()
        act="SELL" if leg["action"]=="sell" else "BUY"
        print(f"  {C(lbl,CYAN)}  {leg['type']} {leg['strike']}"
              f"  Entry Rs{leg['entry_premium']:.1f}  Live-est Rs{est:.1f}")
        print(f"  Your action at entry: {C(act, RED if leg['action']=='sell' else GREEN)}")
        while True:
            ch=input(C("  W=Worthless  B=Bought back : ",CYAN)).strip().upper()
            if ch=="W": close_p=0.0; break
            elif ch=="B":
                try: close_p=float(input(C("  Close price Rs: ",CYAN)).strip()); break
                except: print(C("  ✗ Invalid.",RED))
            else: print(C("  ✗ W or B only.",RED))
        leg["closed"]=True; leg["close_premium"]=close_p
        lp=(leg["entry_premium"]-close_p) if leg["action"]=="sell" else (close_p-leg["entry_premium"])
        total_pnl+=lp
        print(C(f"  Leg P&L: Rs {lp:+.1f}",GREEN if lp>=0 else RED)); print()

    for adj in s["adjustments"]:
        if adj.get("closed"): continue
        est=adj.get("current_premium",adj["entry_premium"])
        print(f"  {C('Adj: '+adj['type'],CYAN)}  {adj['opt_type']} {adj['strike']}  Entry Rs{adj['entry_premium']:.1f}  Est Rs{est:.1f}")
        while True:
            ch=input(C("  W=Worthless  B=Bought back : ",CYAN)).strip().upper()
            if ch=="W": close_p=0.0; break
            elif ch=="B":
                try: close_p=float(input(C("  Close price Rs: ",CYAN)).strip()); break
                except: print(C("  ✗ Invalid.",RED))
            else: print(C("  ✗ W or B only.",RED))
        adj["closed"]=True; adj["close_premium"]=close_p
        ap=(adj["entry_premium"]-close_p) if adj["action"]=="sell" else (close_p-adj["entry_premium"])
        total_pnl+=ap
        print(C(f"  Adj P&L: Rs {ap:+.1f}",GREEN if ap>=0 else RED)); print()

    nc=s.get("net_credit",0)
    total_rs=round(total_pnl*lots*s["lot_size"],0)
    pct=round(total_pnl/nc*100,1) if nc else 0
    pc=GREEN if total_pnl>=0 else RED

    print(C("═"*56,CYAN))
    print(C("  EXPIRY RESULT",BOLD))
    print(f"  P&L per lot   : {C(f'Rs {total_pnl:.1f}',pc)}")
    print(f"  P&L total     : {C(f'Rs {total_rs:.0f}',pc)}")
    print(f"  vs Credit     : {C(f'{pct:+.1f}%',pc)}")
    print(f"  Credit coll.  : Rs {nc:.1f}/lot")
    print(C("═"*56,CYAN)); print()

    log_event(s,f"Expiry close. P&L Rs{total_pnl:.1f}/lot  Rs{total_rs:.0f} total")
    s["phase"]="CLOSED"; s["final_pnl"]=total_pnl; s["final_pnl_total"]=total_rs
    save_state(s)
    
    # v5.4: Log completed trade to analytics
    if HAS_ANALYTICS:
        try:
            from performance_analytics import TradeLog
            log = TradeLog()
            log.add_trade({
                'id': f"{strat}_{datetime.now().isoformat()}",
                'strategy': strat.replace('_', ' ').title(),
                'entry_date': s.get('entry_time', '').split('T')[0] if s.get('entry_time') else str(datetime.now().date()),
                'entry_time': s.get('entry_time', '').split('T')[1][:8] if s.get('entry_time') else '',
                'exit_date': str(datetime.now().date()),
                'exit_time': datetime.now().strftime('%H:%M:%S'),
                'entry_price': s.get('net_credit', 0),
                'exit_price': s.get('current_spot', 0),
                'position_size': lots,
                'pnl': total_pnl,
                'pnl_total': total_rs,
                'status': 'CLOSED_EXPIRY',
            })
        except Exception as e:
            log_event(s, f"Trade logging failed: {e}")
    
    archive_trade(s)
    STATE_FILE.unlink(missing_ok=True)
    print(C("  ✅  Position closed and archived.",GREEN))
    deploy_new_trade()


# ═══════════════════════════════════════════════════════════════════
#  CTRL+E — EMERGENCY MARKET EXIT
# ═══════════════════════════════════════════════════════════════════
_emergency_flag = threading.Event()

def _watch_ctrl_e():
    """Background thread — listens for CTRL+E (ASCII 5). Unix/Mac only."""
    try:
        import termios, tty
        fd=sys.stdin.fileno()
        old=termios.tcgetattr(fd)
        tty.setraw(fd)
        while True:
            ch=sys.stdin.read(1)
            if ch=="": _emergency_flag.set(); break  # CTRL+E
            if ch=="": break                          # CTRL+C passthrough
        termios.tcsetattr(fd,termios.TCSADRAIN,old)
    except Exception: pass   # Windows: use manual E input instead

def emergency_exit_market(s):
    """
    Sharp exit at market rate (CTRL+E or manual trigger).
    Enter current market bid/ask for each open leg.
    Archives and prompts new trade.
    """
    spot=s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI")) or 0
    hdr("🚨  EMERGENCY MARKET EXIT  (CTRL+E)")
    print(C("  Sharp exit — enter CURRENT MARKET PRICE for each open leg.",RED+BOLD))
    print(C("  Selling back  → use BID price",YELLOW))
    print(C("  Buying back   → use ASK price",YELLOW))
    print()

    total_pnl=0.0; lots=1
    for leg in s["legs"].values():
        if leg: lots=leg.get("lots",1); break

    update_live_premiums(s,spot)  # refresh BS estimates

    for key,leg in s["legs"].items():
        if not leg or leg.get("closed"): continue
        est=leg.get("current_premium",leg["entry_premium"])
        print(f"  {key.replace('_',' ').title():22} {leg['type']} {leg['strike']}"
              f"  Entry Rs{leg['entry_premium']:.1f}  BS-Est Rs{est:.1f}")
        try:
            mp=float(input(C("  Market price now Rs [press ENTER to use estimate]: ",RED)).strip() or str(round(est,1)))
        except: mp=est
        leg["closed"]=True; leg["close_premium"]=mp
        lp=(leg["entry_premium"]-mp) if leg["action"]=="sell" else (mp-leg["entry_premium"])
        total_pnl+=lp
        print(C(f"  Leg P&L: Rs {lp:+.1f}",GREEN if lp>=0 else RED)); print()

    for adj in s["adjustments"]:
        if adj.get("closed"): continue
        est=adj.get("current_premium",adj["entry_premium"])
        print(f"  Adj {adj['type']:20} {adj['opt_type']} {adj['strike']}  Entry Rs{adj['entry_premium']:.1f}  Est Rs{est:.1f}")
        try:
            mp=float(input(C("  Market price Rs [ENTER=estimate]: ",RED)).strip() or str(round(est,1)))
        except: mp=est
        adj["closed"]=True; adj["close_premium"]=mp
        ap=(adj["entry_premium"]-mp) if adj["action"]=="sell" else (mp-adj["entry_premium"])
        total_pnl+=ap
        print(C(f"  Adj P&L: Rs {ap:+.1f}",GREEN if ap>=0 else RED)); print()

    nc=s.get("net_credit",0)
    total_rs=round(total_pnl*lots*s["lot_size"],0)
    pct=round(total_pnl/nc*100,1) if nc else 0
    pc=GREEN if total_pnl>=0 else RED

    print(C("═"*56,RED))
    print(C("  EMERGENCY EXIT RESULT",BOLD+RED))
    print(f"  P&L per lot   : {C(f'Rs {total_pnl:.1f}',pc)}")
    print(f"  P&L total     : {C(f'Rs {total_rs:.0f}',pc)}")
    print(f"  vs Credit     : {C(f'{pct:+.1f}%',pc)}")
    print(C("═"*56,RED)); print()

    log_event(s,f"EMERGENCY EXIT. P&L Rs{total_pnl:.1f}/lot  Rs{total_rs:.0f} total")
    s["phase"]="CLOSED"; s["final_pnl"]=total_pnl; s["final_pnl_total"]=total_rs
    save_state(s)
    
    # v5.4: Log emergency exit to analytics
    if HAS_ANALYTICS:
        try:
            from performance_analytics import TradeLog
            log = TradeLog()
            log.add_trade({
                'id': f"{s.get('strategy','unknown')}_{datetime.now().isoformat()}",
                'strategy': s.get('strategy', 'unknown').replace('_', ' ').title(),
                'entry_date': s.get('entry_time', '').split('T')[0] if s.get('entry_time') else str(datetime.now().date()),
                'entry_time': s.get('entry_time', '').split('T')[1][:8] if s.get('entry_time') else '',
                'exit_date': str(datetime.now().date()),
                'exit_time': datetime.now().strftime('%H:%M:%S'),
                'entry_price': s.get('net_credit', 0),
                'exit_price': spot,
                'position_size': lots,
                'pnl': total_pnl,
                'pnl_total': total_rs,
                'status': 'EMERGENCY_EXIT',
            })
        except Exception as e:
            log_event(s, f"Trade logging failed: {e}")
    
    archive_trade(s)
    STATE_FILE.unlink(missing_ok=True)
    print(C("  ✅  Emergency exit complete. Trade archived.",GREEN))
    deploy_new_trade()


# ═══════════════════════════════════════════════════════════════════
#  DEPLOY NEW TRADE — called after every close
# ═══════════════════════════════════════════════════════════════════
def deploy_new_trade():
    """After any trade close, offer to deploy a new trade immediately."""
    print()
    print(C("═"*56,CYAN))
    print(C("  TRADE CLOSED — WHAT NEXT?",BOLD+CYAN))
    print(C("═"*56,CYAN))
    print()
    print(f"  {C('[N]',GREEN)}  Deploy new trade now  (fresh IV/HV analysis)")
    print(f"  {C('[X]',GRAY)}  Exit  (run options_manager.py again when ready)")
    print()
    try:
        ch=input(C("  Choice (N/X): ",CYAN)).strip().upper()
    except (EOFError,KeyboardInterrupt):
        ch="X"
    if ch=="N":
        print(); print(C("  Starting fresh IV/HV analysis…",CYAN)); time.sleep(1)
        s_new=empty_state()
        zone=run_iv_hv_analysis(s_new)
        if zone: strategy_selection(s_new,zone)
    else:
        print(C("  Exiting. Run  python options_manager.py  when ready.",GRAY))
        sys.exit(0)




# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
#  v5.4 MODULE INITIALIZATION
# ═══════════════════════════════════════════════════════════════════

def show_performance_metrics():
    """
    Display performance metrics from completed trades.
    Shows win rate, avg P&L, Sharpe ratio, max drawdown, etc.
    """
    if not HAS_ANALYTICS:
        print(C("  ⚠️   Performance analytics module not available", YELLOW))
        return
    
    try:
        from performance_analytics import TradeLog, PerformanceMetrics, PerformanceReport
        
        # Initialize TradeLog first (required by PerformanceMetrics)
        trade_log = TradeLog()
        metrics = PerformanceMetrics(trade_log)
        all_metrics = metrics.calculate_all()
        
        if not all_metrics or all_metrics.get('total_trades', 0) == 0:
            print(C("  ⓘ  No completed trades to analyze yet.", GRAY))
            return
        
        # Display summary
        hdr("📊 PERFORMANCE METRICS")
        print()
        print(C("  Trade Summary:", CYAN+BOLD))
        print(f"    Total Trades      : {all_metrics.get('total_trades', 0)}")
        print(f"    Winning Trades    : {all_metrics.get('winning_trades', 0)}")
        print(f"    Losing Trades     : {all_metrics.get('losing_trades', 0)}")
        print(f"    Win Rate          : {all_metrics.get('win_rate', 0):.1f}%")
        print()
        
        print(C("  P&L Summary:", CYAN+BOLD))
        print(f"    Total P&L         : Rs {all_metrics.get('total_pnl', 0):+.0f}")
        print(f"    Avg P&L per Trade : Rs {all_metrics.get('avg_pnl', 0):+.0f}")
        print(f"    Largest Win       : Rs {all_metrics.get('largest_win', 0):+.0f}")
        print(f"    Largest Loss      : Rs {all_metrics.get('largest_loss', 0):+.0f}")
        print()
        
        print(C("  Risk Metrics:", CYAN+BOLD))
        print(f"    Sharpe Ratio      : {all_metrics.get('sharpe_ratio', 0):.2f}")
        print(f"    Max Drawdown      : {all_metrics.get('max_drawdown', 0):.1f}%")
        print(f"    Profit Factor     : {all_metrics.get('profit_factor', 0):.2f}")
        print()
        
        # Generate detailed report
        report = PerformanceReport()
        detailed = report.generate_detailed_report()
        if detailed:
            print(C("  Detailed Report:", CYAN+BOLD))
            print(detailed[:500] + ("..." if len(detailed) > 500 else ""))
        
        print()
        
    except Exception as e:
        print(C(f"  ✗ Error loading metrics: {e}", RED))

def init_v54_modules():
    """
    Initialize all v5.4 enhancement modules.
    Returns config dict with all initialized handlers.
    """
    config = {
        'broker': None,
        'dashboard': None,
        'alerts': None,
        'analytics': None,
    }
    
    # 1. BROKER API
    if HAS_BROKER_API:
        broker_config = {
            'broker': os.getenv('BROKER_NAME', 'zerodha'),  # zerodha, angel, upstox
            'api_key': os.getenv('BROKER_API_KEY'),
            'access_token': os.getenv('BROKER_ACCESS_TOKEN'),
            'symbol': '^NSEI',
        }
        
        if broker_config.get('api_key'):
            try:
                broker = create_broker_connection(broker_config)
                if broker and broker.connect():
                    config['broker'] = broker
                    print(C(f"  ✅  Broker API connected: {broker.broker}", GREEN))
                else:
                    print(C("  ⚠️   Broker API fallback to Yahoo Finance", YELLOW))
            except Exception as e:
                print(C(f"  ⚠️   Broker API init failed: {e}", YELLOW))
    
    # 2. GOOGLE SHEETS DASHBOARD
    if HAS_SHEETS:
        sheet_id = os.getenv('GOOGLE_SHEETS_ID')
        if sheet_id:
            try:
                dashboard = DashboardMonitor(
                    state_file='options_state.json',
                    spreadsheet_id=sheet_id
                )
                if dashboard.start():
                    config['dashboard'] = dashboard
                    print(C("  ✅  Google Sheets dashboard connected", GREEN))
                else:
                    print(C("  ⚠️   Google Sheets auth failed", YELLOW))
            except Exception as e:
                print(C(f"  ⚠️   Sheets init failed: {e}", YELLOW))
    
    # 3. EMAIL & SMS ALERTS
    if HAS_ALERTS:
        email_config = {
            'sender_email': os.getenv('EMAIL_SENDER'),
            'sender_password': os.getenv('EMAIL_PASSWORD'),
            'smtp_server': 'smtp.gmail.com',
            'smtp_port': 587,
            'recipient_emails': os.getenv('EMAIL_RECIPIENTS', '').split(','),
        }
        
        sms_config = {
            'account_sid': os.getenv('TWILIO_ACCOUNT_SID'),
            'auth_token': os.getenv('TWILIO_AUTH_TOKEN'),
            'from_number': os.getenv('TWILIO_FROM_NUMBER'),
            'to_numbers': os.getenv('TWILIO_TO_NUMBERS', '').split(','),
        }
        
        try:
            alerts = AlertManager(
                email_config=email_config if email_config.get('sender_email') else None,
                sms_config=sms_config if sms_config.get('account_sid') else None
            )
            config['alerts'] = alerts
            print(C("  ✅  Alert system initialized", GREEN))
        except Exception as e:
            print(C(f"  ⚠️   Alerts init failed: {e}", YELLOW))
    
    # 4. PERFORMANCE ANALYTICS
    if HAS_ANALYTICS:
        try:
            trade_log = TradeLog('trade_log.json')
            analytics = PerformanceMetrics(trade_log)
            config['analytics'] = {
                'log': trade_log,
                'metrics': analytics,
            }
            print(C("  ✅  Performance analytics initialized", GREEN))
        except Exception as e:
            print(C(f"  ⚠️   Analytics init failed: {e}", YELLOW))
    
    return config


def main():
    global _V54_CONFIG
    hdr("⚡  OPTIONS MANAGER  v5.4  —  10 Strategies + Real-Time Alerts")
    
    # ═══ v5.4 MODULE INITIALIZATION ═══
    v54_config = init_v54_modules()
    _V54_CONFIG = v54_config  # Store in module-level variable for access in other functions
    
    # ═══ v5.4: Performance Metrics Menu ═══
    print()
    print(C("  Quick Menu:", CYAN))
    print(f"  {C('[M]', CYAN)}  View Performance Metrics")
    print(f"  {C('[T]', CYAN)}  Trade Manager (default)")
    print()
    try:
        menu_ch = input(C("  Choice (M/T/ENTER=T): ", CYAN)).strip().upper()
    except: menu_ch = "T"
    
    if menu_ch == "M":
        show_performance_metrics()
        print()
        try:
            if input(C("  Continue to trade manager? (y/n): ", CYAN)).strip().lower() != "y":
                return
        except: return
    
    print()

    s=load_state()

    if s.get("phase")=="LIVE":
        strat=s.get("strategy","ironfly")
        label={"ironfly":"Iron Fly","ironcondor":"Iron Condor","butterfly":"Butterfly","credit_spread":"Credit Spread","bwb":"BWB","strangle":"Short Strangle","calendar":"Calendar Spread","debit_spread":"Debit Spread","long_options":"Long Options","debit_straddle":"Debit Straddle"}.get(strat,strat)
        print(C(f"  Resuming LIVE {label}",GREEN+BOLD))
        
        # Try broker API first, fallback to Yahoo Finance
        if v54_config.get('broker'):
            spot = v54_config['broker'].fetch_spot_price()
        else:
            spot = fetch_spot(s.get("symbol","^NSEI"))
        
        if spot: s["current_spot"]=spot; print(C(f"  Live spot: {spot}",CYAN))
        else: print(C("  Spot fetch failed — using last known.",YELLOW))
        show_position_summary(s)
        draw_payoff_chart(s)
        print(C("  ✅  State data available — skipping IV/HV analysis (Step 1).",GRAY))
        print(C("  Resuming live monitor…",CYAN)); time.sleep(1)
        live_monitor_loop(s, v54_config)
        return

    if s.get("phase")=="CLOSED":
        STATE_FILE.unlink(missing_ok=True); s=empty_state()

    zone=run_iv_hv_analysis(s)
    if zone: strategy_selection(s, zone, v54_config)



# ═══════════════════════════════════════════════════════════════════
#  CREDIT SPREAD — SETUP  (Theta Gainers Rule Book — all 6 sections)
# ═══════════════════════════════════════════════════════════════════
def credit_spread_setup(s):
    hdr("CREDIT SPREAD — SETUP  (Theta Gainers Rule Book)")

    spot = s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI"))
    vc   = s.get("vix_current", 17)
    ratio = s.get("ratio", 1.0)

    if spot:
        print(f"  Live Spot   : {C(B(str(spot)), GREEN)}")
        print(f"  India VIX   : {C(str(vc), YELLOW)}")
        print(f"  IV/HV Ratio : {C(str(ratio), YELLOW)}")
    print()

    # ── Section 05: IV check ──────────────────────────────────────
    print(C("  ── IV Conditions Check (Section 05) ───────────────", CYAN))
    if vc and float(vc) < 15:
        print(C(f"  ❌  IV < 15 ({vc}) — premium too thin. AVOID credit spreads.", RED+BOLD))
        if input(C("  Override and continue? (y/n): ", CYAN)).strip().lower() != "y": return
    elif vc and float(vc) >= 20:
        print(C(f"  ✅  IV High ({vc}) — BEST conditions. Wider spreads allowed.", GREEN))
    elif vc and float(vc) >= 15:
        print(C(f"  ✅  IV Moderate ({vc}) — Acceptable. Use tighter spreads.", YELLOW))

    # ── Section 02: Master Decision Tree — spread type ───────────
    print()
    print(C("  ── Section 02: Market Bias Decision Tree ───────────", CYAN))
    print(f"  {C('[1]', RED)}    Credit CALL Spread  — Bearish / Neutral (market at/below resistance)")
    print(f"  {C('[2]', GREEN)}   Credit PUT  Spread  — Bullish / Neutral (market at/above support)")
    print()

    # auto-suggest based on zone
    if ratio >= 1.4:
        print(C("  Zone suggests: Both spreads valid (high premium zone)", YELLOW))
    print(C("  Decision Tree:", GRAY))
    print(C("  Market at resistance → CALL Spread | Market at support → PUT Spread", GRAY))
    print()
    try:
        cs_choice = int(input(C("  Select type (1=Call / 2=Put): ", CYAN)).strip())
    except: cs_choice = 1
    if cs_choice not in [1, 2]: cs_choice = 1

    cs_type  = "call" if cs_choice == 1 else "put"
    opt_type = "CE"   if cs_type == "call" else "PE"
    bias_lbl = "Bearish/Neutral" if cs_type == "call" else "Bullish/Neutral"
    print(C(f"  Selected: Credit {opt_type} Spread — {bias_lbl}", GREEN))

    # ── Expiry selection ──────────────────────────────────────────
    expiry_date, dte = select_expiry()
    s["expiry_date"] = expiry_date
    s["dte_at_entry"] = dte

    # DTE-based spread width guidance (Section 05)
    print()
    print(C("  ── Expiry & IV based spread width guidance ─────────", CYAN))
    if dte <= 7:
        spread_sugg = "100-200 pts  (Weekly — best theta decay)"
        spread_lo, spread_hi = 100, 200
    else:
        spread_sugg = "200-300 pts  (Monthly — wider range needed)"
        spread_lo, spread_hi = 200, 300
    if vc and float(vc) >= 20:
        print(C(f"  High IV + {'Weekly' if dte<=7 else 'Monthly'}: wider spreads OK ({spread_sugg})", YELLOW))
    else:
        print(C(f"  Moderate IV: tighter spreads ({spread_sugg})", GRAY))

    # ── Section 03/04: Strike selection ──────────────────────────
    print()
    print(C(f"  ── Section 0{'3' if cs_type=='call' else '4'}: Strike Selection ─────────────────────", CYAN))

    if cs_type == "call":
        print(C("  CALL Spread — find RESISTANCE zone:", YELLOW+BOLD))
        print(C("  Sell Call AT or just BELOW resistance", GRAY))
        print(C("  Buy Call 100-200 pts ABOVE sold strike", GRAY))
        if spot:
            res_sugg = int(round(spot * 1.022 / 50) * 50)
            hedge_sugg = res_sugg + 150
            print(f"  Suggested resistance zone : {C(str(res_sugg), YELLOW)}  (~2.2% above spot)")
            print(f"  Suggested hedge strike    : {C(str(hedge_sugg), GRAY)}  (+150 pts)")
    else:
        print(C("  PUT Spread — find SUPPORT zone:", YELLOW+BOLD))
        print(C("  Sell Put AT or just ABOVE support", GRAY))
        print(C("  Buy Put 100-200 pts BELOW sold strike", GRAY))
        if spot:
            sup_sugg  = int(round(spot * 0.978 / 50) * 50)
            hedge_sugg = sup_sugg - 150
            print(f"  Suggested support zone  : {C(str(sup_sugg), YELLOW)}  (~2.2% below spot)")
            print(f"  Suggested hedge strike  : {C(str(hedge_sugg), GRAY)}  (-150 pts)")

    # Delta guidance
    print()
    print(C("  Strike Selection by Risk Level:", CYAN))
    print(f"  Conservative : Delta 0.15-0.20  (at S/R level)       LOW risk")
    print(f"  Moderate     : Delta 0.20-0.25  (near S/R level)     MEDIUM risk")
    print(f"  Aggressive   : Delta 0.25-0.35  (slightly inside S/R) HIGH risk")
    print()

    try:
        if cs_type == "call":
            raw = input(C(f"  SELL Call strike (at resistance) [{res_sugg if spot else 'e.g.24200'}]: ", CYAN)).strip()
            sold_strike = int(raw) if raw else (res_sugg if spot else 0)
            raw = input(C(f"  BUY  Call strike (hedge, 100-200 pts above) [{sold_strike+150}]: ", CYAN)).strip()
            hedge_strike = int(raw) if raw else sold_strike + 150
        else:
            raw = input(C(f"  SELL Put strike (at support) [{sup_sugg if spot else 'e.g.23800'}]: ", CYAN)).strip()
            sold_strike = int(raw) if raw else (sup_sugg if spot else 0)
            raw = input(C(f"  BUY  Put strike (hedge, 100-200 pts below) [{sold_strike-150}]: ", CYAN)).strip()
            hedge_strike = int(raw) if raw else sold_strike - 150
    except: print(C("  ✗ Invalid.", RED)); return

    spread_width = abs(sold_strike - hedge_strike)
    if spread_width < 50:
        print(C(f"  ⚠  Spread width {spread_width} too narrow — min 100 pts recommended.", YELLOW))
    if spread_width > 300:
        print(C(f"  ⚠  Spread width {spread_width} very wide — max loss increases.", YELLOW))

    # ── Lots ──────────────────────────────────────────────────────
    try: lots = int(input(C("  Number of lots [1]: ", CYAN)).strip() or "1")
    except: lots = 1

    # ── Premiums ──────────────────────────────────────────────────
    print()
    print(C("  ── Enter Premiums ──────────────────────────────────", CYAN))
    try:
        sell_prem = float(input(C(f"  SELL {opt_type} {sold_strike}  premium collected : Rs ", CYAN)).strip())
        buy_prem  = float(input(C(f"  BUY  {opt_type} {hedge_strike} premium paid      : Rs ", CYAN)).strip())
    except: print(C("  ✗ Invalid.", RED)); return

    net_credit = round(sell_prem - buy_prem, 2)
    max_loss   = round(spread_width - net_credit, 2)
    stop_loss_level = round(net_credit * 1.5, 2)   # Loss Rule 1
    profit_target_50 = round(net_credit * 0.50, 2)
    profit_target_70 = round(net_credit * 0.70, 2)

    # BE
    if cs_type == "call":
        be = round(sold_strike + net_credit, 2)
        ube = be; lbe = be - spread_width * 3   # put side irrelevant
        s["short_call"] = sold_strike
        s["short_put"]  = None
    else:
        be = round(sold_strike - net_credit, 2)
        lbe = be; ube = be + spread_width * 3
        s["short_put"]  = sold_strike
        s["short_call"] = None

    # ── Risk-Reward verification ──────────────────────────────────
    print()
    print(C("  ── Risk-Reward Verification (Section 01) ───────────", CYAN))
    print(f"  Net Credit           : {C(f'Rs {net_credit:.1f}', GREEN)}")
    print(f"  Spread Width         : {spread_width} pts")
    mp_total=round(net_credit*lots*s["lot_size"],0); ml_total=round(max_loss*lots*s["lot_size"],0)
    print(f"  Max Profit           : {C(f'Rs {net_credit:.1f}/lot  (Rs {mp_total:.0f} total)', GREEN)}")
    print(f"  Max Loss             : {C(f'Rs {max_loss:.1f}/lot  (Rs {ml_total:.0f} total)', RED)}")
    print(f"  Breakeven            : {C(str(be), YELLOW)}")
    print(f"  Stop Loss Level      : {C(f'Rs {stop_loss_level:.1f}  (1.5x credit — Loss Rule 1)', RED)}")
    print(f"  Profit Target 50%    : {C(f'Rs {profit_target_50:.1f} remaining premium', GREEN)}")
    print(f"  Profit Target 70%    : {C(f'Rs {profit_target_70:.1f} remaining premium', GREEN)}")
    print(f"  DTE at entry         : {dte}  |  Expiry: {expiry_date}")

    # capital risk check
    capital_risk = max_loss * lots * s["lot_size"]
    print()
    try:
        cap = float(input(C("  Total capital Rs (for risk % check, ENTER to skip): ", CYAN)).strip() or "0")
    except: cap = 0
    if cap > 0:
        risk_pct = capital_risk / cap * 100
        col = GREEN if risk_pct <= 2 else RED
        print(C(f"  Risk % of capital    : {risk_pct:.1f}%  (max 2% per rule)", col))
        if risk_pct > 2:
            safe_lots = max(1, int(cap * 0.02 / (max_loss * s["lot_size"])))
            print(C(f"  ⚠  Reduce to {safe_lots} lot(s) to stay within 2% rule.", YELLOW))

    # ── Pre-trade checklist (Section 06) ─────────────────────────
    print()
    print(C("  ── Pre-Trade Checklist (Section 06) ────────────────", CYAN))
    checks = [
        ("Market bias clearly identified",   bias_lbl),
        ("Chart S/R level confirmed",        f"{'Resistance' if cs_type=='call' else 'Support'} at {sold_strike}"),
        ("IV level acceptable",              f"VIX={vc}"),
        ("Expiry selected",                  f"{expiry_date} ({dte} DTE)"),
        ("Net credit calculated",            f"Rs {net_credit:.1f}"),
        ("Max loss defined",                 f"Rs {max_loss:.1f}"),
        ("Stop loss level set",              f"Rs {stop_loss_level:.1f} (1.5x credit)"),
        ("Profit target defined",            f"50-70% decay = Rs {profit_target_50:.1f}-{profit_target_70:.1f}"),
        ("Position size within 2% rule",     "✅" if cap == 0 or (max_loss*lots*s["lot_size"]/cap*100 <= 2) else "⚠ Review"),
    ]
    for chk, val in checks:
        print(f"  ✅  {chk:<40} {C(val, GRAY)}")

    print()
    if input(C("  Confirm entry (y/n): ", CYAN)).strip().lower() != "y": return

    # ── Store state ───────────────────────────────────────────────
    s.update({
        "cs_type":        cs_type,
        "cs_opt_type":    opt_type,
        "sold_strike":    sold_strike,
        "hedge_strike":   hedge_strike,
        "spread_width":   spread_width,
        "net_credit":     net_credit,
        "upper_be":       ube,
        "lower_be":       lbe,
        "breakeven":      be,
        "stop_loss_level":stop_loss_level,
        "profit_t50":     profit_target_50,
        "profit_t70":     profit_target_70,
        "entry_spot":     spot or sold_strike,
        "current_spot":   spot or sold_strike,
        "entry_time":     datetime.now().isoformat(),
        "phase":          "LIVE",
        "roll_count":     {"call":0,"put":0},
        "adjustments":    [],
        "pnl_snapshots":  [],
    })
    s["legs"] = {
        "short_leg": {
            "strike": sold_strike, "type": opt_type, "action": "sell",
            "lots": lots, "entry_premium": sell_prem, "current_premium": sell_prem
        },
        "long_leg": {
            "strike": hedge_strike, "type": opt_type, "action": "buy",
            "lots": lots, "entry_premium": buy_prem, "current_premium": buy_prem
        },
    }

    log_event(s, f"CreditSpread {opt_type}: SELL {sold_strike} Rs{sell_prem} / BUY {hedge_strike} Rs{buy_prem} | net=Rs{net_credit} BE={be} SL=Rs{stop_loss_level} expiry={expiry_date}")
    save_state(s)

    print(); print(C("═"*66, GREEN))
    print(C(f"  ✅  CREDIT {opt_type} SPREAD ENTERED", GREEN+BOLD))
    print(C("═"*66, GREEN))
    print()
    print(C(f"  Market must stay {'BELOW' if cs_type=='call' else 'ABOVE'} {sold_strike} for max profit.", CYAN+BOLD))
    print(C(f"  Exit at 50-70% premium decay. Hard SL at 1.5x credit.", CYAN))
    print()
    show_position_summary(s)
    draw_payoff_chart(s)
    print(C("  Starting live 15-min monitor… CTRL+C to save & exit.", CYAN))
    time.sleep(1)
    live_monitor_loop(s)


# ═══════════════════════════════════════════════════════════════════
#  CREDIT SPREAD — BREACH / ADJUSTMENT GUIDANCE
# ═══════════════════════════════════════════════════════════════════
def guide_cs_breach(s, spot, side):
    cs_type  = s.get("cs_type","call")
    sold     = s.get("sold_strike", 0)
    nc       = s.get("net_credit", 0)
    sl_lvl   = s.get("stop_loss_level", nc * 1.5)
    opt_type = s.get("cs_opt_type","CE")
    be       = s.get("breakeven", sold)
    sw       = s.get("spread_width", 150)
    pnl      = update_live_premiums(s, spot)

    print()
    print(C(f"  ╔{'═'*58}╗", RED+BOLD))
    print(C(f"  ║  🚨  CREDIT {opt_type} SPREAD — SOLD STRIKE UNDER PRESSURE {'='*14}║", RED+BOLD))
    print(C(f"  ╚{'═'*58}╝", RED+BOLD))
    print()
    print(f"  Spot {spot}  |  Sold {opt_type} {sold}  |  BE {be}  |  P&L Rs{pnl:.1f}")
    print()

    # ── Loss Rules (Section 03/04) ────────────────────────────────
    print(C("  ── Loss Rule Assessment ───────────────────────────", CYAN))
    sl_short = s["legs"].get("short_leg",{})
    curr_prem = sl_short.get("current_premium", sl_short.get("entry_premium",0)) if sl_short else 0
    entry_prem = sl_short.get("entry_premium",0) if sl_short else 0
    net_loss   = curr_prem - entry_prem   # how much net prem has grown (positive = losing)

    if net_loss >= sl_lvl:
        print(C(f"  🚨  LOSS RULE 1 HIT: Net premium Rs{curr_prem:.1f} ≥ 1.5x credit Rs{sl_lvl:.1f}", RED+BOLD))
        print(C("  EXIT IMMEDIATELY — Do NOT average. Do NOT hold. (Loss Rule 1)", RED+BOLD))
    else:
        remaining = sl_lvl - net_loss
        print(C(f"  Current net premium : Rs{curr_prem:.1f}  (entry Rs{entry_prem:.1f})", YELLOW))
        print(C(f"  Stop loss level     : Rs{sl_lvl:.1f}  (Rs{remaining:.1f} buffer remaining)", YELLOW))

    # Candle close check (Loss Rule 2)
    print()
    print(C("  ── Loss Rule 2 — 15-min Candle Check ──────────────", CYAN))
    if cs_type == "call":
        print(C(f"  Has a 15-min candle CLOSED ABOVE sold Call {sold}?", YELLOW))
        print(C("  YES → Defensive action: EXIT or ROLL. (Loss Rule 2)", RED))
    else:
        print(C(f"  Has a 15-min candle CLOSED BELOW sold Put {sold}?", YELLOW))
        print(C("  YES → Defensive action: EXIT or ROLL. (Loss Rule 2)", RED))

    # ── Adjustment rules ──────────────────────────────────────────
    print()
    print(C("  ── Adjustment Decision ────────────────────────────", CYAN))
    if cs_type == "call":
        print(C("  Adjustment 1 (Slow drift): Roll spread 1-2 strikes HIGHER.", YELLOW))
        new_sell_sugg = round((sold + 100) / 50) * 50
        print(C(f"  → Roll from {sold} CE to ~{new_sell_sugg} CE  (same width)", GRAY))
        print(C("  Adjustment 2 (Violent breakout): EXIT IMMEDIATELY. No adjustment.", RED+BOLD))
    else:
        print(C("  Adjustment 1 (Slow drift): Roll spread 1-2 strikes LOWER.", YELLOW))
        new_sell_sugg = round((sold - 100) / 50) * 50
        print(C(f"  → Roll from {sold} PE to ~{new_sell_sugg} PE  (same width)", GRAY))
        print(C("  Adjustment 2 (Violent crash): EXIT IMMEDIATELY. No adjustment.", RED+BOLD))

    print()
    print(C("  ── Golden Rules ────────────────────────────────────", CYAN))
    print(C("  Never hold a losing spread emotionally.", RED))
    print(C("  Never average down on a losing spread.", RED))
    print(C("  Never fight a strong trending move.", RED))
    print()
    print("   E = Exit now (Loss Rule 1/2 triggered)")
    print("   R = Roll spread (Adjustment 1 — slow drift only)")
    print("   W = Watch one more candle (if marginal breach)")
    print()
    ch = input(C("  Action (E/R/W): ", CYAN)).strip().upper()

    if ch == "E":
        print()
        print(C("  Exiting Credit Spread position.", RED+BOLD))
        expiry_close_wizard(s)

    elif ch == "R":
        rc = s.get("roll_count", {"call":0,"put":0})
        rk = "call" if cs_type=="call" else "put"
        if rc.get(rk,0) >= 1:
            print(C("  ⚠  Already rolled once. Rule: if 2nd breach → EXIT entirely.", RED+BOLD))
            expiry_close_wizard(s); return
        print()
        print(C("  Rolling spread (Adjustment 1):", YELLOW+BOLD))
        try:
            close_s = float(input(C(f"  Buyback price for SELL {opt_type} {sold}: Rs ", CYAN)).strip())
            close_b = float(input(C(f"  Sell price for BUY {opt_type} {s.get('hedge_strike','')}: Rs ", CYAN)).strip())
            new_sell = int(input(C(f"  New SELL {opt_type} strike [{new_sell_sugg}]: ", CYAN)).strip() or str(new_sell_sugg))
            new_hedge = int(input(C(f"  New BUY {opt_type} strike [{new_sell+(sw if cs_type=='call' else -sw)}]: ", CYAN)).strip() or str(new_sell+(sw if cs_type=="call" else -sw)))
            new_sp = float(input(C(f"  New SELL {opt_type} {new_sell} premium collected: Rs ", CYAN)).strip())
            new_bp = float(input(C(f"  New BUY {opt_type} {new_hedge} premium paid: Rs ", CYAN)).strip())
        except: print(C("  ✗ Invalid.", RED)); return

        roll_debit = close_s - close_b
        new_nc = round(new_sp - new_bp - roll_debit, 2)
        new_be = round(new_sell + new_nc, 2) if cs_type=="call" else round(new_sell - new_nc, 2)

        s["legs"]["short_leg"].update({"strike":new_sell,"entry_premium":new_sp,"current_premium":new_sp})
        s["legs"]["long_leg"].update({"strike":new_hedge,"entry_premium":new_bp,"current_premium":new_bp})
        s["sold_strike"] = new_sell
        s["hedge_strike"] = new_hedge
        s["net_credit"]  = new_nc
        s["breakeven"]   = new_be
        s["stop_loss_level"] = round(new_nc*1.5, 2)
        if cs_type == "call": s["upper_be"] = new_be
        else:                 s["lower_be"]  = new_be
        rc[rk] = rc.get(rk,0)+1; s["roll_count"]=rc

        log_event(s, f"CS Roll: {sold}→{new_sell} new_nc=Rs{new_nc:.1f} BE={new_be}")
        save_state(s); draw_payoff_chart(s)
        print(C(f"  ✅  Rolled to {new_sell}. New BE={new_be}. New credit=Rs{new_nc:.1f}. Roll count={rc[rk]}/1.", GREEN))

    elif ch == "W":
        print(C("  Watching next candle. Monitor closely. [Loss Rule 2]", YELLOW))

    else:
        print(C("  ✗ Invalid.", RED))


# ═══════════════════════════════════════════════════════════════════
#  BROKEN WING BUTTERFLY (BWB) — SETUP  (Full Advanced Rulebook)
#  16 Sections | 10 Golden Rules | 14-point Entry Checklist
# ═══════════════════════════════════════════════════════════════════
def bwb_setup(s):
    hdr("BROKEN WING BUTTERFLY (BWB) — SETUP  (Advanced Professional Rulebook)")

    spot = s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI"))
    vc   = s.get("vix_current", 17)
    ratio= s.get("ratio", 1.0)

    if spot:
        print(f"  Live Spot   : {C(B(str(spot)), GREEN)}")
        print(f"  India VIX   : {C(str(vc), YELLOW)}")
        print(f"  IV/HV Ratio : {C(str(ratio), YELLOW)}")
    print()

    # ── Section 06: Decision Tree Entry (10 steps) ───────────────
    print(C("  ── Section 06: Entry Decision Tree (10 Steps) ─────", CYAN))
    print(C("  Every step must be YES before proceeding.", YELLOW+BOLD))
    print()
    checks = [
        ("Step 1", "Clear directional bias (bullish OR bearish)?"),
        ("Step 2", "Clear support/resistance level identified?"),
        ("Step 3", "DTE between 5-15 days? (Weekly preferred)"),
        ("Step 4", "NO major event in next 24 hours?"),
        ("Step 5", "IV normal / VIX < 18?"),
        ("Step 6", "Short strike at ATM or high-OI zone near S/R?"),
        ("Step 7", "Broken wing side WIDER than safe wing?"),
        ("Step 8", "Position will be NET CREDIT?"),
        ("Step 9", "POP > 55%?"),
        ("Step 10","Both breakeven alerts will be set after entry?"),
    ]
    vix_ok = vc and float(vc) < 18
    for code, desc in checks:
        if "VIX" in desc:
            auto = "✅" if vix_ok else C("⚠ VIX="+str(vc), RED)
            print(f"  {C(code,CYAN):<16} {desc}  {auto}")
        else:
            print(f"  {C(code,CYAN):<16} {desc}")
    print()
    if input(C("  All 10 steps confirmed YES? (y/n): ", CYAN)).strip().lower() != "y":
        print(C("  ⛔  Do NOT enter BWB — entry conditions not met.", RED+BOLD)); return

    # ── Section 01: BWB vs Butterfly clarity ─────────────────────
    print()
    print(C("  ── Section 01: BWB vs Standard Butterfly ───────────", CYAN))
    print(C("  BWB = ASYMMETRIC wings → creates NET CREDIT + directional bias.", YELLOW+BOLD))
    print(C("  Standard Butterfly = equal wings, neutral, usually net debit.", GRAY))
    print(C("  Always target NET CREDIT BWB. Debit BWB = expert only. [Section 12]", GRAY))
    print()

    # ── Section 02: Type selection ────────────────────────────────
    print(C("  ── Section 02: BWB Type Selection ─────────────────", CYAN))
    print(f"  {C('[1]', GREEN)}  Bullish Put BWB   — slight bullish bias, sell puts [TY-01]")
    print(f"  {C('[2]', RED)}   Bearish Call BWB  — slight bearish bias, sell calls [TY-02]")
    print()
    print(C("  Wing Ratio Guide:", GRAY))
    print(f"  {'150/100':<12} Mild credit   — first-time BWB traders")
    print(f"  {'200/100':<12} Good credit   — IDEAL for most BWBs ← recommended")
    print(f"  {'250/100':<12} Good credit   — strong conviction")
    print(f"  {'300/100':<12} High credit   — expert + very high conviction only")
    print()
    try:
        btype = int(input(C("  Select BWB type (1=Bullish Put / 2=Bearish Call): ", CYAN)).strip())
    except: btype = 1
    if btype not in [1,2]: btype = 1
    bwb_bias = "bullish" if btype==1 else "bearish"
    opt_type = "PE" if btype==1 else "CE"
    print(C(f"  Selected: {'Bullish Put' if btype==1 else 'Bearish Call'} BWB  ({opt_type})", GREEN))

    # ── Section 04: Wing ratio ────────────────────────────────────
    print()
    print(C("  ── Section 04: Wing Ratio Selection ────────────────", CYAN))
    try:
        ratio_choice = int(input(C("  Wing ratio — wide/tight (1=150/100  2=200/100  3=250/100  4=300/100) [2]: ", CYAN)).strip() or "2")
    except: ratio_choice = 2
    wide_dist_map = {1:150, 2:200, 3:250, 4:300}
    tight_dist    = 100
    wide_dist     = wide_dist_map.get(ratio_choice, 200)
    print(C(f"  Wing ratio: {wide_dist}/{tight_dist}  (wide={wide_dist}pts / tight={tight_dist}pts)", GREEN))

    # ── Expiry selection ──────────────────────────────────────────
    expiry_date, dte = select_expiry()
    s["expiry_date"]  = expiry_date
    s["dte_at_entry"] = dte

    # ── Section 03/04: Strike selection ──────────────────────────
    print()
    print(C("  ── Section 03: Step-by-Step Creation ──────────────", CYAN))
    if spot:
        # Bullish Put BWB: sell puts slightly below spot
        if bwb_bias == "bullish":
            center_sugg = int(round((spot - 50) / 50) * 50)
            safe_sugg   = center_sugg + tight_dist   # ITM (upper)
            wide_sugg   = center_sugg - wide_dist    # OTM (lower, broken)
        else:
            center_sugg = int(round((spot + 50) / 50) * 50)
            safe_sugg   = center_sugg - tight_dist   # lower call
            wide_sugg   = center_sugg + wide_dist    # upper call (broken)

        print(f"  Suggested structure ({opt_type}):")
        print(f"  SELL 2x Center (body) : {C(str(center_sugg), YELLOW)}  (ATM/near-ATM — max theta)")
        print(f"  BUY  1x Safe wing     : {C(str(safe_sugg), CYAN)}   ({tight_dist}pts — caps loss on bias side)")
        print(f"  BUY  1x Broken wing   : {C(str(wide_sugg), GRAY)}  ({wide_dist}pts — wide, reduces hedge cost)")
        print()
        print(C(f"  [Section 04] Short strike: ATM or 1-2 strikes OTM in bias direction", GRAY))
        print(C(f"  [Section 04] Safe wing: {tight_dist}pts | Broken wing: {wide_dist}pts", GRAY))
    else:
        center_sugg = safe_sugg = wide_sugg = None
    print()

    try:
        raw = input(C(f"  CENTER (SELL 2x) strike [{center_sugg}]: ", CYAN)).strip()
        center_strike = int(raw) if raw else center_sugg
        raw = input(C(f"  SAFE WING (BUY 1x, tight) [{safe_sugg}]: ", CYAN)).strip()
        safe_strike = int(raw) if raw else safe_sugg
        raw = input(C(f"  BROKEN WING (BUY 1x, wide) [{wide_sugg}]: ", CYAN)).strip()
        wide_strike = int(raw) if raw else wide_sugg
    except: print(C("  ✗ Invalid.", RED)); return

    # Validate 1:2:1 ratio [Section 14]
    print()
    print(C("  [Section 14] Verifying 1:2:1 lot ratio — non-negotiable.", CYAN))
    try:
        lots = int(input(C("  Number of lots for center SELL (×2 will be used) [1]: ", CYAN)).strip() or "1")
    except: lots = 1
    print(C(f"  Structure: BUY {lots}x {wide_strike} | SELL {lots*2}x {center_strike} | BUY {lots}x {safe_strike}", GREEN))

    # ── Premiums ──────────────────────────────────────────────────
    print()
    print(C("  ── Premiums [Section 03, Step 7] ───────────────────", CYAN))
    try:
        center_p = float(input(C(f"  SELL {opt_type} {center_strike} premium (×2): Rs ", CYAN)).strip())
        safe_p   = float(input(C(f"  BUY  {opt_type} {safe_strike}  premium (safe wing): Rs ", CYAN)).strip())
        wide_p   = float(input(C(f"  BUY  {opt_type} {wide_strike}  premium (broken wing): Rs ", CYAN)).strip())
    except: print(C("  ✗ Invalid.", RED)); return

    net_credit = round(2*center_p - safe_p - wide_p, 2)

    # [Step 7] Net credit check — MANDATORY
    if net_credit <= 0:
        print(C(f"  ❌  Net credit = Rs{net_credit:.1f} — DEBIT structure! BWB requires NET CREDIT.", RED+BOLD))
        print(C("  Solution: Widen broken wing further or adjust center strike.", YELLOW))
        if input(C("  Continue anyway? (y/n): ", CYAN)).strip().lower() != "y": return

    # P&L calculations
    safe_width  = abs(safe_strike  - center_strike)
    broken_width= abs(wide_strike  - center_strike)
    max_profit  = round(safe_width  + net_credit, 2)   # market pins at center
    max_loss    = round(broken_width - net_credit, 2)   # market beyond broken wing
    pop_approx  = round(min(70, 55 + (net_credit / center_p) * 15), 1)

    # Breakevens
    if bwb_bias == "bullish":  # put BWB
        upper_be = round(center_strike + safe_width + net_credit, 2)
        lower_be = round(wide_strike   + net_credit, 2)   # approx
    else:                      # call BWB
        lower_be = round(center_strike - safe_width - net_credit, 2)
        upper_be = round(wide_strike   - net_credit, 2)

    # ── Section 03 Step 7: Verify ─────────────────────────────────
    print()
    print(C("  ── Step 7: Net Credit & POP Verification ───────────", CYAN))
    nc_col = GREEN if net_credit > 0 else RED
    print(f"  Net Credit     : {C(f'Rs {net_credit:.1f}', nc_col)}")
    print(f"  Safe wing      : {safe_width} pts    Broken wing : {broken_width} pts")
    print(f"  Max Profit     : {C(f'Rs {max_profit:.1f}  (market near {center_strike} at expiry)', GREEN)}")
    print(f"  Max Loss       : {C(f'Rs {max_loss:.1f}  (beyond {wide_strike})', RED)}")
    print(f"  POP (approx)   : {C(f'{pop_approx}%', GREEN if pop_approx>=55 else RED)}")
    print(f"  Upper BE       : {C(str(upper_be), YELLOW)}")
    print(f"  Lower BE       : {C(str(lower_be), YELLOW)}")
    print(f"  DTE at entry   : {dte}  |  Expiry: {expiry_date}")
    print()

    if pop_approx < 55:
        print(C(f"  ⚠  [Step 9] POP {pop_approx}% < 55% — widen broken wing or adjust center.", YELLOW))
    if net_credit > 0:
        print(C(f"  ✅  Net credit confirmed — immediate positive theta. [Golden Rule 5]", GREEN))

    # Capital risk check [Section 13]
    print()
    print(C("  ── Section 13: Risk Management ─────────────────────", CYAN))
    print(f"  Max risk/trade : 3-5% of capital  |  Account risk : 1-2% max")
    try:
        cap = float(input(C("  Total capital Rs (ENTER to skip): ", CYAN)).strip() or "0")
    except: cap = 0
    if cap > 0:
        risk_pct = max_loss * lots * s["lot_size"] / cap * 100
        col = GREEN if risk_pct <= 2 else YELLOW if risk_pct <= 5 else RED
        print(C(f"  Risk %         : {risk_pct:.1f}% of capital", col))
        if risk_pct > 5:
            safe_lots = max(1, int(cap * 0.02 / (max_loss * s["lot_size"])))
            print(C(f"  ⚠  Reduce to {safe_lots} lot(s) for 2% account risk.", YELLOW))

    # ── Section 15: Entry Checklist ───────────────────────────────
    print()
    print(C("  ── Section 15: 14-Point Entry Checklist ────────────", CYAN))
    cl_items = [
        "Market direction confirmed",
        "View based on chart analysis (S/R/VWAP)",
        f"DTE {dte} days — within 5-15 range" if 5<=dte<=15 else f"DTE {dte} ⚠ (ideal: 5-15)",
        "Short strike at ATM/high-OI zone",
        f"Broken wing ({wide_dist}pts) > safe wing ({tight_dist}pts)",
        "Lot ratio 1:2:1 confirmed",
        f"Net Credit: Rs{net_credit:.1f}" + (" ✅" if net_credit>0 else " ❌"),
        f"POP {pop_approx}%" + (" ✅" if pop_approx>=55 else " ⚠"),
        f"Max loss Rs{max_loss:.0f} within account risk",
        f"Both BEs noted: {lower_be} / {upper_be}",
        "Price alerts set at both BEs (on your terminal)",
        "No major event in 24h",
        "India VIX not spiking" + (f" (VIX={vc})" if vc else ""),
        "Adjustment plan ready for all 3 scenarios",
    ]
    for i, item in enumerate(cl_items, 1):
        col = RED if "⚠" in item or "❌" in item else GREEN
        print(f"  {C(str(i).zfill(2),GRAY)}  {C(item, col)}")
    print()

    # ── Section 16: Golden Rules reminder ─────────────────────────
    print(C("  ── Section 16: Golden Rules (key ones) ─────────────", CYAN))
    print(C("  GR4: NEVER wait for 100% profit. Exit at 50-70% systematically.", YELLOW))
    print(C("  GR5: Net Credit entry is non-negotiable for most trades.", YELLOW))
    print(C("  GR9: Know your breakevens BEFORE entering. Always.", YELLOW))
    print(C("  GR10: Max 2 adjustments/day. Third = emotional. Exit instead.", YELLOW))
    print()

    if input(C("  Confirm BWB entry (y/n): ", CYAN)).strip().lower() != "y": return

    # ── Store state ───────────────────────────────────────────────
    s.update({
        "bwb_bias":        bwb_bias,
        "bwb_opt_type":    opt_type,
        "bwb_center":      center_strike,
        "bwb_safe_strike": safe_strike,
        "bwb_wide_strike": wide_strike,
        "bwb_wide_dist":   wide_dist,
        "bwb_tight_dist":  tight_dist,
        "bwb_max_profit":  max_profit,
        "bwb_max_loss":    max_loss,
        "bwb_pop":         pop_approx,
        "net_credit":      net_credit,
        "upper_be":        upper_be,
        "lower_be":        lower_be,
        "breakeven_safe":  upper_be if bwb_bias=="bullish" else lower_be,
        "breakeven_wide":  lower_be if bwb_bias=="bullish" else upper_be,
        "entry_spot":      spot or center_strike,
        "current_spot":    spot or center_strike,
        "entry_time":      datetime.now().isoformat(),
        "phase":           "LIVE",
        "roll_count":      {"call":0,"put":0},
        "adjustments":     [],
        "pnl_snapshots":   [],
    })
    s["legs"] = {
        "broken_wing": {"strike":wide_strike,  "type":opt_type,"action":"buy", "lots":lots,  "entry_premium":wide_p,  "current_premium":wide_p},
        "center_sell1":{"strike":center_strike,"type":opt_type,"action":"sell","lots":lots,  "entry_premium":center_p,"current_premium":center_p},
        "center_sell2":{"strike":center_strike,"type":opt_type,"action":"sell","lots":lots,  "entry_premium":center_p,"current_premium":center_p},
        "safe_wing":   {"strike":safe_strike,  "type":opt_type,"action":"buy", "lots":lots,  "entry_premium":safe_p,  "current_premium":safe_p},
    }

    log_event(s, f"BWB {'Bullish Put' if bwb_bias=='bullish' else 'Bearish Call'}: "
                 f"SELL 2x{center_strike} @ Rs{center_p} | "
                 f"BUY {safe_strike} @ Rs{safe_p} | BUY {wide_strike} @ Rs{wide_p} | "
                 f"net=Rs{net_credit:.1f} BE={lower_be}-{upper_be} POP={pop_approx}%")
    save_state(s)

    print(); print(C("═"*66, GREEN))
    print(C(f"  ✅  BROKEN WING BUTTERFLY ENTERED  [{bwb_bias.upper()}]", GREEN+BOLD))
    print(C("═"*66, GREEN)); print()
    print(C(f"  Market must stay {'ABOVE' if bwb_bias=='bullish' else 'BELOW'} center for max profit.", CYAN+BOLD))
    print(C(f"  [GR2] Theta is your PRIMARY income — protect time value above all.", CYAN))
    print(C(f"  [GR4] Book at 50-70% profit. Never wait for 100%.", CYAN))
    print(C(f"  [Section 08] Monitor Greeks: Delta < 0.25, Theta > Rs50/day/lot", CYAN))
    print()
    show_position_summary(s)
    draw_payoff_chart(s)
    print(C("  Starting live 15-min candle-close monitor… CTRL+C=save  CTRL+E=emergency", CYAN))
    time.sleep(1)
    live_monitor_loop(s)


# ═══════════════════════════════════════════════════════════════════
#  BWB — BREACH / ADJUSTMENT GUIDANCE  (Section 07 + 09)
# ═══════════════════════════════════════════════════════════════════
def guide_bwb_breach(s, spot, side):
    bwb_bias   = s.get("bwb_bias","bullish")
    center     = s.get("bwb_center",0)
    wide_s     = s.get("bwb_wide_strike",0)
    safe_s     = s.get("bwb_safe_strike",0)
    nc         = s.get("net_credit",0)
    max_loss   = s.get("bwb_max_loss", abs(nc)*2)
    pnl        = update_live_premiums(s, spot)
    loss_pct   = round(abs(pnl)/max_loss*100,1) if pnl<0 and max_loss>0 else 0
    adj_count  = len([a for a in s.get("adjustments",[]) if not a.get("closed")])

    danger_side = "lower" if bwb_bias=="bullish" else "upper"
    danger = (side == danger_side)

    print()
    print(C(f"  ╔{'═'*60}╗", RED+BOLD if danger else YELLOW+BOLD))
    print(C(f"  ║  {'🚨' if danger else '⚠'}  BWB {'WIDE-WING' if danger else 'SAFE-WING'} SIDE BREACH ─────────────────────────║", RED+BOLD if danger else YELLOW+BOLD))
    print(C(f"  ╚{'═'*60}╝", RED+BOLD if danger else YELLOW+BOLD))
    print()
    print(f"  Spot {spot}  |  Center {center}  |  Wide {wide_s}  |  Safe {safe_s}")
    print(f"  Current P&L: {C(f'Rs {pnl:.1f}', GREEN if pnl>=0 else RED)}  |  Loss: {loss_pct:.1f}% of max loss")
    print()

    # ── Section 07 Scenario C — market moved against ──────────────
    print(C("  ── Section 07: Scenario C — Market Against Position ─", CYAN))
    print(C("  FIRST:  Roll profitable short strikes toward current price", YELLOW+BOLD))
    print(C("  SECOND: Convert to flatter structure — reduce delta", YELLOW))
    print(C("  THIRD:  Sell far OTM 10-15 delta options for recovery credit", YELLOW))
    print(C("  HARD STOP: Loss = 2% of account → close ENTIRE position [Section 13]", RED+BOLD))
    print()

    # 50% of max loss check [Section 07 Scenario C]
    if loss_pct >= 50:
        print(C(f"  🚨  50% of max loss hit (Rs{abs(pnl):.0f} / Rs{max_loss:.0f}) — ADJUST OR CLOSE [Scenario C]", RED+BOLD))

    # Adjustment limit check [Section 09, Golden Rule 10]
    if adj_count >= 2:
        print(C(f"  ❌  2 adjustments already used today — Section 09 limit reached.", RED+BOLD))
        print(C("  Golden Rule 10: Third adjustment = emotional. EXIT instead.", RED))
        if input(C("  Exit entire position? (y/n): ", CYAN)).strip().lower() == "y":
            expiry_close_wizard(s)
        return

    print(C("  ── Adjustment Options [Section 09] ─────────────────", CYAN))
    print()
    print(f"  {C('A', CYAN)}  Roll short strikes toward current price [Technique 1]")
    print(f"  {C('B', CYAN)}  Convert to normal butterfly — restore symmetry [Technique 2]")
    print(f"  {C('C', CYAN)}  Sell far OTM (10-15 delta) for recovery credit [Technique 4]")
    print(f"  {C('D', CYAN)}  Reduce size — strip 50% of position [Technique 5]")
    print(f"  {C('E', CYAN)}  Emergency adj exit — close adjustments only")
    print(f"  {C('X', CYAN)}  Exit entire position")
    print()
    print(C(f"  ⚠  [Section 09] Max 2 adjustments/day. Each needs a clear thesis.", YELLOW))
    print(C(f"  ⚠  Never adjust in last 30 min of session.", YELLOW))
    print()

    try:
        ch = input(C("  Action (A/B/C/D/E/X): ", CYAN)).strip().upper()
    except: ch = "X"

    if ch == "A":
        # Roll short strikes
        print()
        print(C("  Rolling short strikes [Section 09, Technique 1]:", YELLOW+BOLD))
        print(f"  Buy back current center shorts at {center}, re-sell closer to {spot}")
        new_center_sugg = int(round(spot/50)*50) if bwb_bias=="bullish" else int(round(spot/50)*50)
        try:
            buyback_p = float(input(C(f"  Buyback price for SELL {center} {s.get('bwb_opt_type','PE')} (×2): Rs ", CYAN)).strip())
            new_c     = int(input(C(f"  New center strike [{new_center_sugg}]: ", CYAN)).strip() or str(new_center_sugg))
            new_cp    = float(input(C(f"  New SELL {new_c} premium (×2): Rs ", CYAN)).strip())
        except: print(C("  ✗ Invalid.", RED)); return
        roll_cost = round(2*(buyback_p - new_cp), 2)
        if roll_cost > nc*0.30:
            print(C(f"  ⚠  Roll cost Rs{roll_cost:.0f} > 30% of credit Rs{nc:.0f} [Section 09C rule]", YELLOW))
        s["bwb_center"] = new_c
        s["legs"]["center_sell1"].update({"strike":new_c,"entry_premium":new_cp,"current_premium":new_cp})
        s["legs"]["center_sell2"].update({"strike":new_c,"entry_premium":new_cp,"current_premium":new_cp})
        log_event(s, f"BWB Roll: {center}→{new_c} buyback Rs{buyback_p:.0f} new Rs{new_cp:.0f}")
        save_state(s); draw_payoff_chart(s)
        print(C(f"  ✅  Rolled center to {new_c}. Adj #{adj_count+1}/2.", GREEN))

    elif ch == "B":
        print()
        print(C("  Converting to Normal Butterfly [Section 09, Technique 2]:", YELLOW+BOLD))
        print(f"  Buy back broken wing ({wide_s}) and replace with standard-width wing")
        std_wing_sugg = center - 200 if bwb_bias=="bullish" else center + 200
        try:
            buyback_wide = float(input(C(f"  Buyback broken wing {wide_s}: Rs ", CYAN)).strip())
            new_wing_s   = int(input(C(f"  New symmetric wing [{std_wing_sugg}]: ", CYAN)).strip() or str(std_wing_sugg))
            new_wing_p   = float(input(C(f"  New wing {new_wing_s} premium: Rs ", CYAN)).strip())
        except: print(C("  ✗ Invalid.", RED)); return
        s["legs"]["broken_wing"].update({"closed":True,"close_premium":buyback_wide})
        s["legs"]["broken_wing_new"] = {"strike":new_wing_s,"type":s.get("bwb_opt_type","PE"),
                                         "action":"buy","lots":1,"entry_premium":new_wing_p,"current_premium":new_wing_p}
        log_event(s, f"BWB→Butterfly: wide {wide_s}→{new_wing_s}")
        save_state(s); draw_payoff_chart(s)
        print(C(f"  ✅  Converted to standard Butterfly. Position symmetry restored.", GREEN))

    elif ch == "C":
        print()
        print(C("  OTM Recovery [Section 09, Technique 4 / Section 12C]:", YELLOW+BOLD))
        print(C("  Only sell 10-15 delta options. NEVER ATM. [Section 14]", RED))
        loss_amt = abs(pnl) * s.get("lot_size",75)
        recovery_per_lot = round(loss_amt / max(dte:=s.get("dte_at_entry",5), 1), 0)
        print(f"  Loss to recover: Rs{loss_amt:.0f} over ~{dte} days = Rs{recovery_per_lot:.0f}/day")
        print(f"  Max lots for OTM sell: 5× original = {5} lots max [Section 13]")
        try:
            otm_s = int(input(C(f"  OTM strike to sell (10-15 delta, far from spot): ", CYAN)).strip())
            otm_p = float(input(C(f"  OTM {otm_s} premium: Rs ", CYAN)).strip())
            otm_lots = int(input(C(f"  Lots [1]: ", CYAN)).strip() or "1")
        except: print(C("  ✗ Invalid.", RED)); return
        ot = s.get("bwb_opt_type","PE")
        s["adjustments"].append({"type":f"OTM Recovery","strike":otm_s,"action":"sell",
                                  "entry_premium":otm_p,"current_premium":otm_p,"lots":otm_lots,
                                  "opt_type":ot,"closed":False,"ts":datetime.now().isoformat()})
        log_event(s, f"BWB OTM recovery: sell {otm_s} {ot} @ Rs{otm_p:.0f} ×{otm_lots}")
        save_state(s); draw_payoff_chart(s)
        print(C(f"  ✅  Recovery sell recorded. Monitor {otm_s} closely.", GREEN))

    elif ch == "D":
        print()
        print(C("  Reducing position size by 50% [Section 09, Technique 5]:", YELLOW+BOLD))
        print(C("  Close 1 of every 2 lots proportionally.", GRAY))
        for key, leg in s["legs"].items():
            if leg and not leg.get("closed"):
                print(f"  {key}: {leg['type']} {leg['strike']} — entry Rs{leg['entry_premium']:.1f}")
        if input(C("  Confirm size reduction (y/n): ", CYAN)).strip().lower() == "y":
            log_event(s, "BWB size reduction: 50%")
            save_state(s)
            print(C("  ✅  Recorded. Close 50% of each leg in your broker.", GREEN))

    elif ch == "E":
        emergency_adj_exit(s, spot)

    elif ch == "X":
        print()
        print(C("  Exiting entire BWB position.", RED+BOLD))
        expiry_close_wizard(s)

    else:
        print(C("  ✗ Invalid.", RED))


# ═══════════════════════════════════════════════════════════════════
#  SHORT STRANGLE — SETUP & MANAGEMENT  (Theta Gainers Rule Book)
#  Premium Matching Adjustment Method | Control Drawdown | Harvest Theta
# ═══════════════════════════════════════════════════════════════════
def strangle_setup(s):
    hdr("SHORT STRANGLE — SETUP  (Premium Matching Adjustment Rule Book)")

    spot = s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI"))
    vc   = s.get("vix_current", 17)

    if spot:
        print(f"  Live Spot   : {C(B(str(spot)), GREEN)}")
        print(f"  India VIX   : {C(str(vc), YELLOW)}")
    print()

    # ── Section 2: Entry Rules ────────────────────────────────────
    print(C("  ── Section 2: Entry Rules & Market Filters ────────", CYAN))
    print(C("  Golden Rule: NEVER TOUCH THE LOSING SIDE [Section 3]", RED+BOLD))
    print(C("  Only adjust the profitable (winning) side.", RED))
    print()

    # Market condition check
    print(C("  Entry Conditions:", YELLOW+BOLD))
    print(f"  □  DTE: 7-21 days preferred (you have {s.get('dte_at_entry','N/A')} DTE)")
    print(f"  □  VIX normal, not spiking (current VIX={vc})")
    print(f"  □  Avoid entry on event days (RBI, Budget, Fed)")
    print(f"  □  Trade on Thursday (previous expiry) or Monday open")
    print()

    if input(C("  All entry conditions confirmed (y/n): ", CYAN)).strip().lower() != "y":
        print(C("  ⛔  Entry conditions not met. SKIP THIS TRADE.", RED+BOLD)); return

    # Expiry selection
    expiry_date, dte = select_expiry()
    s["expiry_date"]  = expiry_date
    s["dte_at_entry"] = dte

    # ── Strike Selection ──────────────────────────────────────────
    print()
    print(C("  ── Strike Selection ─────────────────────────────────", CYAN))
    print(C("  Section 2: Sell OTM Call and OTM Put simultaneously.", GRAY))
    print(C("  CRITICAL: Entry premiums must be APPROXIMATELY EQUAL.", YELLOW+BOLD))
    print(C("  Premium Matching = equal premium, not equal distance.", YELLOW))
    print()

    if spot:
        put_sugg = int(round((spot - 300) / 50) * 50)
        call_sugg= int(round((spot + 300) / 50) * 50)
        print(f"  Suggested structure:")
        print(f"  SELL PUT  : {C(str(put_sugg)+' PE', CYAN)}  ({round(spot-put_sugg)}pts OTM)")
        print(f"  SELL CALL : {C(str(call_sugg)+' CE', CYAN)}  ({round(call_sugg-spot)}pts OTM)")
    else:
        put_sugg = call_sugg = 0

    try:
        raw = input(C(f"  SHORT PUT strike [{put_sugg}]: ", CYAN)).strip()
        short_put = int(raw) if raw else put_sugg
        raw = input(C(f"  SHORT CALL strike [{call_sugg}]: ", CYAN)).strip()
        short_call = int(raw) if raw else call_sugg
    except: print(C("  ✗ Invalid.", RED)); return

    # ── Premiums — MUST be equal or very close ────────────────────
    print()
    print(C("  ── Premiums [Section 2: Equal premium at entry] ────", CYAN))
    try:
        put_prem = float(input(C(f"  SHORT PUT {short_put} PE premium  : Rs ", CYAN)).strip())
        call_prem= float(input(C(f"  SHORT CALL {short_call} CE premium : Rs ", CYAN)).strip())
    except: print(C("  ✗ Invalid.", RED)); return

    net_credit = round(put_prem + call_prem, 2)

    # Premium matching validation
    prem_diff = abs(put_prem - call_prem)
    prem_avg = (put_prem + call_prem) / 2
    prem_diff_pct = round(prem_diff / prem_avg * 100, 1) if prem_avg > 0 else 0

    print()
    print(C("  ── Premium Matching Validation [Section 6] ─────────", CYAN))
    print(f"  Put premium  : Rs {put_prem:.1f}")
    print(f"  Call premium : Rs {call_prem:.1f}")
    print(f"  Difference   : Rs {prem_diff:.1f}  ({prem_diff_pct}% of average)")
    print(f"  Total Credit : Rs {net_credit:.1f}")

    if prem_diff_pct > 15:
        print(C(f"  ⚠  Premium difference {prem_diff_pct}% > 15% — NOT balanced.", YELLOW))
        if input(C("  Continue anyway (y/n): ", CYAN)).strip().lower() != "y": return
    else:
        print(C(f"  ✅  Premiums balanced within {prem_diff_pct}%", GREEN))

    # ── Lots ──────────────────────────────────────────────────────
    try:
        lots = int(input(C("  Number of lots [1]: ", CYAN)).strip() or "1")
    except: lots = 1

    # Loss & adjustment thresholds
    max_loss_approx = (abs(short_call - short_put) - net_credit) * s.get("lot_size",75)
    adj_trigger_put = round(put_prem * 0.30, 2)   # 70% decay
    adj_trigger_call = round(call_prem * 2.0, 2)  # 2x loss
    stop_loss_3x = round(net_credit * 3, 2)

    # ── Risk Management [Section 11] ──────────────────────────────
    print()
    print(C("  ── Section 11: Risk Management ──────────────────────", CYAN))
    print(f"  Max loss (approx) : Rs {max_loss_approx:.0f}  (width - credit)")
    print(f"  Max risk/trade    : 1-2% of capital")
    print(f"  Adj trigger (Put) : Rs {adj_trigger_put:.1f}  (70% decay from Rs {put_prem:.1f})")
    print(f"  Loss trigger      : Rs {adj_trigger_call:.1f}  (2x entry premium)")
    print(f"  Hard stop (3x)    : Rs {stop_loss_3x:.1f}  (3x total credit)")
    print()

    try:
        cap = float(input(C("  Total capital Rs (ENTER to skip): ", CYAN)).strip() or "0")
    except: cap = 0
    if cap > 0:
        risk_pct = max_loss_approx / cap * 100
        col = GREEN if risk_pct <= 2 else YELLOW if risk_pct <= 5 else RED
        print(C(f"  Risk % : {risk_pct:.1f}% of capital", col))

    # ── Entry Checklist [Section 2] ──────────────────────────────
    print()
    print(C("  ── Pre-Trade Checklist ──────────────────────────────", CYAN))
    cl = [
        "NIFTY or BankNifty selected",
        f"DTE {dte} days between 7-21",
        f"Sell OTM Call {short_call} + Put {short_put}",
        f"Entry premiums balanced: {put_prem:.1f} vs {call_prem:.1f}",
        f"Net credit Rs {net_credit:.1f}",
        "No major event in 24h",
        "VIX normal (not spiking)",
        "Max loss understood and within risk limits",
        "Adjustment plan ready (premium matching method)",
        "Golden Rule understood: NEVER adjust losing side",
    ]
    for i, item in enumerate(cl, 1):
        print(f"  {C(str(i).zfill(2), GRAY)}  {C(item, GREEN)}")

    print()
    if input(C("  Confirm Short Strangle entry (y/n): ", CYAN)).strip().lower() != "y": return

    # ── Store state ───────────────────────────────────────────────
    s.update({
        "strangle_short_put":        short_put,
        "strangle_short_call":       short_call,
        "strangle_entry_put_prem":   put_prem,
        "strangle_entry_call_prem":  call_prem,
        "strangle_short_put_curr":   put_prem,
        "strangle_short_call_curr":  call_prem,
        "net_credit":                net_credit,
        "strangle_stop_loss":        stop_loss_3x,
        "strangle_max_loss":         max_loss_approx,
        "entry_spot":                spot or (short_put+short_call)/2,
        "current_spot":              spot or (short_put+short_call)/2,
        "entry_time":                datetime.now().isoformat(),
        "phase":                     "LIVE",
        "adjustments":               [],
        "pnl_snapshots":             [],
    })
    s["legs"] = {
        "short_put":  {"strike":short_put, "type":"PE","action":"sell","lots":lots,"entry_premium":put_prem, "current_premium":put_prem},
        "short_call": {"strike":short_call,"type":"CE","action":"sell","lots":lots,"entry_premium":call_prem,"current_premium":call_prem},
    }

    log_event(s, f"Short Strangle: SELL {short_put} PE @ Rs{put_prem} + {short_call} CE @ Rs{call_prem} = "
                 f"Rs{net_credit} credit | SL={stop_loss_3x} | expiry={expiry_date}")
    save_state(s)

    print(); print(C("═"*66, GREEN))
    print(C(f"  ✅  SHORT STRANGLE ENTERED", GREEN+BOLD))
    print(C("═"*66, GREEN)); print()
    print(C(f"  Golden Rule: NEVER adjust the LOSING side. [Section 3]", RED+BOLD))
    print(C(f"  Adjust only the profitable (winning) side.", RED))
    print(C(f"  Premium matching: both legs must have equal premium again after adjustment.", CYAN))
    print(C(f"  Max 3 adjustments per expiry. After 3rd → convert to Iron Fly or exit. [Section 11]", CYAN))
    print()
    show_position_summary(s)
    draw_payoff_chart(s)
    print(C("  Starting live 15-min candle-close monitor… CTRL+C=save  CTRL+E=emergency", CYAN))
    time.sleep(1)
    live_monitor_loop(s)


# ═══════════════════════════════════════════════════════════════════
#  SHORT STRANGLE — ADJUSTMENT GUIDANCE  (Premium Matching Method)
# ═══════════════════════════════════════════════════════════════════
def guide_strangle_adj(s, spot, side):
    """
    Premium Matching Adjustment for Short Strangle.
    Section 4: Trigger when profitable leg decays 60-70% OR losing leg doubles (2x).
    Section 5: Buy back profitable, sell new option matching losing side's premium.
    Golden Rule: NEVER touch losing side.
    """
    short_put = s.get("strangle_short_put", 0)
    short_call= s.get("strangle_short_call",0)
    entry_put_p= s.get("strangle_entry_put_prem", 0)
    entry_call_p=s.get("strangle_entry_call_prem",0)
    curr_put_p = s.get("strangle_short_put_curr", entry_put_p)
    curr_call_p= s.get("strangle_short_call_curr",entry_call_p)
    net_credit = s.get("net_credit", entry_put_p + entry_call_p)
    adj_count  = len([a for a in s.get("adjustments",[]) if not a.get("closed")])

    print()
    print(C("  ╔"+"═"*60+"╗", YELLOW+BOLD))
    print(C("  ║  ⚡  SHORT STRANGLE ADJUSTMENT TRIGGER  ─────────────────║", YELLOW+BOLD))
    print(C("  ╚"+"═"*60+"╝", YELLOW+BOLD))
    print()
    print(f"  SHORT PUT {short_put} : entry Rs{entry_put_p:.1f} → current Rs{curr_put_p:.1f}")
    print(f"  SHORT CALL {short_call} : entry Rs{entry_call_p:.1f} → current Rs{curr_call_p:.1f}")
    print()

    # ── Section 4: Trigger Identification ───────────────────────
    print(C("  ── Section 4: Adjustment Trigger Rules ────────────", CYAN))

    # Check PUT side
    put_decay_pct = round((entry_put_p - curr_put_p) / entry_put_p * 100, 1) if entry_put_p > 0 else 0
    put_triggered = curr_put_p <= entry_put_p * 0.30  # 70% decay

    # Check CALL side
    call_loss_pct = round((curr_call_p - entry_call_p) / entry_call_p * 100, 1) if entry_call_p > 0 else 0
    call_triggered = curr_call_p >= entry_call_p * 2.0  # 2x

    print(f"  PUT:  {put_decay_pct:.0f}% decayed → Trigger: {C('YES (70% decay)', GREEN if put_triggered else GRAY)}")
    print(f"  CALL: {call_loss_pct:+.0f}% change → Trigger: {C('YES (2x loss)', RED if call_triggered else GRAY)}")
    print()

    if adj_count >= 3:
        print(C(f"  ❌  3 adjustments already made [Section 11, Rule: max 3/expiry]", RED+BOLD))
        print(C("  Options: (1) Convert to Iron Fly (Section 9), OR (2) EXIT position", RED))
        if input(C("  Convert to Iron Fly or exit? (1=Iron Fly / 2=Exit): ", CYAN)).strip() == "1":
            print(C("  Recording Iron Fly conversion (add ±100pt wings to shorts).", YELLOW))
        else:
            expiry_close_wizard(s)
        return

    # ── Section 5: Premium Matching Adjustment ──────────────────
    print(C("  ── Section 5: Premium Matching Adjustment Method ──", CYAN))

    if put_triggered:
        print(C("  ✅  PUT PROFITABLE — Buy back & sell premium-matched option", GREEN+BOLD))
        print(f"  Step 1: BUY BACK put at Rs{curr_put_p:.1f} → profit Rs{entry_put_p - curr_put_p:.1f}")
        try:
            buyback_p = float(input(C(f"  Buyback price for PUT {short_put} [Rs{curr_put_p:.1f}]: ", CYAN)).strip() or str(curr_put_p))
            profit = round(entry_put_p - buyback_p, 2)
        except: print(C("  ✗ Invalid.", RED)); return

        print()
        print(f"  Step 3: FIND MATCH for CALL premium Rs{curr_call_p:.1f}")
        print(C("  Section 6: Match premiums, NOT delta, NOT distance.", GRAY+BOLD))
        match_range_lo = round(curr_call_p * 0.85, 1)
        match_range_hi = round(curr_call_p * 1.15, 1)
        print(f"  Valid range: Rs {match_range_lo} - Rs {match_range_hi}")
        print(C("  Look for OTM PUT option with premium in this range.", GRAY))
        print()
        try:
            new_put = int(input(C(f"  New PUT strike to sell (OTM, should be lower than {short_put}): ", CYAN)).strip())
            new_put_p = float(input(C(f"  New PUT {new_put} premium to collect [Rs{curr_call_p:.1f}±]: ", CYAN)).strip())
        except: print(C("  ✗ Invalid.", RED)); return

        if new_put_p < match_range_lo or new_put_p > match_range_hi:
            print(C(f"  ⚠  Premium Rs{new_put_p:.1f} outside match range Rs{match_range_lo}-{match_range_hi}", YELLOW))
            if input(C("  Force this match (y/n): ", CYAN)).strip().lower() != "y": return

        # Record adjustment
        s["adjustments"].append({
            "type": "Premium Match Adj", "action": "buyback_sell",
            "buy_strike": short_put, "buy_prem": buyback_p,
            "sell_strike": new_put, "sell_prem": new_put_p,
            "profit": profit, "closed": False,
            "ts": datetime.now().isoformat()
        })
        # Update state
        s["strangle_short_put"] = new_put
        s["strangle_short_put_curr"] = new_put_p
        adj_count += 1

        log_event(s, f"Strangle Adj #{adj_count}: Bought {short_put} @ Rs{buyback_p} profit Rs{profit:.1f}. "
                     f"Sold {new_put} @ Rs{new_put_p:.1f}")
        save_state(s); draw_payoff_chart(s)
        print(C(f"  ✅  Adjustment recorded. New structure: SELL {s['strangle_short_call']} CE + {new_put} PE. "
                f"Adj #{adj_count}/3.", GREEN))

    elif call_triggered:
        print(C("  ❌  CALL LOSING — Buy back & sell premium-matched option", RED+BOLD))
        print(f"  Step 1: BUY BACK call at Rs{curr_call_p:.1f} → loss Rs{-(entry_call_p - curr_call_p):.1f}")
        try:
            buyback_p = float(input(C(f"  Buyback price for CALL {short_call} [Rs{curr_call_p:.1f}]: ", CYAN)).strip() or str(curr_call_p))
            loss = round(entry_call_p - buyback_p, 2)
        except: print(C("  ✗ Invalid.", RED)); return

        print()
        print(f"  Step 3: FIND MATCH for PUT premium Rs{curr_put_p:.1f}")
        match_range_lo = round(curr_put_p * 0.85, 1)
        match_range_hi = round(curr_put_p * 1.15, 1)
        print(f"  Valid range: Rs {match_range_lo} - Rs {match_range_hi}")
        print()
        try:
            new_call = int(input(C(f"  New CALL strike to sell (OTM, should be higher than {short_call}): ", CYAN)).strip())
            new_call_p = float(input(C(f"  New CALL {new_call} premium to collect [Rs{curr_put_p:.1f}±]: ", CYAN)).strip())
        except: print(C("  ✗ Invalid.", RED)); return

        if new_call_p < match_range_lo or new_call_p > match_range_hi:
            print(C(f"  ⚠  Premium Rs{new_call_p:.1f} outside match range Rs{match_range_lo}-{match_range_hi}", YELLOW))

        s["adjustments"].append({
            "type": "Premium Match Adj", "action": "buyback_sell",
            "buy_strike": short_call, "buy_prem": buyback_p,
            "sell_strike": new_call, "sell_prem": new_call_p,
            "loss": loss, "closed": False,
            "ts": datetime.now().isoformat()
        })
        s["strangle_short_call"] = new_call
        s["strangle_short_call_curr"] = new_call_p
        adj_count += 1

        log_event(s, f"Strangle Adj #{adj_count}: Bought {short_call} @ Rs{buyback_p} loss Rs{loss:.1f}. "
                     f"Sold {new_call} @ Rs{new_call_p:.1f}")
        save_state(s); draw_payoff_chart(s)
        print(C(f"  ✅  Adjustment recorded. New structure: SELL {new_call} CE + {s['strangle_short_put']} PE. "
                f"Adj #{adj_count}/3.", GREEN))

    else:
        print(C("  No trigger conditions met. Holding position. [Section 3: Rule-based, emotion-free]", GRAY))


# ═══════════════════════════════════════════════════════════════════
#  CALENDAR SPREAD — SETUP  (VIX-Based, Theta + Vega Play)
#  14 Sections | Time Decay Focused | Volatility Adaptive
# ═══════════════════════════════════════════════════════════════════
def calendar_setup(s):
    hdr("CALENDAR SPREAD — SETUP  (VIX-Based Theta + Vega Play)")

    spot = s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI"))
    vc   = s.get("vix_current", 17)

    if spot:
        print(f"  Live Spot   : {C(B(str(spot)), GREEN)}")
        print(f"  India VIX   : {C(str(vc), YELLOW)}")
    print()

    # ── Section 01: What is a Calendar Spread? ─────────────────────
    print(C("  ── Section 01: What is a Calendar Spread? ────────", CYAN))
    print(C("  Exploits TIME DECAY (Theta) + IV difference between near/far legs.", YELLOW+BOLD))
    print(C("  Near leg expires quickly (collects premium fast).", YELLOW))
    print(C("  Far leg is bought as hedge (captures vol changes).", YELLOW))
    print(C("  Tent-shaped profit zone, defined risk on both sides.", CYAN))
    print()

    # ── Section 02 & 03: VIX Classification ────────────────────────
    print(C("  ── Section 02 & 03: VIX Classification ────────────", CYAN))
    print(C("  India VIX determines your calendar type:", YELLOW+BOLD))
    print()

    # Classify VIX zone
    if vc and float(vc) >= 20:
        vix_zone = "HIGH"
        cal_type = "CREDIT"
        target_prem_lo, target_prem_hi = 40, 45
        strategy_note = "VIX expected to revert down. Sell aggressively."
        print(f"  VIX = {vc}  →  {C('HIGH (20-30)', RED+BOLD)} → {C('CREDIT CALENDAR', RED)}")
    elif vc and float(vc) >= 13:
        vix_zone = "AVERAGE"
        cal_type = "BALANCED"
        target_prem_lo, target_prem_hi = 28, 35
        strategy_note = "VIX neutral. Stay balanced on both Call + Put sides."
        print(f"  VIX = {vc}  →  {C('AVERAGE (13-20)', YELLOW+BOLD)} → {C('BALANCED CALENDAR', YELLOW)}")
    else:
        vix_zone = "LOW"
        cal_type = "DEBIT"
        target_prem_lo, target_prem_hi = 22, 25
        strategy_note = "VIX historically bounces from lows. Small debit acceptable."
        print(f"  VIX = {vc}  →  {C('LOW (9-12)', GREEN+BOLD)} → {C('DEBIT CALENDAR', GREEN)}")

    print()
    print(C(f"  Calendar Type: {cal_type}", CYAN+BOLD))
    print(C(f"  Target premium per sold leg: Rs {target_prem_lo}–{target_prem_hi}", CYAN))
    print(C(f"  Strategy: {strategy_note}", GRAY))
    print()

    # ── Section 08: Deployment Rules (Timing, Instrument) ──────────
    print(C("  ── Section 08: Deployment Rules ───────────────────", CYAN))
    print(C("  Deploy: Monday or Tuesday of expiry week (max theta)", GRAY))
    print(C("  Avoid: Expiry day, major events, > 2% pre-move", GRAY))
    print(C("  Prefer: NIFTY for better liquidity + easier adjustment", GRAY))
    print()

    if input(C("  Proceed with {} Calendar entry (y/n): ".format(cal_type), CYAN)).strip().lower() != "y":
        return

    # ── Near-expiry selection ──────────────────────────────────────
    expiry_near, dte_near = select_expiry()
    print()
    print(C("  ── Select Far-Expiry (Hedge Leg) ──────────────────", CYAN))
    print(C("  Far leg: typically NEXT weekly (1-2 weeks out)", GRAY))
    print()

    try:
        weeks_out = int(input(C("  Weeks out for far leg [2]: ", CYAN)).strip() or "2")
    except: weeks_out = 2

    # Calculate far expiry (roughly weeks_out weeks later)
    from datetime import datetime, timedelta
    try:
        near_dt = datetime.strptime(expiry_near, "%Y-%m-%d").date()
        far_dt = near_dt + timedelta(weeks=weeks_out)
        expiry_far = str(far_dt)
        dte_far = (far_dt - datetime.now().date()).days
    except:
        expiry_far = expiry_near
        dte_far = dte_near
        print(C("  ⚠ Using same expiry for far leg", YELLOW))

    print(C(f"  Near expiry: {expiry_near} ({dte_near} DTE)", CYAN))
    print(C(f"  Far expiry:  {expiry_far} ({dte_far} DTE)", CYAN))
    print()

    # ═══ NEW: SECTION 05 MOVED HERE — Premium Selection FIRST ═══
    print(C("  ── Section 05: Premium Targets (Select BEFORE strikes) ─", CYAN+BOLD))
    print(C(f"  Target: Rs {target_prem_lo}–{target_prem_hi} per sold leg", YELLOW+BOLD))
    print(C("  Use these as guides when selecting strikes next.", GRAY))
    print()

    try:
        target_call_p = float(input(C(f"  Target SELL CALL premium [{target_prem_lo}]: ", CYAN)).strip() or str(target_prem_lo))
        target_put_p  = float(input(C(f"  Target SELL PUT premium [{target_prem_lo}]: ", CYAN)).strip() or str(target_prem_lo))
    except:
        target_call_p = target_prem_lo
        target_put_p = target_prem_lo

    print(C(f"  ✓ Target premiums: CALL Rs{target_call_p:.0f}  PUT Rs{target_put_p:.0f}", GREEN))
    print(C("  Now find strikes that match these target premiums in your broker's chain.", GRAY))
    print()

    # ── Section 06 & 07: Strike Selection ──────────────────────────
    print(C("  ── Section 06 & 07: Strike Selection ───────────────", CYAN))
    print(C("  Find strikes with premiums matching targets above", YELLOW+BOLD))
    print(C("  Sell near-expiry at ATM or slightly OTM", GRAY))
    print(C("  Buy far-expiry as hedge (same or wider strikes)", GRAY))
    print()

    if spot:
        atm_call = int(round(spot / 50) * 50)
        atm_put  = int(round(spot / 50) * 50)
        print(f"  ATM level for {spot}: {C(str(atm_call), CYAN)}")
        print(C("  Tip: Start at ATM, adjust ±1-2 strikes to match target premiums.", GRAY))
    else:
        atm_call = atm_put = 0

    try:
        near_call = int(input(C(f"  Near-expiry SELL CALL (target Rs{target_call_p:.0f}) [{atm_call}]: ", CYAN)).strip() or str(atm_call))
        near_put  = int(input(C(f"  Near-expiry SELL PUT (target Rs{target_put_p:.0f}) [{atm_put}]: ", CYAN)).strip() or str(atm_put))
        far_call  = int(input(C(f"  Far-expiry BUY CALL (hedge) [{near_call}]: ", CYAN)).strip() or str(near_call))
        far_put   = int(input(C(f"  Far-expiry BUY PUT (hedge) [{near_put}]: ", CYAN)).strip() or str(near_put))
    except:
        print(C("  ✗ Invalid.", RED)); return

    # ── Actual premiums entered now (matched to target) ──────────────
    print()
    print(C("  ── Section 05: Actual Premiums (Validation) ───────", CYAN))
    print(C("  Enter actual market premiums from your broker:", YELLOW))
    print()

    try:
        near_call_p = float(input(C(f"  SELL CALL {near_call} actual premium (Rs): ", CYAN)).strip())
        near_put_p  = float(input(C(f"  SELL PUT {near_put} actual premium (Rs): ", CYAN)).strip())
        far_call_p  = float(input(C(f"  BUY CALL {far_call} actual premium (Rs): ", CYAN)).strip())
        far_put_p   = float(input(C(f"  BUY PUT {far_put} actual premium (Rs): ", CYAN)).strip())
    except:
        print(C("  ✗ Invalid.", RED)); return

    # Calculate net premium
    near_credit = near_call_p + near_put_p
    far_debit   = far_call_p + far_put_p
    net_premium = near_credit - far_debit

    print()
    print(C("  ── Premium Summary ────────────────────────────────", CYAN))
    print(f"  Near sell credit : Rs {near_credit:.1f}  (CALL {near_call_p:.1f} + PUT {near_put_p:.1f})")
    print(f"  Far buy debit    : Rs {far_debit:.1f}   (CALL {far_call_p:.1f} + PUT {far_put_p:.1f})")
    print(f"  Net premium      : Rs {net_premium:.1f}  {C('(CREDIT)', GREEN if net_premium>0 else RED)}")
    print()

    # ── Check if premium targets were met ────────────────────────────
    call_var = abs(near_call_p - target_call_p)
    put_var = abs(near_put_p - target_put_p)
    print(C("  ── Target Match Validation ────────────────────────", CYAN))
    print(f"  CALL: Target Rs{target_call_p:.0f}  Actual Rs{near_call_p:.1f}  Variance: Rs{call_var:.1f}")
    print(f"  PUT:  Target Rs{target_put_p:.0f}  Actual Rs{near_put_p:.1f}  Variance: Rs{put_var:.1f}")
    if call_var <= 5 and put_var <= 5:
        print(C("  ✅  Premiums within acceptable range (±5%)", GREEN))
    elif call_var <= 10 and put_var <= 10:
        print(C("  ✓  Premiums within acceptable range (±10%)", YELLOW))
    else:
        print(C("  ⚠  Premiums > 10% from target — consider different strikes", YELLOW))
    print()

    if net_premium < 0 and cal_type != "DEBIT":
        print(C(f"  ⚠  Net debit Rs {abs(net_premium):.1f} — not ideal for {cal_type} calendar.", YELLOW))

    # ── Section 12: Risk Management ────────────────────────────────
    print(C("  ── Section 12: Risk Management ───────────────────", CYAN))
    max_loss = max(abs(net_premium) * 2, (abs(near_call - near_put) - net_premium))
    print(f"  Max loss (estimated) : Rs {max_loss:.0f}")
    print(f"  Profit target (40-50%): Rs {net_premium * 0.45:.0f}")
    print(f"  Stop loss (100% of premium): {C(f'Rs {abs(net_premium):.0f}', RED)}")
    print()

    try: lots = int(input(C("  Number of lots [1]: ", CYAN)).strip() or "1")
    except: lots = 1

    if input(C("  Confirm CALENDAR SPREAD entry (y/n): ", CYAN)).strip().lower() != "y": return

    # ── Store state ───────────────────────────────────────────────
    s.update({
        "cal_type":           cal_type,
        "cal_vix_zone":       vix_zone,
        "cal_vix_entry":      float(vc) if vc else 17,
        "cal_near_call":      near_call,
        "cal_near_put":       near_put,
        "cal_far_call":       far_call,
        "cal_far_put":        far_put,
        "cal_sold_strike":    near_call,  # primary sold strike for breach detection
        "cal_sold_prem":      near_call_p,
        "cal_net_premium":    net_premium,
        "net_premium":        net_premium,
        "net_credit":         abs(net_premium),
        "upper_be":           max(near_call, near_put),
        "lower_be":           min(near_call, near_put),
        "cal_max_loss":       max_loss,
        "expiry_date":        expiry_near,
        "dte_at_entry":       dte_near,
        "cal_far_expiry":     expiry_far,
        "cal_dte_far":        dte_far,
        "entry_spot":         spot or 0,
        "current_spot":       spot or 0,
        "entry_time":         datetime.now().isoformat(),
        "phase":              "LIVE",
        "adjustments":        [],
        "pnl_snapshots":      [],
    })
    s["legs"] = {
        "near_call_sell": {"strike":near_call,"type":"CE","action":"sell","expiry":expiry_near,
                          "lots":lots,"entry_premium":near_call_p,"current_premium":near_call_p},
        "near_put_sell":  {"strike":near_put, "type":"PE","action":"sell","expiry":expiry_near,
                          "lots":lots,"entry_premium":near_put_p, "current_premium":near_put_p},
        "far_call_buy":   {"strike":far_call, "type":"CE","action":"buy", "expiry":expiry_far,
                          "lots":lots,"entry_premium":far_call_p, "current_premium":far_call_p},
        "far_put_buy":    {"strike":far_put,  "type":"PE","action":"buy", "expiry":expiry_far,
                          "lots":lots,"entry_premium":far_put_p,  "current_premium":far_put_p},
    }

    log_event(s, f"Calendar Spread ({cal_type}): Near SELL {near_call}C@Rs{near_call_p:.0f} + {near_put}P@Rs{near_put_p:.0f} | "
                 f"Far BUY {far_call}C@Rs{far_call_p:.0f} + {far_put}P@Rs{far_put_p:.0f} | "
                 f"Net={net_premium:.0f} VIX={vix_zone}")
    save_state(s)

    print(); print(C("═"*66, GREEN))
    print(C(f"  ✅  CALENDAR SPREAD ENTERED  ({cal_type})", GREEN+BOLD))
    print(C("═"*66, GREEN))
    print(C(f"  VIX Zone: {vix_zone}  ({vc})  |  Type: {cal_type}", CYAN+BOLD))
    print(C(f"  Near: {expiry_near} ({dte_near} DTE)  |  Far: {expiry_far} ({dte_far} DTE)", CYAN))
    print()
    print(C(f"  [Section 01] Theta + Vega play — benefits from time decay + volatility moves", CYAN))
    print(C(f"  [Section 12] Exit at 40-50% profit target or 100% loss — do NOT hold to expiry", CYAN))
    print(C(f"  [Section 09] Max 1 adjustment per expiry — if in doubt, close and redeploy", CYAN))
    print()
    show_position_summary(s)
    draw_calendar_payoff_chart(s)
    print(C("  Starting live 15-min candle-close monitor… CTRL+C=save  CTRL+E=emergency", CYAN))
    time.sleep(1)
    live_monitor_loop(s)


# ═══════════════════════════════════════════════════════════════════
#  CALENDAR SPREAD — ADJUSTMENT GUIDANCE  (Section 09)
# ═══════════════════════════════════════════════════════════════════
def guide_calendar_adj(s, spot, side):
    """
    Calendar adjustment: Roll sold legs when they breach, or close entire position.
    Section 09: When & How to Adjust.
    """
    cal_type = s.get("cal_type", "BALANCED")
    near_call = s.get("cal_near_call", 0)
    near_put = s.get("cal_near_put", 0)
    sold_prem = s.get("cal_sold_prem", 0)
    net_prem = s.get("cal_net_premium", 0)
    vix_entry = s.get("cal_vix_entry", 17)
    vc_now = s.get("vix_current", 17)
    pnl = update_live_premiums(s, spot)
    dte_near = s.get("dte_at_entry", 7)

    print()
    print(C(f"  ╔{'═'*60}╗", YELLOW+BOLD))
    print(C(f"  ║  ⚠  CALENDAR SPREAD ADJUSTMENT GUIDANCE  ───────────║", YELLOW+BOLD))
    print(C(f"  ╚{'═'*60}╝", YELLOW+BOLD))
    print()
    print(f"  Calendar Type: {cal_type}  |  VIX entry: {vix_entry}  |  VIX now: {vc_now}")
    print(f"  Near expiry DTE: {dte_near}  |  Current P&L: {C(f'Rs {pnl:.1f}', GREEN if pnl>=0 else RED)}")
    print()

    # ── Section 09: When to Adjust ──────────────────────────────────
    print(C("  ── Section 09: When to Adjust ─────────────────────", CYAN))

    breach_cond1 = spot >= near_call - sold_prem or spot <= near_put + sold_prem
    breach_cond2 = vc_now > vix_entry * 1.3
    breach_cond3 = dte_near <= 2
    breach_cond4 = pnl <= -abs(net_prem)

    if breach_cond1: print(f"  ✓ Market moved {sold_prem:.0f}pts from sold strike")
    if breach_cond2: print(f"  ✓ VIX spiked {round((vc_now/vix_entry - 1)*100)}% from entry")
    if breach_cond3: print(f"  ✓ Less than {dte_near} days to near-expiry")
    if breach_cond4: print(f"  ✓ Loss = 100% of premium collected (hard stop)")

    print()
    if not (breach_cond1 or breach_cond2 or breach_cond3):
        print(C("  ⏳  No adjustment trigger. Hold position, collect theta.", GRAY))
        return

    print(C("  ── Adjustment Options ─────────────────────────────", CYAN))
    print(f"  {C('R', CYAN)}  Roll sold legs to new weekly (collect more premium)")
    print(f"  {C('C', CYAN)}  Close entire calendar (accept current P&L)")
    print(f"  {C('H', CYAN)}  Hold — no adjustment needed")
    print()

    try:
        ch = input(C("  Action (R/C/H): ", CYAN)).strip().upper()
    except: ch = "H"

    if ch == "R":
        print()
        print(C("  Rolling sold legs to next weekly expiry:", YELLOW+BOLD))
        print(C("  [Section 09] Roll if new sale collects > loss on closure", GRAY))
        try:
            current_call_p = float(input(C(f"  Current CALL {near_call} price (close cost): ", CYAN)).strip())
            current_put_p = float(input(C(f"  Current PUT {near_put} price (close cost): ", CYAN)).strip())
            new_call_p = float(input(C(f"  New CALL {near_call} premium to sell: ", CYAN)).strip())
            new_put_p = float(input(C(f"  New PUT {near_put} premium to sell: ", CYAN)).strip())
        except: print(C("  ✗ Invalid.", RED)); return

        close_loss = (current_call_p + current_put_p) - (s.get("cal_near_call_entry", 0) + s.get("cal_near_put_entry", 0))
        new_credit = new_call_p + new_put_p

        if new_credit > close_loss:
            print(C(f"  ✅  Roll viable: Close loss Rs{close_loss:.0f} < New credit Rs{new_credit:.0f}", GREEN))
            s["adjustments"].append({"type":"Calendar Roll","action":"roll_sold","closed":False})
            log_event(s, f"Calendar roll: closed {near_call}C/{near_put}P @ Rs{current_call_p:.0f}/{current_put_p:.0f}, "
                         f"new sells @ Rs{new_call_p:.0f}/{new_put_p:.0f}")
        else:
            print(C(f"  ⚠  Roll cost Rs{close_loss:.0f} >= new credit Rs{new_credit:.0f} — close instead", YELLOW))
            ch = "C"

    if ch == "C" or (ch == "R" and new_credit <= close_loss):
        print()
        print(C("  [Section 09] Closing entire calendar:", RED+BOLD))
        try:
            close_val = float(input(C(f"  Exit value (current estimate): ", CYAN)).strip())
        except: close_val = pnl
        print(C(f"  ✅  Calendar closed. Final P&L: Rs {close_val:.1f}", GREEN if close_val>=0 else YELLOW))
        s["phase"] = "CLOSED"
        log_event(s, f"Calendar closed at Rs{close_val:.1f}")
        save_state(s)
        return

    save_state(s)
    print(C("  Continue monitoring next 15-min scan.", GRAY))


# ═══════════════════════════════════════════════════════════════════
#  VERTICAL DEBIT SPREAD — SETUP  (Directional, Defined Risk)
#  6 Sections | Defined Max Loss | Directional Conviction Required
# ═══════════════════════════════════════════════════════════════════
def debit_spread_setup(s):
    hdr("VERTICAL DEBIT SPREAD — SETUP  (Directional Play)")

    spot = s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI"))
    vc   = s.get("vix_current", 17)

    if spot:
        print(f"  Live Spot   : {C(B(str(spot)), GREEN)}")
        print(f"  India VIX   : {C(str(vc), YELLOW)}")
    print()

    print(C("  ── What is a Vertical Debit Spread? ───────────", CYAN))
    print(C("  BUY ATM + SELL OTM (same expiry, same type) — defined max loss", YELLOW+BOLD))
    print(C("  Max loss = net debit paid. Max profit = spread width - debit.", YELLOW))
    print(C("  REQUIRES: Strong directional conviction (only enter with conviction)", RED))
    print()

    print(C("  ── Type Selection ─────────────────────────────", CYAN))
    print(f"  {C('[1]', GREEN)}  Bullish Call Debit Spread — market going UP")
    print(f"  {C('[2]', RED)}   Bearish Put Debit Spread  — market going DOWN")
    print()
    
    try:
        bias_choice = int(input(C("  Select bias (1=Bullish / 2=Bearish): ", CYAN)).strip())
    except: bias_choice = 1
    if bias_choice not in [1, 2]: bias_choice = 1

    spread_type = "bullish" if bias_choice==1 else "bearish"
    opt_type = "CE" if bias_choice==1 else "PE"
    print(C(f"  Selected: {'Bullish Call' if bias_choice==1 else 'Bearish Put'} Debit Spread", GREEN))

    expiry_date, dte = select_expiry()
    s["expiry_date"]  = expiry_date
    s["dte_at_entry"] = dte

    print()
    print(C("  ── Strike Selection ───────────────────────────", CYAN))
    
    if spot:
        atm_sugg = int(round(spot/50)*50)
        otm_sugg = atm_sugg + (200 if spread_type == "bullish" else -200)
        print(f"  Suggested: BUY {atm_sugg} {opt_type}  |  SELL {otm_sugg} {opt_type}")
    print()

    try:
        if spread_type == "bullish":
            raw = input(C(f"  BUY Call strike (ATM) [{atm_sugg if spot else ''}]: ", CYAN)).strip()
            buy_strike = int(raw) if raw else atm_sugg
            raw = input(C(f"  SELL Call strike (OTM, above) [{otm_sugg if spot else ''}]: ", CYAN)).strip()
            sell_strike = int(raw) if raw else otm_sugg
        else:
            raw = input(C(f"  BUY Put strike (ATM) [{atm_sugg if spot else ''}]: ", CYAN)).strip()
            buy_strike = int(raw) if raw else atm_sugg
            raw = input(C(f"  SELL Put strike (OTM, below) [{otm_sugg if spot else ''}]: ", CYAN)).strip()
            sell_strike = int(raw) if raw else otm_sugg
    except: print(C("  ✗ Invalid.", RED)); return

    spread_width = abs(buy_strike - sell_strike)
    
    print()
    print(C("  ── Premiums & Risk ────────────────────────────", CYAN))
    try:
        buy_prem = float(input(C(f"  BUY {opt_type} {buy_strike} premium (cost): Rs ", CYAN)).strip())
        sell_prem = float(input(C(f"  SELL {opt_type} {sell_strike} premium (credit): Rs ", CYAN)).strip())
    except: print(C("  ✗ Invalid.", RED)); return

    net_debit = round(buy_prem - sell_prem, 2)
    max_profit = round(spread_width - net_debit, 2)
    max_loss = net_debit

    if spread_type == "bullish":
        be = round(buy_strike + net_debit, 2)
    else:
        be = round(buy_strike - net_debit, 2)

    print()
    print(C("  ── Trade Summary ──────────────────────────────", CYAN))
    print(f"  Net Debit (cost)    : {C(f'Rs {net_debit:.1f}', RED)}")
    print(f"  Spread Width        : {spread_width} pts")
    print(f"  Max Profit          : {C(f'Rs {max_profit:.1f}', GREEN)}")
    print(f"  Max Loss            : {C(f'Rs {max_loss:.1f}', RED)}")
    print(f"  Breakeven           : {C(str(be), YELLOW)}")
    print()

    if input(C("  Confirm debit spread entry (y/n): ", CYAN)).strip().lower() != "y": return

    try: lots = int(input(C("  Number of lots [1]: ", CYAN)).strip() or "1")
    except: lots = 1

    s.update({
        "debit_spread_type": spread_type,
        "debit_opt_type": opt_type,
        "buy_strike": buy_strike,
        "sell_strike": sell_strike,
        "net_debit": net_debit,
        "spread_width": spread_width,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "breakeven": be,
        "upper_be": be if spread_type=="bullish" else be + spread_width,
        "lower_be": be - spread_width if spread_type=="bullish" else be,
        "entry_spot": spot or 0,
        "current_spot": spot or 0,
        "entry_time": datetime.now().isoformat(),
        "phase": "LIVE",
        "adjustments": [],
        "pnl_snapshots": [],
    })
    s["legs"] = {
        "long_leg":  {"strike":buy_strike, "type":opt_type, "action":"buy", 
                      "lots":lots, "entry_premium":buy_prem, "current_premium":buy_prem},
        "short_leg": {"strike":sell_strike, "type":opt_type, "action":"sell",
                      "lots":lots, "entry_premium":sell_prem, "current_premium":sell_prem},
    }

    log_event(s, f"Debit Spread: BUY {buy_strike}{opt_type} @ Rs{buy_prem} / SELL {sell_strike}{opt_type} @ Rs{sell_prem} = Rs{net_debit} debit")
    save_state(s)

    print(); print(C("═"*66, GREEN))
    print(C(f"  ✅  {spread_type.upper()} DEBIT SPREAD ENTERED", GREEN+BOLD))
    print(C("═"*66, GREEN))
    print(C(f"  Max loss: Rs {max_loss:.0f}  |  Max profit: Rs {max_profit:.0f}", CYAN+BOLD))
    print()
    show_position_summary(s)
    draw_payoff_chart(s)
    print(C("  Starting live 15-min monitor… CTRL+C=save  CTRL+E=emergency", CYAN))
    time.sleep(1)
    live_monitor_loop(s)


if __name__=="__main__":
    main()


# ═══════════════════════════════════════════════════════════════════
#  LONG CALLS / LONG PUTS — SETUP  (Directional Conviction Play)
#  5 Sections | Simple Directional Buy | Pure Leverage Play
# ═══════════════════════════════════════════════════════════════════
def long_options_setup(s):
    hdr("LONG OPTIONS — SETUP  (Directional Buy Call / Put)")

    spot = s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI"))
    vc   = s.get("vix_current", 17)

    if spot:
        print(f"  Live Spot   : {C(B(str(spot)), GREEN)}")
        print(f"  India VIX   : {C(str(vc), YELLOW)}")
    print()

    print(C("  ── What are Long Options? ─────────────────────", CYAN))
    print(C("  SIMPLEST directional strategy: BUY a call or put outright", YELLOW+BOLD))
    print(C("  Call: Profit from UP move  |  Put: Profit from DOWN move", CYAN))
    print(C("  Max loss = premium paid ONLY. Defined risk!", GREEN))
    print(C("  Max profit = UNLIMITED (theoretically)", RED))
    print()

    print(C("  ── VIX Check ──────────────────────────────────", CYAN))
    if vc and float(vc) < 20:
        print(C(f"  ✓ VIX {vc} is acceptable", GREEN))
    else:
        print(C(f"  ⚠ VIX {vc} is high — premium expensive", YELLOW))
    print()

    print(C("  ── Type Selection ─────────────────────────────", CYAN))
    print(f"  {C('[1]', GREEN)}  Long Call — bullish view (market going UP)")
    print(f"  {C('[2]', RED)}   Long Put  — bearish view (market going DOWN)")
    print()
    
    try:
        opt_choice = int(input(C("  Select (1=Call / 2=Put): ", CYAN)).strip())
    except: opt_choice = 1
    if opt_choice not in [1, 2]: opt_choice = 1

    opt_type = "CE" if opt_choice==1 else "PE"
    print(C(f"  Selected: Long {opt_type}", GREEN))

    expiry_date, dte = select_expiry()
    s["expiry_date"]  = expiry_date
    s["dte_at_entry"] = dte

    print()
    print(C("  ── Strike Selection ───────────────────────────", CYAN))
    if spot:
        atm_sugg = int(round(spot/50)*50)
        print(f"  ATM: {atm_sugg}  |  Slightly OTM: {atm_sugg + (150 if opt_choice==1 else -150)}")
    
    try:
        raw = input(C(f"  Strike to BUY ({opt_type}) [{atm_sugg if spot else ''}]: ", CYAN)).strip()
        strike = int(raw) if raw else atm_sugg
    except: print(C("  ✗ Invalid.", RED)); return

    print()
    print(C("  ── Premium ────────────────────────────────────", CYAN))
    try:
        prem = float(input(C(f"  BUY {opt_type} {strike} premium (Rs): ", CYAN)).strip())
    except: print(C("  ✗ Invalid.", RED)); return

    max_loss = prem
    
    print()
    print(C("  ── Summary ────────────────────────────────────", CYAN))
    print(f"  Premium paid    : {C(f'Rs {prem:.1f}', RED)}")
    print(f"  Max loss        : {C(f'Rs {prem:.1f}', RED)}")
    print(f"  Max profit      : {C('Unlimited', GREEN)}")
    print(f"  Break-even      : {C(str(strike + prem if opt_choice==1 else strike - prem), YELLOW)}")
    print()

    try: lots = int(input(C("  Number of lots [1]: ", CYAN)).strip() or "1")
    except: lots = 1

    if input(C(f"  Confirm LONG {opt_type} entry (y/n): ", CYAN)).strip().lower() != "y": return

    s.update({
        "long_opt_type": opt_type,
        "long_strike": strike,
        "long_entry_prem": prem,
        "long_max_loss": prem,
        "breakeven": round(strike + prem if opt_choice==1 else strike - prem, 2),
        "upper_be": strike + 1000,
        "lower_be": strike - 1000,
        "entry_spot": spot or 0,
        "current_spot": spot or 0,
        "entry_time": datetime.now().isoformat(),
        "phase": "LIVE",
        "adjustments": [],
        "pnl_snapshots": [],
    })
    s["legs"] = {
        "long_option": {"strike":strike, "type":opt_type, "action":"buy",
                        "lots":lots, "entry_premium":prem, "current_premium":prem},
    }

    log_event(s, f"Long {opt_type}: BUY {strike}{opt_type} @ Rs{prem:.1f}")
    save_state(s)

    print(); print(C("═"*66, GREEN))
    print(C(f"  ✅  LONG {opt_type} ENTERED", GREEN+BOLD))
    print(C("═"*66, GREEN))
    print(C(f"  Max loss: Rs {prem:.0f}  |  Profit potential: UNLIMITED", CYAN+BOLD))
    print()
    show_position_summary(s)
    draw_payoff_chart(s)
    print(C("  Starting live 15-min monitor… CTRL+C=save  CTRL+E=emergency", CYAN))
    time.sleep(1)
    live_monitor_loop(s)


# ═══════════════════════════════════════════════════════════════════
#  DEBIT STRADDLE / STRANGLE — SETUP  (Volatility Buyer)
#  7 Sections | VIX-Bottom Buyer | Gamma Long Position
# ═══════════════════════════════════════════════════════════════════
def debit_straddle_setup(s):
    hdr("DEBIT STRADDLE / STRANGLE — SETUP  (Volatility Bottom Buyer)")

    spot = s.get("current_spot") or fetch_spot(s.get("symbol","^NSEI"))
    vc   = s.get("vix_current", 17)

    if spot:
        print(f"  Live Spot   : {C(B(str(spot)), GREEN)}")
        print(f"  India VIX   : {C(str(vc), YELLOW)}")
    print()

    print(C("  ── Debit Straddle vs Strangle ─────────────────", CYAN))
    print(C("  BUY both a CALL and PUT simultaneously", YELLOW+BOLD))
    print(C("  Straddle: same strike (ATM) — higher cost, profit from any move", GREEN))
    print(C("  Strangle: diff strikes (OTM) — lower cost, need bigger move", YELLOW))
    print(C("  Golden Rule: Only buy when VIX is HISTORICALLY LOW", RED+BOLD))
    print()

    print(C("  ── VIX Entry Condition (CRITICAL) ─────────────", CYAN))
    if vc and float(vc) < 13:
        print(C(f"  ✓ VIX {vc} is VERY LOW — EXCELLENT entry condition!", GREEN+BOLD))
    elif vc and float(vc) < 16:
        print(C(f"  ✓ VIX {vc} is LOW — Good entry condition", GREEN))
    else:
        print(C(f"  ⚠ VIX {vc} is MODERATE/HIGH — NOT ideal", RED))
        if input(C("  Continue anyway (y/n): ", CYAN)).strip().lower() != "y": return
    print()

    print(C("  ── Type Selection ─────────────────────────────", CYAN))
    print(f"  {C('[1]', GREEN)}  Debit Straddle  — ATM call + ATM put")
    print(f"  {C('[2]', YELLOW)}  Debit Strangle  — OTM call + OTM put")
    print()
    
    try:
        type_choice = int(input(C("  Select (1=Straddle / 2=Strangle): ", CYAN)).strip())
    except: type_choice = 1
    if type_choice not in [1, 2]: type_choice = 1

    is_straddle = (type_choice == 1)
    print(C(f"  Selected: {'Debit Straddle' if is_straddle else 'Debit Strangle'}", GREEN))

    expiry_date, dte = select_expiry()
    s["expiry_date"]  = expiry_date
    s["dte_at_entry"] = dte

    print()
    print(C("  ── Strike Selection ───────────────────────────", CYAN))
    if spot:
        atm = int(round(spot/50)*50)
        if is_straddle:
            print(f"  Straddle: BUY {atm} CE + BUY {atm} PE (same strike)")
        else:
            print(f"  Strangle: BUY {atm+200} CE + BUY {atm-200} PE (200pts apart)")
    print()

    if is_straddle:
        try:
            raw = input(C(f"  ATM strike [{atm if spot else ''}]: ", CYAN)).strip()
            call_strike = put_strike = int(raw) if raw else atm
        except: print(C("  ✗ Invalid.", RED)); return
    else:
        try:
            raw = input(C(f"  CALL strike (OTM) [{atm+200 if spot else ''}]: ", CYAN)).strip()
            call_strike = int(raw) if raw else atm+200
            raw = input(C(f"  PUT strike (OTM) [{atm-200 if spot else ''}]: ", CYAN)).strip()
            put_strike = int(raw) if raw else atm-200
        except: print(C("  ✗ Invalid.", RED)); return

    print()
    print(C("  ── Premiums ────────────────────────────────────", CYAN))
    try:
        call_prem = float(input(C(f"  BUY CALL {call_strike} premium (Rs): ", CYAN)).strip())
        put_prem  = float(input(C(f"  BUY PUT {put_strike} premium (Rs): ", CYAN)).strip())
    except: print(C("  ✗ Invalid.", RED)); return

    total_cost = call_prem + put_prem
    breakeven_up = call_strike + total_cost
    breakeven_down = put_strike - total_cost

    print()
    print(C("  ── Summary ────────────────────────────────────", CYAN))
    print(f"  Total cost (debit) : {C(f'Rs {total_cost:.1f}', RED)}")
    print(f"  Max loss           : {C(f'Rs {total_cost:.1f}', RED)}")
    print(f"  Max profit         : {C('Unlimited', GREEN)}")
    print(f"  Upper BE           : {C(str(breakeven_up), YELLOW)}")
    print(f"  Lower BE           : {C(str(breakeven_down), YELLOW)}")
    print()

    try: lots = int(input(C("  Number of lots [1]: ", CYAN)).strip() or "1")
    except: lots = 1

    if input(C(f"  Confirm DEBIT {'STRADDLE' if is_straddle else 'STRANGLE'} entry (y/n): ", CYAN)).strip().lower() != "y": return

    s.update({
        "debit_long_type": "straddle" if is_straddle else "strangle",
        "debit_call_strike": call_strike,
        "debit_put_strike": put_strike,
        "debit_total_cost": total_cost,
        "debit_max_loss": total_cost,
        "breakeven_up": breakeven_up,
        "breakeven_down": breakeven_down,
        "upper_be": breakeven_up,
        "lower_be": breakeven_down,
        "entry_spot": spot or 0,
        "current_spot": spot or 0,
        "entry_time": datetime.now().isoformat(),
        "phase": "LIVE",
        "adjustments": [],
        "pnl_snapshots": [],
    })
    s["legs"] = {
        "long_call": {"strike":call_strike, "type":"CE", "action":"buy",
                      "lots":lots, "entry_premium":call_prem, "current_premium":call_prem},
        "long_put":  {"strike":put_strike, "type":"PE", "action":"buy",
                      "lots":lots, "entry_premium":put_prem, "current_premium":put_prem},
    }

    log_event(s, f"Debit {'Straddle' if is_straddle else 'Strangle'}: BUY {call_strike}CE @ Rs{call_prem} + BUY {put_strike}PE @ Rs{put_prem} = Rs{total_cost:.1f}")
    save_state(s)

    print(); print(C("═"*66, GREEN))
    print(C(f"  ✅  DEBIT {'STRADDLE' if is_straddle else 'STRANGLE'} ENTERED", GREEN+BOLD))
    print(C("═"*66, GREEN))
    print(C(f"  Betting on: VOLATILITY EXPANSION", RED+BOLD))
    print(C(f"  Max loss: Rs {total_cost:.0f}  |  Profit: unlimited", CYAN+BOLD))
    print()
    show_position_summary(s)
    draw_payoff_chart(s)
    print(C("  Starting live 15-min monitor… CTRL+C=save  CTRL+E=emergency", CYAN))
    time.sleep(1)
    live_monitor_loop(s)

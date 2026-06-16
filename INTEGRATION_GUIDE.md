# OPTIONS MANAGER v5.5 — MODULE INTEGRATION GUIDE

## Overview

This guide shows how to integrate the four enhanced modules with `options_manager.py`:
- **alert_system_v2.py** — Real-time alerts (breach, profit, stop loss, adjustments)
- **backtesting_engine_v2.py** — Strategy performance simulation & replay
- **broker_api_handler_v2.py** — Live data from Zerodha/Angel/Upstox + Yahoo Finance fallback
- **performance_analytics_v2.py** — Trade logging, metrics, risk-adjusted reporting

All modules are designed for **seamless integration** with options_manager's state dict and phase flow.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    OPTIONS MANAGER v5.5                     │
│  (Core: strategy selection, setup, live monitoring, state)  │
└────┬────────────────────────────────────────────────────────┘
     │
     ├─► BROKER API HANDLER v2 ────────────► Real-time spot/option data
     │   (fetch_spot_price, fetch_option_chain, fetch_option_price)
     │
     ├─► ALERT SYSTEM v2 ──────────────────► Email/SMS notifications
     │   (breach alerts, profit targets, stop losses, adjustments)
     │
     ├─► PERFORMANCE ANALYTICS v2 ────────► Trade logging & metrics
     │   (add_trade_from_options_manager_state, calculate metrics)
     │
     └─► BACKTESTING ENGINE v2 ───────────► Strategy performance testing
         (add_trade_from_options_manager_state, replay trades)
```

---

## 1. ALERT SYSTEM INTEGRATION

### Setup

```python
from alert_system_v2 import AlertManager

# Email configuration
email_config = {
    'sender_email': 'your_email@gmail.com',
    'sender_password': 'your_app_password',  # Gmail app password, not main password
    'smtp_server': 'smtp.gmail.com',
    'smtp_port': 587,
    'recipient_emails': ['alert@example.com'],
}

# SMS configuration (optional, requires Twilio account)
sms_config = {
    'account_sid': 'your_twilio_sid',
    'auth_token': 'your_twilio_token',
    'from_number': '+1234567890',
    'to_numbers': ['+919191XXXXXX'],
}

# Initialize
alerts = AlertManager(email_config=email_config, sms_config=sms_config)
```

### Usage in options_manager

During **LIVE phase** (monitoring active positions):

```python
# Breach alert
alerts.send_breach_alert(
    side='upper',
    distance=50,
    strike=24500,
    severity='WARNING',
    context={
        'strategy': state['strategy'],
        'current_spot': current_spot,
        'entry_spot': state['entry_spot'],
        'upper_be': state['upper_be'],
        'lower_be': state['lower_be'],
        'net_credit': state['net_credit'],
    }
)

# Profit target reached
alerts.send_profit_target_alert(
    strategy=state['strategy'],
    profit_pct=50.0,
    context={'strategy': state['strategy'], 'current_spot': current_spot}
)

# Stop loss hit
alerts.send_stop_loss_alert(
    strategy=state['strategy'],
    loss_amount=5000,
    context={'strategy': state['strategy']}
)

# Position opened
alerts.send_position_opened_alert(
    strategy=state['strategy'],
    context=state
)

# Position closed
alerts.send_position_closed_alert(
    strategy=state['strategy'],
    pnl=state['current_pnl'],
    context=state
)

# Custom alert
alerts.send_alert(
    alert_type='ADJUSTMENT',
    subject=f"Rolling {leg_name}",
    message=f"Exit at {exit_price}, re-enter at new strike",
    severity='INFO',
    context=state
)
```

### Rate Limiting

Alerts are rate-limited to prevent spam:
- **Same alert type**: 5 minutes cooldown (EMAIL)
- **SMS alerts**: 15 minutes cooldown (only WARNING/CRITICAL)

```python
# Check alert history
summary = alerts.get_alert_summary()
print(f"Total alerts sent: {summary['total_alerts']}")
print(f"Recent alerts: {summary['recent_alerts'][-5:]}")
```

---

## 2. BROKER API HANDLER INTEGRATION

### Setup

```python
from broker_api_handler_v2 import BrokerManager, BrokerFactory

# Using BrokerManager (recommended for options_manager integration)
broker = BrokerManager(
    broker_name='zerodha',  # or 'angel', 'upstox', 'manual'
    config={
        'api_key': 'your_zerodha_api_key',
        'access_token': 'your_access_token',
        'symbol': '^NSEI',
    }
)

# Or use factory for auto-fallback to Yahoo Finance
api = BrokerFactory.create('zerodha', config)
```

### Fetch Real-Time Data

```python
# Get spot price
spot = broker.get_spot_price()
print(f"Current NIFTY: {spot}")

# Get option price with Greeks
option_data = broker.get_option_price(
    symbol='NIFTY',
    expiry='2026-06-18',
    strike=24500,
    opt_type='CE'  # Call; use 'PE' for Put
)

# Returns:
# {
#     'strike': 24500,
#     'type': 'CE',
#     'bid': 150.5,
#     'ask': 151.5,
#     'last_price': 151.0,
#     'iv': 17.5,
#     'delta': 0.65,
#     'gamma': 0.001,
#     'theta': -0.05,
#     'vega': 0.08,
#     'timestamp': '2026-06-10 10:30:00',
# }

# Get option chain (all strikes for expiry)
chain = broker.get_option_chain(expiry='2026-06-18')
# Returns: {'calls': [...], 'puts': [...]}

# Check broker status
status = broker.get_status()
print(f"Broker: {status['broker']}, Connected: {status['connected']}")
```

### Fallback Logic

If primary broker fails, automatically falls back to Yahoo Finance:

```
Zerodha → Failed? → Fallback to Yahoo Finance ✓
Angel   → Failed? → Fallback to Yahoo Finance ✓
Upstox  → Failed? → Fallback to Yahoo Finance ✓
Manual  → Uses Yahoo Finance directly
```

### Integration with options_manager

In the **ANALYZE → SETUP** phase:

```python
# Fetch current spot
spot = broker.get_spot_price()

# Calculate strategy strikes based on spot
atm_strike = round(spot / 100) * 100

# For Iron Fly, fetch nearby option prices
ce_atm = broker.get_option_price('NIFTY', expiry, atm_strike, 'CE')
pe_atm = broker.get_option_price('NIFTY', expiry, atm_strike, 'PE')

ce_otm = broker.get_option_price('NIFTY', expiry, atm_strike+200, 'CE')
pe_otm = broker.get_option_price('NIFTY', expiry, atm_strike-200, 'PE')

# Use data to populate entry prices in state
state['leg_CE_ATM']['bid'] = ce_atm['bid']
state['leg_CE_OTM']['ask'] = ce_otm['ask']
```

---

## 3. PERFORMANCE ANALYTICS INTEGRATION

### Setup

```python
from performance_analytics_v2 import TradeLog, PerformanceMetrics, PerformanceReport

# Initialize trade log
log = TradeLog(log_file='trade_log.json')

# Initialize metrics calculator
metrics = PerformanceMetrics(log)

# Initialize report generator
report = PerformanceReport(metrics)
```

### Add Closed Positions

When position enters **CLOSED phase**:

```python
# Method 1: Direct from options_manager state (recommended)
log.add_trade_from_options_manager_state(state)

# Method 2: Manual trade entry
log.add_trade({
    'id': 'IRONFLY_20260610_001',
    'strategy': 'Iron Fly',
    'entry_date': '2026-06-10',
    'entry_time': '10:30:00',
    'entry_price': 250,         # Net credit
    'exit_date': '2026-06-15',
    'exit_time': '14:00:00',
    'exit_price': 125,          # Exit price
    'position_size': 1,
    'pnl': 9375,               # (250-125)*75
    'pnl_pct': 37.5,
    'days_held': 5,
    'status': 'CLOSED',
})
```

### Calculate Performance Metrics

```python
# Overall metrics
all_metrics = metrics.calculate_all()

print(f"Win Rate: {all_metrics['win_rate']:.1f}%")
print(f"Total P&L: Rs {all_metrics['total_pnl']:,.0f}")
print(f"Sharpe Ratio: {all_metrics['sharpe_ratio']:.2f}")
print(f"Max Drawdown: Rs {all_metrics['max_drawdown']:,.0f}")

# By-strategy metrics
by_strategy = metrics.get_by_strategy()
for strategy, m in by_strategy.items():
    print(f"\n{strategy}")
    print(f"  Trades: {m['total_trades']}")
    print(f"  Win Rate: {m['win_rate']:.1f}%")
    print(f"  P&L: Rs {m['total_pnl']:,.0f}")

# Filter by strategy
iron_fly_metrics = metrics.calculate_all(strategy='Iron Fly')
```

### Generate Reports

```python
# Print summary to console
print(report.generate_summary())

# Generate detailed report to file
report.generate_detailed_report('performance_report.txt')

# Get latest trades
latest = log.get_latest_trades(n=10)
for trade in latest:
    print(f"{trade['strategy']} | {trade['pnl']:+.0f} Rs")
```

### Available Metrics

| Metric | Description |
|--------|-------------|
| **win_rate** | % of profitable trades |
| **profit_factor** | Gross profit / Abs(gross loss) |
| **payoff_ratio** | Avg win / Abs(avg loss) |
| **sharpe_ratio** | Risk-adjusted return (annualized) |
| **sortino_ratio** | Downside-adjusted return |
| **calmar_ratio** | Annual return / Max drawdown |
| **max_drawdown** | Largest peak-to-trough decline |
| **recovery_factor** | Total profit / Abs(max drawdown) |
| **roi** | Return on initial capital |

---

## 4. BACKTESTING ENGINE INTEGRATION

### Setup

```python
from backtesting_engine_v2 import BacktestEngine, BacktestScenario

# Create engine
engine = BacktestEngine()
```

### Create Backtest Scenarios

```python
# Single scenario for Iron Fly
scenario = BacktestScenario(
    strategy='Iron Fly',
    symbol='^NSEI',
    start_date='2026-04-01',
    end_date='2026-06-30',
    initial_capital=100000,
    lot_size=75
)

# Add trades (manual entry)
scenario.add_trade(
    entry_date='2026-04-01',
    entry_price=250,      # Net debit/credit in points
    exit_date='2026-04-07',
    exit_price=125,       # Exit price
    position_size=1,
    trade_type='credit',
    strategy_name='Iron Fly'
)

# Or add from options_manager state
scenario.add_trade_from_options_manager_state(closed_state)

engine.add_scenario(scenario)
```

### Run Backtest

```python
# Run all scenarios
results = engine.run_all(load_price_data=False)  # False for manual trades

# Get summary for one scenario
summary_text = scenario.get_summary()
print(summary_text)

# Get metrics dict
metrics = scenario.calculate_metrics()
print(f"Total P&L: Rs {metrics['total_pnl']:,.0f}")
print(f"Win Rate: {metrics['win_rate']:.1f}%")
print(f"Sharpe: {metrics['sharpe_ratio']:.2f}")
```

### Compare Strategies

```python
# Add multiple strategies
scenario_if = BacktestScenario('Iron Fly', '^NSEI', '2026-01-01', '2026-06-30')
scenario_ic = BacktestScenario('Iron Condor', '^NSEI', '2026-01-01', '2026-06-30')

# Add trades to each...
engine.add_scenario(scenario_if)
engine.add_scenario(scenario_ic)

# Run and compare
engine.run_all(load_price_data=False)
comparison = engine.compare_strategies()

print(f"Strategies: {comparison['strategies']}")
print(f"P&L: {comparison['total_pnl']}")
print(f"Win Rates: {comparison['win_rates']}")
print(f"Sharpe: {comparison['sharpe_ratios']}")

# Generate report
engine.generate_report('backtest_report.txt')
```

---

## 5. COMPLETE INTEGRATION EXAMPLE

### Full Workflow: Setup → Monitor → Close → Analyze

```python
from options_manager import OptionsManager
from alert_system_v2 import AlertManager
from broker_api_handler_v2 import BrokerManager
from performance_analytics_v2 import TradeLog, PerformanceMetrics, PerformanceReport
from backtesting_engine_v2 import BacktestEngine, BacktestScenario

# ═══════════════════════════════════════════════════════════════════
# 1. SETUP
# ═══════════════════════════════════════════════════════════════════

# Initialize all modules
alerts = AlertManager(email_config=email_cfg, sms_config=sms_cfg)
broker = BrokerManager(broker_name='zerodha', config=broker_cfg)
log = TradeLog('trade_log.json')

# Get current spot
spot = broker.get_spot_price()
print(f"Current Spot: {spot}")

# ═══════════════════════════════════════════════════════════════════
# 2. SETUP STRATEGY (in options_manager)
# ═══════════════════════════════════════════════════════════════════

om = OptionsManager()

# Select strategy based on IV/HV ratio
state = om.strategy_selection(
    current_spot=spot,
    iv_hv_ratio=1.2,
    capital=100000,
    manual_strategy=None
)

# Setup Iron Fly
state = om.setup_iron_fly(
    strike=round(spot/100)*100,
    expiry='2026-06-18',
    entry_type='market',
    state=state
)

# Send alert: position opened
alerts.send_position_opened_alert(
    strategy=state['strategy'],
    context=state
)

# ═══════════════════════════════════════════════════════════════════
# 3. LIVE MONITORING (periodic updates)
# ═══════════════════════════════════════════════════════════════════

while state['phase'] == 'LIVE':
    # Update spot price
    current_spot = broker.get_spot_price()
    
    # Update position with Greeks, P&L
    state = om.monitor_live_position(current_spot, state)
    
    # Check for breaches
    if current_spot > state['upper_be'] - 100:
        alerts.send_breach_alert(
            side='upper',
            distance=state['upper_be'] - current_spot,
            strike=state['atm_strike'],
            severity='WARNING',
            context={
                'strategy': state['strategy'],
                'current_spot': current_spot,
                'upper_be': state['upper_be'],
            }
        )
    
    # Check for profit target
    if state['current_pnl'] >= state['net_credit'] * 0.5:  # 50% profit
        alerts.send_profit_target_alert(
            strategy=state['strategy'],
            profit_pct=50.0,
            context=state
        )
        # Close position
        state['phase'] = 'CLOSED'
    
    # Check for stop loss
    if state['current_pnl'] <= -state['net_credit'] * 0.2:  # 20% loss
        alerts.send_stop_loss_alert(
            strategy=state['strategy'],
            loss_amount=abs(state['current_pnl']),
            context=state
        )
        # Close position
        state['phase'] = 'CLOSED'
    
    time.sleep(60)  # Check every minute

# ═══════════════════════════════════════════════════════════════════
# 4. POSITION CLOSED — LOG & REPORT
# ═══════════════════════════════════════════════════════════════════

# Add closed position to trade log
log.add_trade_from_options_manager_state(state)

# Send closing alert
alerts.send_position_closed_alert(
    strategy=state['strategy'],
    pnl=state['current_pnl'],
    context=state
)

# ═══════════════════════════════════════════════════════════════════
# 5. PERFORMANCE ANALYSIS
# ═══════════════════════════════════════════════════════════════════

metrics = PerformanceMetrics(log)
report = PerformanceReport(metrics)

# Print summary
print(report.generate_summary())

# Generate detailed report
report.generate_detailed_report('performance_report.txt')

# ═══════════════════════════════════════════════════════════════════
# 6. BACKTEST HISTORICAL PERFORMANCE
# ═══════════════════════════════════════════════════════════════════

engine = BacktestEngine()

# Create backtest from log
scenario = BacktestScenario(
    strategy='Iron Fly',
    symbol='^NSEI',
    start_date='2026-04-01',
    end_date='2026-06-30',
    initial_capital=100000
)

# Populate with trades from log
for trade in log.get_trades(strategy='Iron Fly', status='CLOSED'):
    scenario.add_trade(
        entry_date=trade['entry_date'],
        entry_price=trade['entry_price'],
        exit_date=trade['exit_date'],
        exit_price=trade['exit_price'],
        trade_type='credit' if trade['entry_price'] > 0 else 'debit'
    )

engine.add_scenario(scenario)
engine.run_all(load_price_data=False)
engine.generate_report('backtest_report.txt')
```

---

## 6. TROUBLESHOOTING

### Alert Not Sending

**Issue**: Emails not received
- **Solution**: Check Gmail allows "Less Secure Apps" or use app password
- **Solution**: Check SMTP credentials are correct
- **Solution**: Check recipient email is valid

**Issue**: SMS not sending
- **Solution**: Install Twilio: `pip install twilio`
- **Solution**: Verify Twilio credentials (SID, token, phone numbers)

### Broker Connection Failed

**Issue**: "Cannot connect to Zerodha"
- **Solution**: Check API key and access token are valid
- **Solution**: Verify kiteconnect is installed: `pip install kiteconnect`
- **Solution**: Falls back to Yahoo Finance automatically

**Issue**: Option data not fetching
- **Solution**: Broker option chain requires custom implementation per broker
- **Solution**: Fallback: Use broker's web interface or manual entry
- **Solution**: Yahoo Finance fallback provides spot prices only

### Metrics Not Calculating

**Issue**: Empty metrics
- **Solution**: Ensure trades are in 'CLOSED' status
- **Solution**: Check trade log file (trade_log.json) exists
- **Solution**: Verify trades have non-zero 'pnl' values

---

## 7. CONFIGURATION EXAMPLES

### Email (Gmail)

```python
email_config = {
    'sender_email': 'your_email@gmail.com',
    'sender_password': 'generated_app_password',  # NOT your Gmail password!
    'smtp_server': 'smtp.gmail.com',
    'smtp_port': 587,
    'recipient_emails': ['your_phone@notify.example.com'],  # MMS gateway
}
```

**Generate Gmail App Password**:
1. Go to myaccount.google.com → Security
2. Enable 2FA if not already enabled
3. Go to App passwords → Select "Mail" → Select "Windows Computer"
4. Copy the generated password (16 chars)

### SMS (Twilio)

```python
sms_config = {
    'account_sid': 'ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
    'auth_token': 'your_auth_token',
    'from_number': '+1234567890',  # Twilio trial number
    'to_numbers': ['+919191XXXXXX'],  # India numbers: +91
}
```

### Zerodha

```python
zerodha_config = {
    'broker': 'zerodha',
    'api_key': 'your_api_key',
    'access_token': 'your_access_token',
    'symbol': '^NSEI',
}
```

---

## 8. FILE STRUCTURE

After integration, your project structure should be:

```
options_trading/
├── options_manager.py                 (v5.5 — core)
├── alert_system_v2.py                 (NEW — alerts)
├── backtesting_engine_v2.py           (NEW — backtesting)
├── broker_api_handler_v2.py           (NEW — live data)
├── performance_analytics_v2.py        (NEW — metrics)
├── main.py                            (Your integration script)
├── config.json                        (Credentials & settings)
├── trade_log.json                     (Auto-created — trade history)
├── performance_report.txt             (Auto-created — metrics report)
└── backtest_report.txt                (Auto-created — backtest results)
```

---

## 9. REQUIREMENTS

```
# Core
numpy>=1.24.0
yfinance>=0.2.30

# Broker APIs (optional, install as needed)
kiteconnect>=4.2.0          # Zerodha
smartapi-python>=1.3.0      # Angel One
upstox-client>=2.0.0        # Upstox

# Alerts (optional)
twilio>=8.0.0               # SMS alerts
```

Install all:
```bash
pip install numpy yfinance twilio kiteconnect
```

---

## 10. NEXT STEPS

1. **Configure credentials** in email_config, sms_config, broker_config
2. **Test broker connection**: `broker.get_spot_price()`
3. **Test alerts**: `alerts.send_alert(...)` with test message
4. **Run first strategy setup** with options_manager
5. **Monitor live position** with alerts and broker data
6. **Log closed position** and review metrics
7. **Backtest strategy** on historical trades
8. **Iterate** — refine thresholds based on performance

---

**Last Updated**: June 2026  
**Version**: v5.5.2  
**Compatibility**: options_manager.py v5.3+

# IV-HV Options Strategy Framework

A production-ready options trading framework built around **Implied Volatility (IV)** and **Historical Volatility (HV)** analysis, integrated with position management, live monitoring, alerts, analytics, and backtesting.

---

## Overview

This project provides a complete workflow for options traders:

- Strategy Selection
- Position Setup
- Live Monitoring
- Risk Management
- Automated Alerts
- Performance Analytics
- Historical Backtesting

The framework is designed to work with multiple option strategies while maintaining a unified state-management architecture.

---

## Architecture

```text
┌───────────────────────────────┐
│      options_manager.py       │
│   Core Strategy Engine        │
└───────────────┬───────────────┘
                │
     ┌──────────┼──────────┐
     │          │          │
     ▼          ▼          ▼

 Broker API   Alerts   Analytics
 Handler       v2         v2

                │
                ▼

          Backtesting
             Engine
```

---

## Features

### Strategy Management

- Centralized position lifecycle management
- Strategy state tracking
- Break-even calculations
- Risk monitoring
- Position adjustment workflow

### Broker Integration

Supports:

- Zerodha Kite
- Angel One (ready for implementation)
- Upstox (ready for implementation)
- Yahoo Finance fallback

Features:

- Live Spot Price
- Option Chain Data
- Greeks Calculation
- Multi-Broker Support
- Automatic Fallback Handling

---

### Alert System

Receive notifications for:

- Upper/Lower Breach
- Profit Target Hit
- Stop Loss Triggered
- Position Opened
- Position Closed
- Strategy Adjustments

Channels:

- Email (SMTP)
- SMS (Twilio)

Additional Features:

- Alert History
- Rate Limiting
- Severity Levels
- Context-Aware Messages

---

### Performance Analytics

Track and analyze strategy performance with:

- Win Rate
- Profit Factor
- Payoff Ratio
- Sharpe Ratio
- Sortino Ratio
- Calmar Ratio
- Recovery Factor
- Max Drawdown
- Consecutive Wins/Losses

Trade logs are stored in JSON format for auditability.

---

### Backtesting Engine

Evaluate strategies using historical trades.

Features:

- Trade Replay
- Multi-Scenario Testing
- Historical Market Data
- Risk Metrics
- Strategy Comparison
- Performance Reports

---

## Project Structure

```text
IV_HV Guide/
│
├── options_manager.py
│
├── alert_system_v2.py
├── broker_api_handler_v2.py
├── performance_analytics_v2.py
├── backtesting_engine_v2.py
│
├── complete_integration_example.py
│
├── README.txt
├── QUICK_REFERENCE.txt
├── INTEGRATION_GUIDE.md
├── DELIVERY_SUMMARY.txt
├── MODIFICATION_SUMMARY.txt
│
└── Rule_Book/
    ├── Debit_Spread_Rule_Book.pdf
    ├── Credit_Spread_Rulebook.pdf
    ├── Iron_Condor_Rulebook.docx
    ├── Calendar_Spread_RuleBook.pdf
    ├── Butterfly_Strategy_RuleBook.pdf
    ├── BWB_Advanced_Rulebook.pdf
    └── Strangle_Adjustment_RuleBook.pdf
```

---

## Modules

### 1. Alert System

**File:** `alert_system_v2.py`

Classes:

- EmailAlertHandler
- SMSAlertHandler
- AlertManager

Capabilities:

- Email notifications
- SMS notifications
- Rate limiting
- Alert logging

---

### 2. Broker API Handler

**File:** `broker_api_handler_v2.py`

Classes:

- BrokerAPI
- YahooFinanceAPI
- ZerodhaKiteAPI
- BrokerFactory
- BrokerManager

Capabilities:

- Market Data Retrieval
- Option Chain Fetching
- Greeks Calculation
- Broker Failover

---

### 3. Performance Analytics

**File:** `performance_analytics_v2.py`

Classes:

- TradeLog
- PerformanceMetrics
- PerformanceReport

Capabilities:

- Trade Tracking
- Performance Evaluation
- Reporting Dashboard

---

### 4. Backtesting Engine

**File:** `backtesting_engine_v2.py`

Classes:

- BacktestScenario
- BacktestEngine

Capabilities:

- Historical Analysis
- Strategy Comparison
- Risk Assessment

---

## Installation

Install required packages:

```bash
pip install numpy
pip install yfinance
pip install twilio
pip install kiteconnect
```

Or:

```bash
pip install numpy yfinance twilio kiteconnect
```

---

## Quick Start

### Clone Repository

```bash
git clone <your-repository-url>
cd <repository-name>
```

### Run Example

```bash
python complete_integration_example.py
```

### Integrate with Your Workflow

```python
from broker_api_handler_v2 import BrokerManager
from alert_system_v2 import AlertManager
from performance_analytics_v2 import TradeLog
from backtesting_engine_v2 import BacktestEngine
```

---

## Trading Workflow

### ANALYZE

- Market Assessment
- IV-HV Evaluation
- Strategy Selection

### SETUP

- Select Strikes
- Fetch Option Prices
- Open Position

### LIVE

- Monitor Position
- Track Break-Evens
- Send Alerts
- Manage Adjustments

### CLOSED

- Exit Position
- Log Trade
- Generate Reports

### ANALYSIS

- Review Metrics
- Compare Performance
- Backtest Improvements

---

## Risk Metrics Included

| Metric | Description |
|----------|-------------|
| Sharpe Ratio | Risk-adjusted return |
| Sortino Ratio | Downside risk-adjusted return |
| Calmar Ratio | Return vs Drawdown |
| Profit Factor | Gross Profit / Gross Loss |
| Recovery Factor | Net Profit / Drawdown |
| Win Rate | Winning Trades % |
| Max Drawdown | Largest Equity Drop |
| Payoff Ratio | Avg Win / Avg Loss |

---

## Strategy Rulebooks Included

The repository contains detailed rulebooks for:

- Debit Spread
- Credit Spread
- Iron Condor
- Calendar Spread
- Butterfly
- Broken Wing Butterfly (BWB)
- Strangle Adjustments

These documents serve as the decision-making layer on top of the execution framework.

---

## Example Use Cases

- IV Rank Based Trading
- Volatility Expansion Strategies
- Volatility Contraction Strategies
- Iron Condor Management
- Credit Spread Automation
- Portfolio Performance Tracking
- Strategy Backtesting

---

## Future Enhancements

- Live Broker Order Placement
- Dashboard UI
- Telegram Alerts
- Discord Integration
- Portfolio-Level Analytics
- Machine Learning Strategy Evaluation
- Cloud Deployment

---

## Disclaimer

This project is intended for educational and research purposes only.

Options trading involves substantial risk and may not be suitable for all investors. Past performance does not guarantee future results.

Always test strategies thoroughly before deploying real capital.

---

## Author

Built for systematic options trading, volatility analysis, and quantitative strategy evaluation.

If you found this project useful, consider giving the repository a ⭐.

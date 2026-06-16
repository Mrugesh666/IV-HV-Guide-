#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   OPTIONS MANAGER v5.5 — COMPLETE INTEGRATION EXAMPLE            ║
║   Alert System + Broker API + Analytics + Backtesting            ║
║                                                                  ║
║   This script demonstrates the full workflow:                   ║
║   Setup → Monitor → Close → Analyze → Backtest                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import time
import json
from datetime import datetime
from typing import Dict, Optional

# Import all modules
from alert_system_v2 import AlertManager
from broker_api_handler_v2 import BrokerManager
from performance_analytics_v2 import TradeLog, PerformanceMetrics, PerformanceReport
from backtesting_engine_v2 import BacktestEngine, BacktestScenario


# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# Email alerts configuration
EMAIL_CONFIG = {
    'sender_email': 'your_email@gmail.com',
    'sender_password': 'your_app_password',  # Gmail app password (not main password!)
    'smtp_server': 'smtp.gmail.com',
    'smtp_port': 587,
    'recipient_emails': ['alert@example.com'],
}

# SMS alerts configuration (optional, requires Twilio account)
SMS_CONFIG = {
    'account_sid': 'your_twilio_sid',
    'auth_token': 'your_twilio_token',
    'from_number': '+1234567890',  # Twilio trial number
    'to_numbers': ['+919191XXXXXX'],  # Your phone (India: +91...)
}

# Broker configuration
BROKER_CONFIG = {
    'broker': 'zerodha',  # or 'angel', 'upstox', 'manual'
    'symbol': '^NSEI',
    'api_key': 'your_zerodha_api_key',
    'access_token': 'your_zerodha_access_token',
}

# Trading parameters
INITIAL_CAPITAL = 100000  # Rs 1 lakh
NIFTY_LOT_SIZE = 75
MAX_LOSS_PCT = 20  # Exit if loss > 20% of credit
PROFIT_TARGET_PCT = 50  # Target 50% of credit as profit


# ═══════════════════════════════════════════════════════════════════
#  POSITION MANAGER — Integration layer between options_manager & modules
# ═══════════════════════════════════════════════════════════════════

class PositionManager:
    """
    Manages a single live options position.
    Integrates:
    - Real-time data from BrokerManager
    - Alerts from AlertManager
    - Logging to TradeLog
    """
    
    def __init__(self, state: Dict, alerts: AlertManager, 
                 broker: BrokerManager, log: TradeLog):
        """
        Initialize position manager.
        
        Args:
            state: Position state from options_manager (e.g., Iron Fly state)
            alerts: AlertManager instance
            broker: BrokerManager instance
            log: TradeLog instance
        """
        self.state = state
        self.alerts = alerts
        self.broker = broker
        self.log = log
        
        self.entry_spot = None
        self.max_profit = None
        self.max_loss = None
        self.closed = False
        self.close_reason = None
        
        self._calculate_limits()
    
    def _calculate_limits(self) -> None:
        """Calculate profit/loss limits from entry."""
        net_credit = self.state.get('net_credit', 0)
        
        # For credit spreads: max profit = net credit
        self.max_profit = net_credit * NIFTY_LOT_SIZE
        
        # Max loss = initial capital * max loss %
        self.max_loss = INITIAL_CAPITAL * (MAX_LOSS_PCT / 100)
    
    def update(self, current_spot: float) -> bool:
        """
        Update position with current spot price.
        Check for breach, profit target, stop loss.
        
        Args:
            current_spot: Current spot price
        
        Returns:
            True if position still active, False if should close
        """
        if self.closed:
            return False
        
        # Update state with current spot
        self.state['current_spot'] = current_spot
        
        # Calculate updated P&L and Greeks (from options_manager)
        # This would normally be done by options_manager.monitor_live_position()
        self._update_position_greeks()
        
        # Check for breach
        if self._check_breach(current_spot):
            self.alerts.send_breach_alert(
                side='upper' if current_spot > self.state['upper_be'] - 100 else 'lower',
                distance=abs(current_spot - self.state['upper_be']),
                strike=self.state.get('atm_strike', 24500),
                severity='WARNING',
                context=self._get_alert_context()
            )
        
        # Check for profit target
        if self._check_profit_target():
            self.alerts.send_profit_target_alert(
                strategy=self.state['strategy'],
                profit_pct=PROFIT_TARGET_PCT,
                context=self._get_alert_context()
            )
            self.close_position(reason='PROFIT_TARGET')
            return False
        
        # Check for stop loss
        if self._check_stop_loss():
            self.alerts.send_stop_loss_alert(
                strategy=self.state['strategy'],
                loss_amount=abs(self.state['current_pnl']),
                context=self._get_alert_context()
            )
            self.close_position(reason='STOP_LOSS')
            return False
        
        return True
    
    def _check_breach(self, current_spot: float) -> bool:
        """Check if price is approaching breakeven."""
        upper_distance = self.state.get('upper_be', float('inf')) - current_spot
        lower_distance = current_spot - self.state.get('lower_be', float('-inf'))
        
        return upper_distance < 100 or lower_distance < 100
    
    def _check_profit_target(self) -> bool:
        """Check if profit target is reached."""
        pnl_pct = self.state.get('current_pnl_pct', 0)
        return pnl_pct >= PROFIT_TARGET_PCT
    
    def _check_stop_loss(self) -> bool:
        """Check if stop loss is hit."""
        pnl = self.state.get('current_pnl', 0)
        return pnl <= -self.max_loss
    
    def _update_position_greeks(self) -> None:
        """
        Update position Greeks and P&L from current spot.
        In real implementation, this calls options_manager.monitor_live_position()
        """
        # Simplified mock update
        current_spot = self.state.get('current_spot')
        entry_spot = self.state.get('entry_spot', current_spot)
        
        # Mock P&L calculation (real version uses options_manager)
        spot_move = current_spot - entry_spot
        # For credit spreads, positive spot move = lower P&L
        estimated_pnl = self.state.get('net_credit', 0) * NIFTY_LOT_SIZE - (spot_move * NIFTY_LOT_SIZE)
        
        self.state['current_pnl'] = estimated_pnl
        self.state['current_pnl_pct'] = (estimated_pnl / (self.state.get('net_credit', 1) * NIFTY_LOT_SIZE)) * 100
    
    def close_position(self, reason: str = 'MANUAL') -> None:
        """Close the position."""
        self.closed = True
        self.close_reason = reason
        self.state['phase'] = 'CLOSED'
        self.state['close_date'] = datetime.now().strftime('%Y-%m-%d')
        self.state['close_time'] = datetime.now().strftime('%H:%M:%S')
        
        # Log to trade log
        self.log.add_trade_from_options_manager_state(self.state)
    
    def _get_alert_context(self) -> Dict:
        """Get context dict for alerts."""
        return {
            'strategy': self.state.get('strategy'),
            'current_spot': self.state.get('current_spot'),
            'entry_spot': self.state.get('entry_spot'),
            'upper_be': self.state.get('upper_be'),
            'lower_be': self.state.get('lower_be'),
            'net_credit': self.state.get('net_credit'),
            'current_pnl': self.state.get('current_pnl'),
        }
    
    def get_summary(self) -> str:
        """Get position summary."""
        return f"""
╔════════════════════════════════════════════════════════════════╗
║  POSITION SUMMARY
╚════════════════════════════════════════════════════════════════╝

Strategy: {self.state.get('strategy')}
Status: {self.state.get('phase')}

Entry Spot: {self.state.get('entry_spot'):.0f}
Current Spot: {self.state.get('current_spot'):.0f}
Entry Price (Credit): Rs {self.state.get('net_credit'):.0f}

Breakevens:
  Upper: {self.state.get('upper_be'):.0f}
  Lower: {self.state.get('lower_be'):.0f}

Current P&L: Rs {self.state.get('current_pnl'):.0f} ({self.state.get('current_pnl_pct'):.1f}%)
Max Profit: Rs {self.max_profit:.0f}
Max Loss: Rs {self.max_loss:.0f}

Closed: {self.closed}
Reason: {self.close_reason or 'N/A'}
        """


# ═══════════════════════════════════════════════════════════════════
#  TRADING SESSION — Complete workflow
# ═══════════════════════════════════════════════════════════════════

class TradingSession:
    """
    Manages a complete trading session:
    1. Setup strategy
    2. Monitor live position
    3. Close when rules triggered
    4. Log and analyze
    """
    
    def __init__(self):
        """Initialize trading session."""
        print("🚀 Initializing Trading Session...\n")
        
        # Initialize all modules
        try:
            self.alerts = AlertManager(
                email_config=EMAIL_CONFIG,
                sms_config=SMS_CONFIG
            )
            print("✅ Alerts system initialized")
        except Exception as e:
            print(f"⚠️  Alerts system init failed: {e}")
            self.alerts = None
        
        try:
            self.broker = BrokerManager(
                broker_name=BROKER_CONFIG['broker'],
                config=BROKER_CONFIG
            )
            print(f"✅ Broker manager initialized ({self.broker.broker_name})")
        except Exception as e:
            print(f"⚠️  Broker manager init failed: {e}")
            self.broker = None
        
        self.log = TradeLog('trade_log.json')
        print("✅ Trade log initialized")
        
        self.metrics = PerformanceMetrics(self.log)
        print("✅ Performance metrics initialized")
        
        self.positions = []
    
    def setup_position(self, strategy: str, spot: Optional[float] = None) -> Optional[PositionManager]:
        """
        Setup a new position.
        
        Args:
            strategy: Strategy name (e.g., 'Iron Fly')
            spot: Current spot price (auto-fetch if None)
        
        Returns:
            PositionManager instance or None if setup failed
        """
        print(f"\n📌 Setting up {strategy} position...")
        
        # Get current spot
        if not spot and self.broker:
            spot = self.broker.get_spot_price()
        
        if not spot:
            print("❌ Cannot fetch spot price")
            return None
        
        print(f"Current Spot: {spot:.0f}")
        
        # Create mock position state (in real code, use options_manager)
        state = {
            'strategy': strategy,
            'phase': 'LIVE',
            'entry_spot': spot,
            'current_spot': spot,
            'entry_date': datetime.now().strftime('%Y-%m-%d'),
            'entry_time': datetime.now().strftime('%H:%M:%S'),
            'atm_strike': round(spot / 100) * 100,
            'net_credit': 250,  # Example
            'upper_be': spot + 200,
            'lower_be': spot - 200,
            'current_pnl': 0,
            'current_pnl_pct': 0,
            'lot_size': NIFTY_LOT_SIZE,
            'legs': {
                'CE_ATM': {'action': 'SELL', 'strike': round(spot/100)*100},
                'PE_ATM': {'action': 'SELL', 'strike': round(spot/100)*100},
            },
        }
        
        # Create position manager
        position = PositionManager(state, self.alerts, self.broker, self.log)
        
        # Send alert: position opened
        if self.alerts:
            self.alerts.send_position_opened_alert(
                strategy=strategy,
                context=state
            )
        
        self.positions.append(position)
        print(position.get_summary())
        return position
    
    def run_live_monitor(self, position: PositionManager, 
                        monitor_duration_sec: int = 300,
                        update_interval_sec: int = 60) -> None:
        """
        Monitor position for specified duration.
        
        Args:
            position: PositionManager instance
            monitor_duration_sec: Total monitoring time
            update_interval_sec: Update frequency
        """
        print(f"\n⏱️  Monitoring for {monitor_duration_sec}s ({monitor_duration_sec//update_interval_sec} updates)\n")
        
        elapsed = 0
        while elapsed < monitor_duration_sec and position.state['phase'] == 'LIVE':
            # Fetch current spot
            if self.broker:
                current_spot = self.broker.get_spot_price()
                print(f"[{elapsed:03d}s] Spot: {current_spot:.0f} | ", end='')
                
                # Update position
                still_active = position.update(current_spot)
                
                if still_active:
                    print(f"P&L: {position.state['current_pnl']:+.0f} Rs ({position.state['current_pnl_pct']:+.1f}%)")
                else:
                    print(f"POSITION CLOSED ({position.close_reason})")
                    break
            
            time.sleep(update_interval_sec)
            elapsed += update_interval_sec
        
        if position.state['phase'] == 'LIVE':
            print("⏰ Monitoring time expired, closing position")
            position.close_position(reason='TIME_LIMIT')
    
    def analyze_session(self) -> None:
        """Generate analysis and reports for the session."""
        print("\n" + "="*70)
        print("📊 SESSION ANALYSIS")
        print("="*70 + "\n")
        
        # Overall metrics
        metrics = self.metrics.calculate_all()
        
        print(f"Total Trades: {metrics['total_trades']}")
        print(f"Win Rate: {metrics['win_rate']:.1f}%")
        print(f"Total P&L: Rs {metrics['total_pnl']:,.0f}")
        print(f"Avg Trade P&L: Rs {metrics['avg_pnl']:,.0f}")
        print(f"Max Drawdown: Rs {metrics['max_drawdown']:,.0f}")
        print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
        
        # By-strategy metrics
        by_strategy = self.metrics.get_by_strategy()
        if len(by_strategy) > 0:
            print("\nBy Strategy:")
            for strategy, m in by_strategy.items():
                if m['total_trades'] > 0:
                    print(f"  {strategy}: {m['total_trades']} trades, {m['win_rate']:.1f}% win, Rs {m['total_pnl']:,.0f}")
        
        # Generate reports
        report = PerformanceReport(self.metrics)
        report.generate_detailed_report('performance_report.txt')
        print("\n✅ Report saved to performance_report.txt")
    
    def backtest_session(self) -> None:
        """Backtest the trades from this session."""
        print("\n" + "="*70)
        print("📈 BACKTESTING SESSION TRADES")
        print("="*70 + "\n")
        
        # Group trades by strategy
        trades_by_strategy = {}
        for trade in self.log.get_trades(status='CLOSED'):
            strategy = trade.get('strategy')
            if strategy not in trades_by_strategy:
                trades_by_strategy[strategy] = []
            trades_by_strategy[strategy].append(trade)
        
        # Create backtest scenarios
        engine = BacktestEngine()
        
        for strategy, trades in trades_by_strategy.items():
            if not trades:
                continue
            
            scenario = BacktestScenario(
                strategy=strategy,
                symbol='^NSEI',
                start_date=trades[0].get('entry_date', '2026-01-01'),
                end_date=trades[-1].get('exit_date', '2026-12-31'),
                initial_capital=INITIAL_CAPITAL,
                lot_size=NIFTY_LOT_SIZE
            )
            
            # Add trades to scenario
            for trade in trades:
                scenario.add_trade(
                    entry_date=trade['entry_date'],
                    entry_price=trade['entry_price'],
                    exit_date=trade['exit_date'],
                    exit_price=trade['exit_price'],
                    trade_type='credit' if trade['entry_price'] > 0 else 'debit'
                )
            
            engine.add_scenario(scenario)
        
        # Run backtest
        engine.run_all(load_price_data=False)
        engine.generate_report('backtest_report.txt')
        print("✅ Backtest report saved to backtest_report.txt")


# ═══════════════════════════════════════════════════════════════════
#  MAIN — Full workflow demonstration
# ═══════════════════════════════════════════════════════════════════

def main():
    """Run complete trading session with all integrated modules."""
    
    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*10 + "OPTIONS MANAGER v5.5 — INTEGRATION EXAMPLE" + " "*16 + "║")
    print("║" + " "*15 + "Alert + Broker + Analytics + Backtest" + " "*17 + "║")
    print("╚" + "="*68 + "╝\n")
    
    # Initialize session
    session = TradingSession()
    
    # Setup Iron Fly position
    position = session.setup_position(strategy='Iron Fly')
    
    if position:
        # Monitor position (simulated with random updates)
        # In real code, this would fetch live data from broker
        print("\n" + "="*70)
        print("⏱️  LIVE MONITORING")
        print("="*70)
        
        # Simulate monitoring with spot price updates
        mock_spots = [24500, 24505, 24510, 24515, 24520, 24525, 24530]
        
        for spot in mock_spots:
            time.sleep(1)  # Simulate delay
            print(f"Spot: {spot:.0f} | ", end='')
            
            still_active = position.update(spot)
            
            print(f"P&L: {position.state['current_pnl']:+.0f} Rs ({position.state['current_pnl_pct']:+.1f}%)")
            
            if not still_active:
                break
        
        # Close if still open
        if not position.closed:
            position.close_position(reason='SESSION_END')
        
        # Analyze session
        session.analyze_session()
        
        # Backtest trades
        session.backtest_session()
        
        print("\n✅ Trading session complete!")
        print("📁 Check these files for detailed results:")
        print("   - trade_log.json (trade history)")
        print("   - performance_report.txt (metrics)")
        print("   - backtest_report.txt (backtest results)")
    else:
        print("❌ Failed to setup position")


if __name__ == '__main__':
    main()

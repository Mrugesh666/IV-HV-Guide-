#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   BACKTESTING ENGINE v2 — Historical Strategy Replay            ║
║   (Integrated with options_manager.py)                           ║
║                                                                  ║
║   Replay strategies on past market data to evaluate performance ║
║   Calculates P&L, win rate, drawdown, Sharpe ratio              ║
║   Understands options_manager state and trade structures        ║
╚════════════════════════════════════════════════════════════════╝
"""

import json
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


class BacktestScenario:
    """Single backtest scenario (one strategy, one date range)."""
    
    def __init__(self, strategy: str, symbol: str, start_date: str, 
                 end_date: str, initial_capital: float = 100000,
                 lot_size: int = 75):
        """
        Initialize backtest scenario.
        
        Args:
            strategy: Strategy name ('Iron Fly', 'Iron Condor', etc)
            symbol: Stock symbol (e.g., '^NSEI')
            start_date: Start date 'YYYY-MM-DD'
            end_date: End date 'YYYY-MM-DD'
            initial_capital: Starting capital in Rs
            lot_size: Lot size for symbol (75 for NIFTY, 15 for BankNifty)
        """
        self.strategy = strategy
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.lot_size = lot_size
        
        self.trades = []
        self.daily_pnl = []
        self.equity_curve = []
        self.price_history = None
        
    def load_price_data(self) -> bool:
        """Load historical price data from Yahoo Finance."""
        try:
            self.price_history = yf.download(
                self.symbol,
                start=self.start_date,
                end=self.end_date,
                progress=False
            )
            
            if len(self.price_history) == 0:
                print(f"❌ No data for {self.symbol}")
                return False
            
            print(f"✅ Loaded {len(self.price_history)} days of data for {self.symbol}")
            return True
            
        except Exception as e:
            print(f"❌ Price data load failed: {e}")
            return False
    
    def add_trade(self, entry_date: str, entry_price: float, 
                  exit_date: str, exit_price: float, 
                  position_size: int = 1, trade_type: str = 'credit',
                  strategy_name: Optional[str] = None) -> Dict:
        """
        Add simulated trade to backtest.
        
        Args:
            entry_date: Trade entry date 'YYYY-MM-DD'
            entry_price: Net debit/credit in points
            exit_date: Exit date 'YYYY-MM-DD'
            exit_price: Exit price in points
            position_size: Number of lots
            trade_type: 'credit' or 'debit'
            strategy_name: Strategy name (defaults to self.strategy)
        """
        if not strategy_name:
            strategy_name = self.strategy
        
        # Calculate P&L based on trade type
        if trade_type == 'credit':
            # Credit strategy: entry is credit received, exit is debit paid
            pnl = (entry_price - exit_price) * position_size * self.lot_size
        else:
            # Debit strategy: entry is debit paid, exit is credit received
            pnl = (exit_price - entry_price) * position_size * self.lot_size
        
        # Avoid division by zero
        entry_capital = entry_price * position_size * self.lot_size
        if entry_capital == 0:
            pnl_pct = 0
        else:
            pnl_pct = (pnl / entry_capital) * 100
        
        trade = {
            'strategy': strategy_name,
            'entry_date': entry_date,
            'entry_price': entry_price,
            'exit_date': exit_date,
            'exit_price': exit_price,
            'position_size': position_size,
            'trade_type': trade_type,
            'lot_size': self.lot_size,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'days_held': self._days_between(entry_date, exit_date),
        }
        
        self.trades.append(trade)
        return trade
    
    def add_trade_from_options_manager_state(self, state: Dict) -> Optional[Dict]:
        """
        Add trade directly from options_manager.py state.
        
        Args:
            state: State dict from options_manager (must have phase='CLOSED')
        
        Returns:
            Trade dict or None if state invalid
        """
        if state.get('phase') != 'CLOSED':
            print(f"⚠️  Cannot add trade: state phase is {state.get('phase')}, not CLOSED")
            return None
        
        try:
            # Calculate net P&L from position
            net_credit = state.get('net_credit', 0)
            current_pnl = state.get('current_pnl', 0)
            
            entry_date = state.get('entry_date', datetime.now().strftime('%Y-%m-%d'))
            exit_date = datetime.now().strftime('%Y-%m-%d')
            
            trade = {
                'strategy': state.get('strategy', 'Unknown'),
                'entry_date': entry_date,
                'entry_price': net_credit,
                'exit_date': exit_date,
                'exit_price': current_pnl,
                'position_size': len([leg for leg in state.get('legs', {}).values() 
                                     if leg.get('action') == 'SELL']),
                'trade_type': 'credit' if net_credit > 0 else 'debit',
                'lot_size': state.get('lot_size', 75),
                'pnl': current_pnl,
                'pnl_pct': (current_pnl / net_credit * 100) if net_credit != 0 else 0,
                'days_held': self._days_between(entry_date, exit_date),
                'source': 'options_manager_state',
            }
            
            self.trades.append(trade)
            print(f"✅ Added trade from options_manager state: {trade['strategy']}")
            return trade
            
        except Exception as e:
            print(f"❌ Failed to add trade from state: {e}")
            return None
    
    def _days_between(self, date1: str, date2: str) -> int:
        """Calculate days between two dates."""
        try:
            d1 = datetime.strptime(date1, '%Y-%m-%d')
            d2 = datetime.strptime(date2, '%Y-%m-%d')
            return abs((d2 - d1).days)
        except:
            return 0
    
    def calculate_metrics(self) -> Dict:
        """Calculate comprehensive backtest metrics."""
        if not self.trades:
            return {
                'status': 'No trades',
                'total_trades': 0,
                'total_pnl': 0,
                'metrics': self._empty_metrics()
            }
        
        pnls = np.array([t['pnl'] for t in self.trades])
        total_pnl = float(np.sum(pnls))
        
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        
        win_count = len(wins)
        loss_count = len(losses)
        total_trades = len(self.trades)
        
        metrics = {
            'total_trades': total_trades,
            'winning_trades': win_count,
            'losing_trades': loss_count,
            'win_rate': (win_count / total_trades * 100) if total_trades > 0 else 0,
            'total_pnl': total_pnl,
            'avg_pnl': float(np.mean(pnls)),
            'avg_win': float(np.mean(wins)) if len(wins) > 0 else 0,
            'avg_loss': float(np.mean(losses)) if len(losses) > 0 else 0,
            'largest_win': float(np.max(pnls)) if len(pnls) > 0 else 0,
            'largest_loss': float(np.min(pnls)) if len(pnls) > 0 else 0,
            'profit_factor': self._profit_factor(wins, losses),
            'payoff_ratio': self._payoff_ratio(wins, losses),
            'max_drawdown': self._calculate_max_drawdown(),
            'avg_trade_duration': float(np.mean([t['days_held'] for t in self.trades])) if self.trades else 0,
            'sharpe_ratio': self._calculate_sharpe_ratio(),
            'roi': (total_pnl / self.initial_capital) * 100,
        }
        
        return metrics
    
    def _profit_factor(self, wins: np.ndarray, losses: np.ndarray) -> float:
        """Profit factor = gross profit / abs(gross loss)."""
        if len(losses) == 0 or np.sum(np.abs(losses)) == 0:
            return 0
        return float(np.sum(wins) / np.sum(np.abs(losses)))
    
    def _payoff_ratio(self, wins: np.ndarray, losses: np.ndarray) -> float:
        """Payoff ratio = avg win / abs(avg loss)."""
        if len(losses) == 0 or np.mean(losses) == 0:
            return 0
        return float(np.mean(wins) / np.abs(np.mean(losses)))
    
    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown."""
        if not self.trades:
            return 0
        
        cumulative = 0
        peak = 0
        max_dd = 0
        
        for trade in sorted(self.trades, key=lambda x: x['entry_date']):
            cumulative += trade['pnl']
            
            if cumulative > peak:
                peak = cumulative
            
            drawdown = peak - cumulative
            if drawdown > max_dd:
                max_dd = drawdown
        
        return -max_dd
    
    def _calculate_sharpe_ratio(self, risk_free_rate: float = 0.06) -> float:
        """Calculate Sharpe ratio (annual)."""
        if len(self.trades) < 2:
            return 0
        
        pnls = np.array([t['pnl'] for t in self.trades])
        
        # Daily returns (approximate)
        daily_returns = pnls / self.initial_capital
        
        # Annualized
        annual_return = np.mean(daily_returns) * 252
        annual_vol = np.std(daily_returns) * np.sqrt(252)
        
        if annual_vol == 0:
            return 0
        
        sharpe = (annual_return - risk_free_rate) / annual_vol
        return float(sharpe)
    
    def _empty_metrics(self) -> Dict:
        """Return empty metrics template."""
        return {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0,
            'total_pnl': 0,
            'avg_pnl': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'largest_win': 0,
            'largest_loss': 0,
            'profit_factor': 0,
            'payoff_ratio': 0,
            'max_drawdown': 0,
            'avg_trade_duration': 0,
            'sharpe_ratio': 0,
            'roi': 0,
        }
    
    def get_summary(self) -> str:
        """Get text summary of backtest."""
        metrics = self.calculate_metrics()
        
        summary = f"""
╔════════════════════════════════════════════════════════════════╗
║  BACKTEST SUMMARY: {self.strategy.upper()}
║  Period: {self.start_date} to {self.end_date}
║  Lot Size: {self.lot_size} | Initial Capital: Rs {self.initial_capital:,.0f}
╚════════════════════════════════════════════════════════════════╝

📊 TRADE STATISTICS
  Total Trades: {metrics['total_trades']}
  Winning: {metrics['winning_trades']} | Losing: {metrics['losing_trades']}
  Win Rate: {metrics['win_rate']:.1f}%

💰 PROFITABILITY
  Total P&L: Rs {metrics['total_pnl']:,.0f}
  Avg Trade P&L: Rs {metrics['avg_pnl']:,.0f}
  Avg Win: Rs {metrics['avg_win']:,.0f}
  Avg Loss: Rs {metrics['avg_loss']:,.0f}
  Largest Win: Rs {metrics['largest_win']:,.0f}
  Largest Loss: Rs {metrics['largest_loss']:,.0f}

⚖️ RISK METRICS
  Profit Factor: {metrics['profit_factor']:.2f}
  Payoff Ratio: {metrics['payoff_ratio']:.2f}
  Max Drawdown: Rs {metrics['max_drawdown']:,.0f}

📈 RETURNS
  ROI: {metrics['roi']:.1f}%
  Sharpe Ratio: {metrics['sharpe_ratio']:.2f}
  Avg Trade Duration: {metrics['avg_trade_duration']:.0f} days

        """
        
        return summary


class BacktestEngine:
    """Multi-scenario backtesting engine."""
    
    def __init__(self):
        """Initialize backtesting engine."""
        self.scenarios = []
        self.results = {}
        
    def add_scenario(self, scenario: BacktestScenario) -> None:
        """Add scenario to backtest."""
        self.scenarios.append(scenario)
    
    def run_all(self, load_price_data: bool = True) -> Dict:
        """
        Run all scenarios.
        
        Args:
            load_price_data: Whether to load historical data (set False for manual trades)
        """
        print("🚀 Starting backtest run...\n")
        
        for scenario in self.scenarios:
            scenario_key = f"{scenario.strategy}_{scenario.start_date}_{scenario.end_date}"
            
            print(f"📌 Running: {scenario_key}")
            
            if load_price_data and not scenario.load_price_data():
                continue
            
            # For scenarios, trades are manually added or from options_manager state
            # In production, would generate trades based on strategy rules
            
            metrics = scenario.calculate_metrics()
            self.results[scenario_key] = {
                'scenario': scenario,
                'metrics': metrics,
            }
            
            print(scenario.get_summary())
        
        return self.results
    
    def compare_strategies(self) -> Dict:
        """Compare metrics across all strategies."""
        if not self.results:
            return {}
        
        comparison = {
            'strategies': [],
            'total_pnl': [],
            'win_rates': [],
            'sharpe_ratios': [],
            'max_drawdowns': [],
            'roi': [],
        }
        
        for key, result in self.results.items():
            strategy_name = result['scenario'].strategy
            metrics = result['metrics']
            
            comparison['strategies'].append(strategy_name)
            comparison['total_pnl'].append(metrics['total_pnl'])
            comparison['win_rates'].append(metrics['win_rate'])
            comparison['sharpe_ratios'].append(metrics['sharpe_ratio'])
            comparison['max_drawdowns'].append(metrics['max_drawdown'])
            comparison['roi'].append(metrics['roi'])
        
        return comparison
    
    def generate_report(self, output_file: str = 'backtest_report.txt') -> None:
        """Generate backtest report."""
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("╔════════════════════════════════════════════════════════════════╗\n")
            f.write("║              BACKTEST REPORT — OPTIONS MANAGER                  ║\n")
            f.write("╚════════════════════════════════════════════════════════════════╝\n\n")
            
            f.write(f"Generated: {datetime.now().isoformat()}\n")
            f.write(f"Total Scenarios: {len(self.scenarios)}\n\n")
            
            for key, result in self.results.items():
                scenario = result['scenario']
                f.write(scenario.get_summary() + "\n")
            
            # Comparison
            comparison = self.compare_strategies()
            if comparison['strategies']:
                f.write("\n╔════════════════════════════════════════════════════════════════╗\n")
                f.write("║              STRATEGY COMPARISON                                 ║\n")
                f.write("╚════════════════════════════════════════════════════════════════╝\n\n")
                
                for i, strategy in enumerate(comparison['strategies']):
                    f.write(f"{strategy.upper()}\n")
                    f.write(f"  P&L: Rs {comparison['total_pnl'][i]:,.0f}\n")
                    f.write(f"  Win Rate: {comparison['win_rates'][i]:.1f}%\n")
                    f.write(f"  Sharpe: {comparison['sharpe_ratios'][i]:.2f}\n")
                    f.write(f"  Max DD: Rs {comparison['max_drawdowns'][i]:,.0f}\n")
                    f.write(f"  ROI: {comparison['roi'][i]:.1f}%\n\n")
        
        print(f"✅ Report saved to {output_file}")


# ═══════════════════════════════════════════════════════════════════
#  USAGE EXAMPLE
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # Example 1: Iron Fly strategy backtest
    engine = BacktestEngine()
    
    scenario1 = BacktestScenario(
        strategy='Iron Fly',
        symbol='^NSEI',
        start_date='2026-04-01',
        end_date='2026-04-30',
        initial_capital=100000,
        lot_size=75
    )
    
    # Manually add trades (credit strategies: entry > exit)
    scenario1.add_trade('2026-04-01', 250, '2026-04-07', 125, trade_type='credit')
    scenario1.add_trade('2026-04-08', 280, '2026-04-15', 140, trade_type='credit')
    scenario1.add_trade('2026-04-16', 200, '2026-04-23', 100, trade_type='credit')
    scenario1.add_trade('2026-04-24', 300, '2026-04-30', 150, trade_type='credit')
    
    engine.add_scenario(scenario1)
    
    # Example 2: Iron Condor
    scenario2 = BacktestScenario(
        strategy='Iron Condor',
        symbol='^NSEI',
        start_date='2026-04-01',
        end_date='2026-04-30',
        initial_capital=100000,
        lot_size=75
    )
    
    scenario2.add_trade('2026-04-02', 350, '2026-04-08', 175, trade_type='credit')
    scenario2.add_trade('2026-04-09', 400, '2026-04-16', 200, trade_type='credit')
    scenario2.add_trade('2026-04-17', 320, '2026-04-24', 160, trade_type='credit')
    
    engine.add_scenario(scenario2)
    
    # Run backtest
    engine.run_all(load_price_data=False)
    
    # Generate report
    engine.generate_report('backtest_report.txt')

#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   PERFORMANCE ANALYTICS v2 — Trade Metrics & Analytics          ║
║   (Integrated with options_manager.py)                           ║
║                                                                  ║
║   Track win rate, Sharpe, drawdown, risk metrics in real-time   ║
║   Generate performance reports and analytics dashboards         ║
║   Direct integration with options_manager state & positions     ║
╚══════════════════════════════════════════════════════════════════╝
"""

import json
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path


class TradeLog:
    """Maintains log of all completed trades."""
    
    def __init__(self, log_file: str = 'trade_log.json'):
        """
        Initialize trade log.
        
        Args:
            log_file: Path to trade log JSON file
        """
        self.log_file = Path(log_file)
        self.trades = []
        self.load_trades()
    
    def load_trades(self) -> None:
        """Load trades from file."""
        try:
            if self.log_file.exists():
                with open(self.log_file, 'r', encoding='utf-8') as f:
                    self.trades = json.load(f)
            else:
                self.trades = []
        except Exception as e:
            print(f"⚠️  Trade log load failed: {e}")
            self.trades = []
    
    def add_trade(self, trade_data: Dict) -> None:
        """
        Add completed trade to log.
        
        Args:
            trade_data: {
                'id': 'unique_id',
                'strategy': 'Iron Fly',
                'entry_date': '2026-06-10',
                'entry_time': '10:30:00',
                'entry_price': 250,  # Net debit/credit
                'exit_date': '2026-06-15',
                'exit_time': '15:00:00',
                'exit_price': 125,   # Exit value
                'position_size': 1,
                'pnl': 9375,  # (250-125)*75
                'pnl_pct': 37.5,
                'days_held': 5,
                'status': 'CLOSED',
            }
        """
        trade_with_timestamp = {
            **trade_data,
            'logged_at': datetime.now().isoformat(),
        }
        
        self.trades.append(trade_with_timestamp)
        self.save_trades()
    
    def add_trade_from_options_manager_state(self, state: Dict) -> bool:
        """
        Add closed position directly from options_manager state.
        
        Args:
            state: State dict from options_manager (must have phase='CLOSED')
        
        Returns:
            True if trade added successfully
        """
        if state.get('phase') != 'CLOSED':
            print(f"⚠️  State phase is {state.get('phase')}, not CLOSED")
            return False
        
        try:
            # Calculate P&L from position
            net_credit = state.get('net_credit', 0)
            current_pnl = state.get('current_pnl', 0)
            
            entry_time = state.get('entry_time', '10:30:00')
            entry_date = state.get('entry_date', datetime.now().strftime('%Y-%m-%d'))
            exit_date = datetime.now().strftime('%Y-%m-%d')
            exit_time = datetime.now().strftime('%H:%M:%S')
            
            # Count position size (number of lots)
            legs = state.get('legs', {})
            position_size = max(1, len([l for l in legs.values() if l.get('action') == 'SELL']))
            
            trade = {
                'id': f"{state.get('strategy')}_{entry_date}_{hash(str(legs))%10000}",
                'strategy': state.get('strategy', 'Unknown'),
                'entry_date': entry_date,
                'entry_time': entry_time,
                'entry_price': net_credit,
                'exit_date': exit_date,
                'exit_time': exit_time,
                'exit_price': current_pnl,
                'position_size': position_size,
                'pnl': current_pnl,
                'pnl_pct': (current_pnl / net_credit * 100) if net_credit != 0 else 0,
                'days_held': (datetime.strptime(exit_date, '%Y-%m-%d') - 
                            datetime.strptime(entry_date, '%Y-%m-%d')).days,
                'status': 'CLOSED',
                'source': 'options_manager',
                'entry_spot': state.get('entry_spot'),
                'exit_spot': state.get('current_spot'),
                'upper_breakeven': state.get('upper_be'),
                'lower_breakeven': state.get('lower_be'),
            }
            
            self.add_trade(trade)
            return True
            
        except Exception as e:
            print(f"❌ Failed to add trade from state: {e}")
            return False
    
    def save_trades(self) -> None:
        """Save trades to file."""
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, 'w', encoding='utf-8') as f:
                json.dump(self.trades, f, indent=2, default=str)
            print(f"✅ Trade log saved ({len(self.trades)} trades)")
        except Exception as e:
            print(f"⚠️  Trade log save failed: {e}")
    
    def get_trades(self, strategy: Optional[str] = None,
                   status: Optional[str] = None) -> List[Dict]:
        """
        Filter trades by criteria.
        
        Args:
            strategy: Filter by strategy name
            status: Filter by status ('CLOSED', 'OPEN')
        """
        trades = self.trades
        
        if strategy:
            trades = [t for t in trades if t.get('strategy') == strategy]
        
        if status:
            trades = [t for t in trades if t.get('status') == status]
        
        return trades
    
    def get_latest_trades(self, n: int = 10) -> List[Dict]:
        """Get n most recent trades."""
        return sorted(self.trades, 
                     key=lambda t: t.get('logged_at', ''),
                     reverse=True)[:n]


class PerformanceMetrics:
    """Calculate performance metrics from trade history."""
    
    def __init__(self, trade_log: TradeLog):
        """
        Initialize metrics calculator.
        
        Args:
            trade_log: TradeLog instance
        """
        self.trade_log = trade_log
    
    def calculate_all(self, strategy: Optional[str] = None) -> Dict:
        """
        Calculate all performance metrics.
        
        Args:
            strategy: Calculate metrics for specific strategy or all
            
        Returns:
            Comprehensive metrics dict with all risk-adjusted ratios
        """
        trades = self.trade_log.get_trades(
            strategy=strategy,
            status='CLOSED'
        )
        
        if not trades:
            return self._empty_metrics()
        
        pnls = np.array([t.get('pnl', 0) for t in trades])
        
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        
        total_pnl = float(np.sum(pnls))
        total_trades = len(trades)
        win_count = len(wins)
        loss_count = len(losses)
        
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
            'std_dev': float(np.std(pnls)),
            'profit_factor': self._profit_factor(wins, losses),
            'payoff_ratio': self._payoff_ratio(wins, losses),
            'max_drawdown': self._max_drawdown(pnls),
            'sharpe_ratio': self._sharpe_ratio(pnls),
            'sortino_ratio': self._sortino_ratio(pnls),
            'calmar_ratio': self._calmar_ratio(pnls),
            'roi': (total_pnl / 100000) * 100,  # Assuming 1L capital
            'recovery_factor': self._recovery_factor(pnls),
            'consecutive_wins': self._longest_streak(pnls, 'wins'),
            'consecutive_losses': self._longest_streak(pnls, 'losses'),
            'risk_reward': self._risk_reward_ratio(pnls),
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
    
    def _max_drawdown(self, pnls: np.ndarray) -> float:
        """Calculate maximum drawdown."""
        cumsum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumsum)
        drawdown = cumsum - peak
        return float(np.min(drawdown))
    
    def _sharpe_ratio(self, pnls: np.ndarray, risk_free_rate: float = 0.06) -> float:
        """Calculate Sharpe ratio (annualized)."""
        if len(pnls) < 2:
            return 0
        
        daily_returns = pnls / 100000  # Normalized by capital
        annual_return = np.mean(daily_returns) * 252
        annual_vol = np.std(daily_returns) * np.sqrt(252)
        
        if annual_vol == 0:
            return 0
        
        return float((annual_return - risk_free_rate) / annual_vol)
    
    def _sortino_ratio(self, pnls: np.ndarray, risk_free_rate: float = 0.06) -> float:
        """Calculate Sortino ratio (downside deviation only)."""
        if len(pnls) < 2:
            return 0
        
        daily_returns = pnls / 100000
        annual_return = np.mean(daily_returns) * 252
        
        # Downside deviation (only negative returns)
        downside = daily_returns[daily_returns < 0]
        downside_vol = np.std(downside) * np.sqrt(252) if len(downside) > 0 else 0
        
        if downside_vol == 0:
            return 0
        
        return float((annual_return - risk_free_rate) / downside_vol)
    
    def _calmar_ratio(self, pnls: np.ndarray) -> float:
        """Calculate Calmar ratio = annual return / abs(max drawdown)."""
        daily_returns = pnls / 100000
        annual_return = np.mean(daily_returns) * 252
        max_dd = self._max_drawdown(pnls)
        
        if max_dd == 0:
            return 0
        
        return float(annual_return / abs(max_dd))
    
    def _recovery_factor(self, pnls: np.ndarray) -> float:
        """Recovery factor = total profit / abs(max drawdown)."""
        total_pnl = np.sum(pnls)
        max_dd = self._max_drawdown(pnls)
        
        if max_dd == 0:
            return 0
        
        return float(total_pnl / abs(max_dd))
    
    def _longest_streak(self, pnls: np.ndarray, streak_type: str) -> int:
        """Find longest consecutive wins or losses."""
        is_win = pnls > 0
        
        if streak_type == 'losses':
            is_win = pnls < 0
        
        current_streak = 0
        longest_streak = 0
        
        for win in is_win:
            if win:
                current_streak += 1
                longest_streak = max(longest_streak, current_streak)
            else:
                current_streak = 0
        
        return longest_streak
    
    def _risk_reward_ratio(self, pnls: np.ndarray) -> float:
        """Risk/Reward ratio for position sizing."""
        if len(pnls) == 0:
            return 0
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        if len(wins) == 0 or len(losses) == 0:
            return 0
        return float(np.sum(np.abs(losses)) / np.sum(wins))
    
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
            'std_dev': 0,
            'profit_factor': 0,
            'payoff_ratio': 0,
            'max_drawdown': 0,
            'sharpe_ratio': 0,
            'sortino_ratio': 0,
            'calmar_ratio': 0,
            'roi': 0,
            'recovery_factor': 0,
            'consecutive_wins': 0,
            'consecutive_losses': 0,
            'risk_reward': 0,
        }
    
    def get_by_strategy(self) -> Dict[str, Dict]:
        """Get metrics broken down by strategy."""
        strategies = set(t.get('strategy') for t in self.trade_log.trades if t.get('strategy'))
        
        metrics_by_strategy = {}
        for strategy in strategies:
            metrics_by_strategy[strategy] = self.calculate_all(strategy=strategy)
        
        return metrics_by_strategy


class PerformanceReport:
    """Generate performance reports and dashboards."""
    
    def __init__(self, metrics: PerformanceMetrics):
        """Initialize report generator."""
        self.metrics = metrics
    
    def generate_summary(self, strategy: Optional[str] = None) -> str:
        """Generate summary report."""
        metrics = self.metrics.calculate_all(strategy=strategy)
        
        report = f"""
╔════════════════════════════════════════════════════════════════╗
║  PERFORMANCE METRICS SUMMARY
║  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
╚════════════════════════════════════════════════════════════════╝

📊 TRADE STATISTICS
  Total Trades: {metrics['total_trades']}
  Winning: {metrics['winning_trades']} ({metrics['win_rate']:.1f}%)
  Losing: {metrics['losing_trades']}

💰 PROFITABILITY
  Total P&L: Rs {metrics['total_pnl']:,.0f}
  Avg Trade P&L: Rs {metrics['avg_pnl']:,.0f}
  Avg Win: Rs {metrics['avg_win']:,.0f}
  Avg Loss: Rs {metrics['avg_loss']:,.0f}
  Largest Win: Rs {metrics['largest_win']:,.0f}
  Largest Loss: Rs {metrics['largest_loss']:,.0f}

⚖️ RISK METRICS
  Profit Factor: {metrics['profit_factor']:.2f}x
  Payoff Ratio: {metrics['payoff_ratio']:.2f}
  Max Drawdown: Rs {metrics['max_drawdown']:,.0f}
  Recovery Factor: {metrics['recovery_factor']:.2f}
  Risk/Reward Ratio: {metrics['risk_reward']:.2f}

📈 RISK-ADJUSTED RETURNS
  Sharpe Ratio: {metrics['sharpe_ratio']:.2f}
  Sortino Ratio: {metrics['sortino_ratio']:.2f}
  Calmar Ratio: {metrics['calmar_ratio']:.2f}
  ROI: {metrics['roi']:.1f}%

🔥 STREAKS
  Longest Win Streak: {metrics['consecutive_wins']} trades
  Longest Loss Streak: {metrics['consecutive_losses']} trades

        """
        
        return report
    
    def generate_detailed_report(self, output_file: str = 'performance_report.txt') -> None:
        """Generate detailed performance report with trade breakdown."""
        with open(output_file, 'w', encoding='utf-8') as f:
            # Overall metrics
            f.write("╔════════════════════════════════════════════════════════════════╗\n")
            f.write("║              DETAILED PERFORMANCE REPORT                        ║\n")
            f.write("╚════════════════════════════════════════════════════════════════╝\n\n")
            
            overall_metrics = self.metrics.calculate_all()
            f.write(self.generate_summary())
            
            # By-strategy breakdown
            by_strategy = self.metrics.get_by_strategy()
            if len(by_strategy) > 1:
                f.write("\n╔════════════════════════════════════════════════════════════════╗\n")
                f.write("║              BY-STRATEGY BREAKDOWN                              ║\n")
                f.write("╚════════════════════════════════════════════════════════════════╝\n\n")
                
                for strategy, metrics in by_strategy.items():
                    if metrics['total_trades'] > 0:
                        f.write(f"\n{strategy.upper()}\n")
                        f.write(f"  Trades: {metrics['total_trades']} | Win Rate: {metrics['win_rate']:.1f}%\n")
                        f.write(f"  Total P&L: Rs {metrics['total_pnl']:,.0f}\n")
                        f.write(f"  Sharpe: {metrics['sharpe_ratio']:.2f} | Sortino: {metrics['sortino_ratio']:.2f}\n")
                        f.write(f"  Max DD: Rs {metrics['max_drawdown']:,.0f} | Recovery Factor: {metrics['recovery_factor']:.2f}\n")
            
            # Trade list
            f.write("\n╔════════════════════════════════════════════════════════════════╗\n")
            f.write("║              TRADE-BY-TRADE BREAKDOWN                            ║\n")
            f.write("╚════════════════════════════════════════════════════════════════╝\n\n")
            
            trades = self.metrics.trade_log.get_trades(status='CLOSED')
            if trades:
                for i, trade in enumerate(sorted(trades, key=lambda x: x.get('exit_date', '')), 1):
                    f.write(f"\n{i}. {trade.get('strategy')} | ")
                    f.write(f"{trade.get('entry_date')} → {trade.get('exit_date')} | ")
                    f.write(f"Days: {trade.get('days_held', 0)} | ")
                    f.write(f"P&L: Rs {trade.get('pnl', 0):,.0f} ({trade.get('pnl_pct', 0):.1f}%)\n")
            else:
                f.write("No closed trades found.\n")
        
        print(f"✅ Report saved to {output_file}")


# ═══════════════════════════════════════════════════════════════════
#  USAGE EXAMPLE
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # Initialize trade log
    log = TradeLog('trade_log.json')
    
    # Add sample trades
    log.add_trade({
        'id': 'IRONFLY_20260610_001',
        'strategy': 'Iron Fly',
        'entry_date': '2026-06-10',
        'entry_time': '10:30:00',
        'entry_price': 250,
        'exit_date': '2026-06-15',
        'exit_time': '14:00:00',
        'exit_price': 125,
        'position_size': 1,
        'pnl': 9375,
        'pnl_pct': 37.5,
        'days_held': 5,
        'status': 'CLOSED',
    })
    
    log.add_trade({
        'id': 'IRONCONDOR_20260601_001',
        'strategy': 'Iron Condor',
        'entry_date': '2026-06-01',
        'entry_time': '09:45:00',
        'entry_price': 350,
        'exit_date': '2026-06-08',
        'exit_time': '15:30:00',
        'exit_price': 175,
        'position_size': 1,
        'pnl': 13125,
        'pnl_pct': 37.5,
        'days_held': 7,
        'status': 'CLOSED',
    })
    
    # Calculate metrics
    metrics = PerformanceMetrics(log)
    all_metrics = metrics.calculate_all()
    
    print(f"Total Trades: {all_metrics['total_trades']}")
    print(f"Win Rate: {all_metrics['win_rate']:.1f}%")
    print(f"Total P&L: Rs {all_metrics['total_pnl']:,.0f}")
    print(f"Sharpe Ratio: {all_metrics['sharpe_ratio']:.2f}")
    
    # Generate reports
    report = PerformanceReport(metrics)
    print(report.generate_summary())
    report.generate_detailed_report('performance_report.txt')

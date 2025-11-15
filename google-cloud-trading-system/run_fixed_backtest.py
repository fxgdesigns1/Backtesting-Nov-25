#!/usr/bin/env python3
"""
Run Fixed Backtest

This script runs a backtest using the universal backtest fix to properly handle OANDA data format.
It tests the fundamental characteristics of each strategy with real market data.
"""

import os
import sys
import logging
import json
import argparse
from datetime import datetime

sys.path.insert(0, '.')

logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import required modules
from src.strategies.momentum_trading import MomentumTradingStrategy
from src.strategies.gold_scalping_optimized import GoldScalpingStrategy
from universal_backtest_fix import run_backtest_for_strategies

LIVE_MIMIC_PROFILE = {
    "spread_multiplier": 6.0,
    "threshold_multiplier": 0.4,
    "breakout_multiplier": 0.1,
    "pullback_mode": "disabled",
    "min_time_between_trades": 5,
    "min_confirmations": 1,
    "min_volatility": 0.0002,
    "min_atr": 0.5,
    "max_spread": 1.5
}


def parse_args():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Run universal fixed backtest.")
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Number of trailing days to backtest (default: 14).",
    )
    parser.add_argument(
        "--include",
        choices=["all", "gold", "momentum"],
        default="all",
        help="Subset of strategies to include (default: all).",
    )
    return parser.parse_args()


def get_backtest_settings():
    """Return a copy of the mandatory live-mimic profile."""
    return LIVE_MIMIC_PROFILE.copy()

def configure_strategy_parameters(strategy, strategy_type):
    """Configure strategy with appropriate fundamental parameters"""
    if strategy_type == "momentum":
        # Set proper momentum strategy parameters
        strategy.min_adx = 25.0  # Strong trend required
        strategy.min_momentum = 0.005  # Significant momentum required
        strategy.min_quality_score = 70  # Only high-quality setups
        strategy.momentum_period = 20  # Longer period for trend confirmation
        strategy.trend_period = 50  # Long-term trend confirmation
        strategy.stop_loss_atr = 3.0  # Wider stop to avoid premature exits
        strategy.take_profit_atr = 6.0  # Reasonable profit target
        strategy.max_trades_per_day = 5  # Limit number of trades
        strategy.min_time_between_trades_minutes = 120  # Avoid overtrading
        strategy.require_trend_continuation = True  # Ensure trend alignment
        
        logger.info(f"✅ Configured momentum strategy with fundamental parameters:")
        logger.info(f"   - min_adx: {strategy.min_adx}")
        logger.info(f"   - min_momentum: {strategy.min_momentum}")
        logger.info(f"   - min_quality_score: {strategy.min_quality_score}")
        logger.info(f"   - momentum_period: {strategy.momentum_period}")
        logger.info(f"   - trend_period: {strategy.trend_period}")
        logger.info(f"   - R:R ratio: 1:{strategy.take_profit_atr/strategy.stop_loss_atr:.1f}")
    
    elif strategy_type == "scalping":
        # Set proper scalping strategy parameters
        strategy.min_spread = 0.0005  # Maximum acceptable spread
        strategy.min_volatility = 0.0015  # Minimum volatility required
        strategy.max_volatility = 0.0050  # Maximum volatility allowed
        strategy.stop_loss_pips = 15  # Tight stop loss
        strategy.take_profit_pips = 30  # Reasonable profit target
        strategy.max_trades_per_day = 10  # Allow more trades for scalping
        strategy.min_time_between_trades_minutes = 30  # Allow more frequent trades
        
        logger.info(f"✅ Configured scalping strategy with fundamental parameters:")
        logger.info(f"   - min_spread: {strategy.min_spread}")
        logger.info(f"   - volatility range: {strategy.min_volatility}-{strategy.max_volatility}")
        logger.info(f"   - stop_loss_pips: {strategy.stop_loss_pips}")
        logger.info(f"   - take_profit_pips: {strategy.take_profit_pips}")
        logger.info(f"   - R:R ratio: 1:{strategy.take_profit_pips/strategy.stop_loss_pips:.1f}")

def main():
    """Run backtest for all strategies with proper fundamental characteristics"""
    args = parse_args()
    backtest_settings = get_backtest_settings()

    logger.info("\n" + "="*70)
    logger.info(f"🎯 {args.days}-DAY BACKTEST - {args.include.upper()} STRATEGIES (FIXED)")
    logger.info("="*70)
    logger.info("Win Rate Threshold: 50%")
    logger.info("Below 50% = FAILURE ❌")
    logger.info("="*70 + "\n")
    
    # Define strategies to test with their fundamental characteristics
    momentum_strategy = MomentumTradingStrategy()
    configure_strategy_parameters(momentum_strategy, "momentum")
    
    gold_strategy = GoldScalpingStrategy()
    configure_strategy_parameters(gold_strategy, "scalping")
    logger.info("🧪 Using mandatory live-mimic backtest profile (legacy mode disabled)")
    if backtest_settings['max_spread'] is not None:
        gold_strategy.max_spread = backtest_settings['max_spread']
    disable_pullback = backtest_settings['pullback_mode'] == 'disabled'
    gold_strategy.enable_backtest_mode(
        spread_multiplier=backtest_settings['spread_multiplier'],
        threshold_multiplier=backtest_settings['threshold_multiplier'],
        breakout_multiplier=backtest_settings['breakout_multiplier'],
        disable_pullback=disable_pullback,
        min_time_between_trades_minutes=backtest_settings['min_time_between_trades'],
        min_confirmations=backtest_settings['min_confirmations'],
        adaptive_min_volatility=backtest_settings['min_volatility'],
        adaptive_min_atr=backtest_settings['min_atr']
    )
    
    strategies = []
    if args.include in ("all", "momentum"):
        strategies.append({
            'name': 'Trump DNA (Momentum Trading)',
            'strategy': momentum_strategy,
            'instruments': ['XAU_USD', 'EUR_USD', 'GBP_USD', 'USD_JPY'],
            'type': 'momentum'
        })
    if args.include in ("all", "gold"):
        strategies.append({
            'name': 'Gold Scalping (Optimized)',
            'strategy': gold_strategy,
            'instruments': ['XAU_USD'],
            'type': 'scalping'
        })

    if not strategies:
        logger.error("❌ No strategies selected for backtest. Use --include to specify valid options.")
        sys.exit(1)
    
    # Run backtests
    results = run_backtest_for_strategies(strategies, days=args.days)
    
    # Print summary
    logger.info("\n" + "="*70)
    logger.info(f"📊 FINAL SUMMARY - {args.days} DAY BACKTEST (FIXED)")
    logger.info("="*70)
    logger.info(f"{'Strategy':<40} {'Trades':<10} {'Win Rate':<15} {'Profit Factor':<15} {'Status':<15}")
    logger.info("-"*70)
    
    for result in results:
        profit_factor = f"{result.get('profit_factor', 0):.2f}"
        logger.info(f"{result['strategy']:<40} {result['trades']:<10} {result['win_rate']:.2f}%{'':<10} {profit_factor:<15} {result['status']:<15}")
    
    logger.info("="*70)
    
    # Count failures
    failures = [r for r in results if 'FAILURE' in r['status'] or r['win_rate'] < 50]
    passes = [r for r in results if 'FAILURE' not in r['status'] and r['win_rate'] >= 50]
    
    logger.info(f"\n✅ PASSED: {len(passes)}")
    logger.info(f"❌ FAILED: {len(failures)}")
    
    if failures:
        logger.info("\n⚠️ FAILED STRATEGIES:")
        for failure in failures:
            logger.info(f"  - {failure['strategy']}: {failure['win_rate']:.2f}% win rate")
    
    # Save detailed results
    output_file = 'fixed_backtest_results.json'
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"\n💾 Detailed results saved to {output_file}")

if __name__ == "__main__":
    main()




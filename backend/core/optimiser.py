# backend/core/optimizer.py
import json
import pandas as pd
import asyncio
from .database import all_engine, sql_text

class OptimizerBot:
    def __init__(self, params_path='strategy_params.json'):
        self.params_path = params_path
        self.justifications = []

    async def get_historical_data(self, days=60):
        try:
            def db_call():
                with all_engine.connect() as conn:
                    if all_engine.dialect.name == 'sqlite':
                        query = sql_text(f"SELECT * FROM trades WHERE timestamp >= date('now', '-{days} days')")
                    else:
                        # PostgreSQL syntax
                        query = sql_text(f"SELECT * FROM trades WHERE timestamp >= CURRENT_DATE - INTERVAL '{days} days'")
                    
                    df = pd.read_sql_query(query, conn)
                    print(f"Optimizer: Loaded {len(df)} trades from the last {days} days.")
                    return df
            return await asyncio.to_thread(db_call)
        except Exception as e:
            print(f"Optimizer: Could not read from database. Error: {e}")
            return pd.DataFrame()

    def analyze_performance(self, df):
        if df.empty: return None
        results = df.groupby('trigger_reason').agg(
            total_trades=('pnl', 'count'),
            total_pnl=('pnl', 'sum'),
            winning_trades=('pnl', lambda x: (x > 0).sum())
        ).reset_index()
        results['win_rate'] = (results['winning_trades'] / results['total_trades']) * 100
        print("\n--- Long-Term Performance Analysis (Last 60 Days) ---")
        print(results.round(2))
        return results

    async def find_optimal_parameters(self):
        df = await self.get_historical_data()
        if df.empty: return None, ["No historical data found. Cannot optimize."]
        
        analysis = self.analyze_performance(df)
        if analysis is None: return None, ["Analysis of historical data failed."]
        
        self.justifications.append("Optimization Report (Last 60 Days):")
        try:
            with open(self.params_path, 'r') as f: new_params = json.load(f)
        except FileNotFoundError:
            return None, ["Strategy parameters file not found."]

        # --- Optimization Logic ---
        rsi_stats = analysis[analysis['trigger_reason'].str.contains('RSI')]
        if not rsi_stats.empty and (rsi_win_rate := rsi_stats['win_rate'].mean()) < 50:
            old_val = new_params['rsi_angle_threshold']
            new_params['rsi_angle_threshold'] = round(old_val * 1.1, 2)
            self.justifications.append(f"- RSI win rate is {rsi_win_rate:.1f}% (<50%). Tightening angle from {old_val} to {new_params['rsi_angle_threshold']}.")

        ma_stats = analysis[analysis['trigger_reason'].str.contains('Anticipate')]
        if not ma_stats.empty and (ma_win_rate := ma_stats['win_rate'].mean()) < 50:
            old_val = new_params['ma_gap_threshold_pct']
            new_params['ma_gap_threshold_pct'] = round(old_val * 0.9, 4)
            self.justifications.append(f"- MA Crossover win rate is {ma_win_rate:.1f}% (<50%). Reducing gap from {old_val} to {new_params['ma_gap_threshold_pct']}.")

        if analysis['total_trades'].sum() > 0 and (analysis['total_pnl'].sum() / analysis['total_trades'].sum()) < 0:
            old_val = new_params['min_atr_value']
            new_params['min_atr_value'] = round(old_val * 1.05, 2)
            self.justifications.append(f"- Overall P&L is negative. Increasing min ATR from {old_val} to {new_params['min_atr_value']} to avoid chop.")

        if len(self.justifications) == 1: self.justifications.append("- No parameters needed adjustment. Performance is stable.")
        return new_params, self.justifications

    def update_strategy_file(self, new_params):
        if new_params:
            with open(self.params_path, 'w') as f: json.dump(new_params, f, indent=4)
            print(f"Optimizer: Successfully updated '{self.params_path}'.")

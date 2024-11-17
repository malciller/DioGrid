from tabulate import tabulate

def run_trade_simulation(trades, usd_amount, scenario_name, sell_key):
    """
    Run a trading simulation for a specific scenario
    
    Args:
        trades (list): List of trade dictionaries containing buy/sell prices
        usd_amount (float): USD amount to trade with
        scenario_name (str): Name of the scenario for printing
        sell_key (str): Dictionary key to use for sell price ('sell1' or 'sell2')
    
    Returns:
        tuple: (total_profit, total_btc)
    """
    total_profit = 0
    total_btc = 0
    trade_rows = []
    
    for trade in trades:
        buy_price = trade["buy"]
        sell_price = trade[sell_key]
        
        asset_amount = usd_amount / buy_price
        total_btc += asset_amount
        
        profit = (asset_amount * sell_price) - usd_amount
        total_profit += profit
        
        return_percent = ((sell_price - buy_price) / buy_price) * 100
        
        trade_rows.append([
            f"${buy_price:,}",
            f"${sell_price:,}",
            f"{return_percent:,.1f}%",
            f"${profit:,.2f}",
            f"${total_profit:,.2f}",
            f"{total_btc:.8f}"
        ])
    
    print(f"\n{scenario_name} (${usd_amount:,.2f} per trade):")
    print(tabulate(
        trade_rows,
        headers=["Buy Price", "Sell Price", "Return", "Profit", "Total Profit", "Total BTC"],
        tablefmt="grid"
    ))
    
    return total_profit, total_btc

def print_summary(scenarios_results):
    """
    Print summary of all scenarios
    
    Args:
        scenarios_results (dict): Dictionary of scenario results
    """
    summary_rows = [
        [name, f"${profit:,.2f}", f"{btc:.8f}"]
        for name, (profit, btc) in scenarios_results.items()
    ]
    
    print("\nFinal Summary:")
    print(tabulate(
        summary_rows,
        headers=["Scenario", "Total Profit", "Total BTC"],
        tablefmt="grid"
    ))

# Global trade settings
BUY_PRICES = [75000, 70000, 65000, 60000, 55000, 50000]
SELL_SCENARIOS = {
    "conservative": [85000, 80000, 75000, 70000, 65000, 60000],
    "moderate": [95000, 90000, 85000, 80000, 75000, 70000],
    "aggressive": [130000, 120000, 110000, 100000, 90000, 80000]
}

def generate_trades():
    """Generate trade dictionaries from global settings"""
    trades = []
    for i, buy_price in enumerate(BUY_PRICES):
        trade = {"buy": buy_price}
        for scenario_name, sell_prices in SELL_SCENARIOS.items():
            trade[scenario_name] = sell_prices[i]
        trades.append(trade)
    return trades

PARAMETERS = {
    "usd_amount": 125,
    "trades": generate_trades(),
    "scenarios": [
        {
            "name": "Conservative Strategy",
            "sell_key": "conservative",
            "usd_amount": 125
        },
        {
            "name": "Aggressive Strategy",
            "sell_key": "aggressive",
            "usd_amount": 250
        },
        {
            "name": "Moderate Strategy",
            "sell_key": "moderate",
            "usd_amount": 175
        }
    ]
}

def main():
    # Run all scenarios
    results = {}
    for scenario in PARAMETERS["scenarios"]:
        results[scenario["name"]] = run_trade_simulation(
            PARAMETERS["trades"],
            scenario["usd_amount"],
            scenario["name"],
            scenario["sell_key"]
        )
    
    # Print final summary
    print_summary(results)

if __name__ == "__main__":
    main()

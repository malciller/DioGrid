from kraken_utils.api import KrakenAuthBuilder, API_KEY, API_SECRET
from kraken_utils.fetcher import CryptoPriceFetcher
from kraken_utils.market import MarketAnalyzer, PositionTracker
from kraken_utils.orders import OrderManager
import logging
import time
import argparse

def main():
    print('Started Main')
    parser = argparse.ArgumentParser(description="Multi-Asset Grid Trading Bot for Kraken")
    parser.add_argument("--grid-percentage", type=float, required=True, help="Grid interval as percentage of current price")
    parser.add_argument("--lower-bound-percentage", type=float, required=True, help="Lower bound as percentage of current price")
    parser.add_argument("--max-open-orders", type=int, required=True, help="Maximum number of open buy orders per asset")
    parser.add_argument("--time-range", type=int, required=True, help="Time range in seconds for checking open orders")
    parser.add_argument("--pairs", nargs='+', required=True, help="List of cryptocurrency pairs to trade (e.g., BTCUSD ETHUSD SOLUSD)")
    args = parser.parse_args()

    auth_builder = KrakenAuthBuilder(api_key=API_KEY, api_secret=API_SECRET)
    price_fetcher = CryptoPriceFetcher(tst)
    market_analyzer = MarketAnalyzer(price_fetcher)
    position_tracker = PositionTracker()
    order_manager = OrderManager(auth_builder, position_tracker, market_analyzer, price_fetcher, args.grid_percentage, config_file = 'config.json')

    order_manager.load_open_orders()

    while True:
        order_manager.check_and_update_open_orders(args.pairs)

        for pair in args.pairs:
            best_price_info = price_fetcher.get_best_price(pair)
            if best_price_info:
                current_price = best_price_info['midpoint_price']
                order_manager.execute_grid_strategy(
                    current_price=current_price,
                    grid_percentage=args.grid_percentage,
                    lower_bound_percentage=args.lower_bound_percentage,
                    pair=pair,
                    max_open_orders=args.max_open_orders
                )
            else:
                logging.error(f"Failed to fetch the current price for {pair}")
        
        time.sleep(args.time_range)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error(f"An error occurred: {e}", exc_info=True)
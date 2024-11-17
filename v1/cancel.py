#!/usr/bin/env python3

'''
    Handles canceling buy orders that fall out of the defined range

'''


import os
import ccxt
import logging
import argparse
import time
from dotenv import load_dotenv

# Load API keys from .env file
load_dotenv()

API_KEY = os.getenv("KRAKEN_API_KEY")
API_SECRET = os.getenv("KRAKEN_API_SECRET")

# Set up logging
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

# Kraken asset pairs mapping (optional if needed)
ASSET_PAIRS = {
    "XXBTZUSD": "BTC/USD",
    "XETHZUSD": "ETH/USD",
    "XXRPZUSD": "XRP/USD",
    "SOLUSD": "SOL/USD",
    "ADAUSD": "ADA/USD",
    "AVAXUSD": "AVAX/USD",
    "TRXUSD": "TRX/USD",
    "XMRUSD": "XMR/USD",
    "FETUSD": "FET/USD"
}


# Initialize Kraken exchange
kraken = ccxt.kraken({
    'apiKey': API_KEY,
    'secret': API_SECRET,
})

# Function to get current price of an asset pair
def get_current_price(pair):
    ticker = kraken.fetch_ticker(pair)
    return ticker['last']

# Function to check if order is within the threshold
def is_within_threshold(order_price, current_price, threshold_percent):
    price_diff = abs(order_price - current_price)
    return price_diff <= (current_price * (threshold_percent / 100))

# Function to cancel orders
def cancel_order(order_id, pair):
    try:
        kraken.cancel_order(order_id)
        logging.info(f"Cancelled order {order_id} for {pair}")
    except ccxt.BaseError as e:
        logging.error(f"Error cancelling order {order_id}: {str(e)}")

# Function to process orders and apply the new check for multiple open buy orders per asset pair
def process_orders(threshold_percent):
    logging.info("Fetching open orders...")
    try:
        open_orders = kraken.fetch_open_orders()
    except ccxt.BaseError as e:
        logging.error(f"Error fetching open orders: {str(e)}")
        return

    # Dictionary to track buy orders per asset pair
    buy_orders_by_pair = {}

    # First, collect all buy orders per asset pair
    for order in open_orders:
        pair = order['symbol']
        side = order['side']
        if side == 'buy':
            if pair not in buy_orders_by_pair:
                buy_orders_by_pair[pair] = []
            buy_orders_by_pair[pair].append(order)

    # Now process the buy orders for each pair
    for pair, orders in buy_orders_by_pair.items():
        # If more than 1 buy order is open for this pair, cancel all buy orders for the pair
        if len(orders) > 1:
            logging.info(f"Found more than 1 open buy order for {pair}, cancelling all buy orders for this pair.")
            for order in orders:
                cancel_order(order['id'], pair)
        else:
            # If only 1 buy order, check if it's within the threshold
            order = orders[0]
            order_id = order['id']
            order_price = float(order['price'])
            try:
                current_price = get_current_price(pair)
                logging.info(f"Order {order_id} for {pair}: order price={order_price}, current price={current_price}")

                if not is_within_threshold(order_price, current_price, threshold_percent):
                    logging.info(f"Order {order_id} for {pair} is outside the {threshold_percent}% threshold, cancelling...")
                    cancel_order(order_id, pair)
                else:
                    logging.info(f"Order {order_id} for {pair} is within the threshold.")
            except ccxt.BaseError as e:
                logging.error(f"Error fetching price for {pair}: {str(e)}")

def main():
    # Set up argument parser to allow threshold configuration
    parser = argparse.ArgumentParser(description="Cancel Kraken orders outside of a price threshold.")
    parser.add_argument('--threshold', type=float, default=1.5, help="Percentage threshold to cancel orders outside the current price.")
    args = parser.parse_args()

    threshold_percent = args.threshold  # Use the threshold passed in as an argument
    logging.info(f"Starting py_cancel.py with a {threshold_percent}% threshold.")

    process_orders(threshold_percent)

if __name__ == "__main__":
    main()

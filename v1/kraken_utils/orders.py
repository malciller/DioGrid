import json
import os
import logging
import time
from datetime import datetime
import requests
from .api import KrakenAuthBuilder, APICounter
from .fetcher import CryptoPriceFetcher
from .market import PositionTracker, MarketAnalyzer

logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class OrderManager:
    def __init__(self, auth_builder: KrakenAuthBuilder, position_tracker: PositionTracker, market_analyzer: MarketAnalyzer, price_fetcher: CryptoPriceFetcher, grid_percentage: float, config_file):
        self.auth_builder = auth_builder
        self.base_url = "https://api.kraken.com"
        self.position_tracker = position_tracker
        self.market_analyzer = market_analyzer
        self.price_fetcher = price_fetcher
        self.min_grid_distance_percent = grid_percentage
        self.open_buy_orders = {}
        self.open_sell_orders = {}
        self.log_file = 'logs/bot_orders.json'
        self.api_counter = APICounter(max_value=20, decay_rate_per_second=1)
        with open(config_file, 'r') as f:
            config = json.load(f)
        self.price_precision = config.get('price_precisions', {})
        self.volume_precision = config.get('volume_precisions', {})
        self.min_trade_size = config.get('min_trade_sizes', {})
        self.kraken_pairs = config.get('kraken_pairs', {})

        logging.info("OrderManager initialized")

    def check_and_update_open_orders(self, pairs):
        for pair in pairs:
            if pair in self.open_buy_orders:
                updated_buy_orders = []
                for order in self.open_buy_orders[pair]:
                    if self.api_counter.can_make_api_call(api_call_weight=1):
                        self.api_counter.update_counter(api_call_weight=1)
                        if self.check_order_status(order['order_id']):
                            updated_buy_orders.append(order)
                        else:
                            logging.info(f"Buy order {order['order_id']} for {pair} is no longer open, removing from tracking.")
                    else:
                        logging.info(f"Rate limit reached, skipping check for buy order {order['order_id']} for {pair}")
                        updated_buy_orders.append(order)
                self.open_buy_orders[pair] = updated_buy_orders
            if pair in self.open_sell_orders:
                updated_sell_orders = []
                for order in self.open_sell_orders[pair]:
                    if self.api_counter.can_make_api_call(api_call_weight=1):
                        self.api_counter.update_counter(api_call_weight=1)
                        if self.check_order_status(order['order_id']):
                            updated_sell_orders.append(order)
                        else:
                            logging.info(f"Sell order {order['order_id']} for {pair} is no longer open, removing from tracking.")
                    else:
                        logging.info(f"Rate limit reached, skipping check for sell order {order['order_id']} for {pair}")
                        updated_sell_orders.append(order)
                self.open_sell_orders[pair] = updated_sell_orders
        
            # Add a delay between order checks to avoid hitting rate limits
            time.sleep(1)  # Add a delay of 1 second or more, adjust based on your rate limit
        self.update_log_file()
        logging.info("Open orders checked and updated for all pairs.")



    def load_open_orders(self):
        if not os.path.exists(self.log_file):
            logging.info(f"No log file found at {self.log_file}, no orders to load.")
            return

        try:
            with open(self.log_file, 'r') as file:
                orders = json.load(file)
        except json.JSONDecodeError:
            logging.error(f"Failed to parse the log file {self.log_file}")
            return

        for order in orders:
            pair = order.get("pair")
            order_id = order.get("order_id")
            side = order.get("side")
            if pair and order_id and self.check_order_status(order_id):
                if pair not in self.open_buy_orders:
                    self.open_buy_orders[pair] = []
                if pair not in self.open_sell_orders:
                    self.open_sell_orders[pair] = []
                
                if side == "buy":
                    self.open_buy_orders[pair].append(order)
                elif side == "sell":
                    self.open_sell_orders[pair].append(order)
            else:
                logging.info(f"Order {order_id} for {pair} is not open, removing from tracking.")

        self.update_log_file()

    def update_log_file(self):
        all_open_orders = []
        for orders in self.open_buy_orders.values():
            all_open_orders.extend(orders)
        for orders in self.open_sell_orders.values():
            all_open_orders.extend(orders)
        
        with open(self.log_file, 'w') as file:
            json.dump(all_open_orders, file, indent=4)

    def check_order_status(self, order_id):
        endpoint = "/0/private/QueryOrders"
        data = {
            "nonce": str(int(1000 * time.time())),
            "txid": order_id
        }
        
        headers = self.auth_builder.get_headers(endpoint, data)
        url = self.base_url + endpoint

        try:
            response = requests.post(url, headers=headers, data=data, timeout=10)
            response.raise_for_status()
            result = response.json()

            if 'error' in result and result['error']:
                logging.error(f"Error checking order status for {order_id}: {result['error']}")
                return False

            order_info = result['result'].get(order_id)
            if not order_info:
                logging.error(f"No order information found for {order_id}")
                return False

            status = order_info.get('status')
            logging.info(f"Order {order_id} status: {status}")
            return status == 'open'
        except requests.RequestException as e:
            logging.error(f"Error querying order status for {order_id}: {e}")
            return False

    def place_order(self, side: str, price: float, quantity: float, pair: str) -> dict:
        # Map the user-friendly pair (e.g., ETH/USD) to the Kraken pair (e.g., XETHZUSD)
        kraken_pair = self.kraken_pairs.get(pair, pair)  # Get the Kraken pair name from the config

        min_trade_size = self._get_min_trade_size(pair)
        if quantity < min_trade_size:
            logging.error(f"Order size {quantity} is below the minimum for {pair} ({min_trade_size})")
            return {}

        price = self._round_price(pair, price)
        quantity = self._round_volume(pair, quantity)

        if side == "sell":
            current_position = self.position_tracker.get_current_position(pair)
            if current_position['balance'] < quantity:
                logging.info(f"Skipping sell order for {pair}: insufficient balance (needed: {quantity}, available: {current_position['balance']})")
                return {}

        self.api_counter.wait_until_ready(0)  # Trading endpoints have a weight of 0

        order_data = {
            "nonce": str(int(1000 * time.time())),
            "ordertype": "limit",
            "type": side,
            "pair": kraken_pair,  # Use the mapped Kraken pair name here
            "price": str(price),
            "volume": str(quantity),
        }
        endpoint = "/0/private/AddOrder"
        headers = self.auth_builder.get_headers(endpoint, order_data)
        url = self.base_url + endpoint

        try:
            response = requests.post(url, headers=headers, data=order_data, timeout=15)
            response.raise_for_status()
            result = response.json()

            txid = result.get('result', {}).get('txid', [])
            if txid:
                txid = txid[0]  # Kraken returns the txid as a list
                logging.info(f"{side.capitalize()} Order Placed Successfully for {pair}: {result}")
                order_details = {
                    "side": side,
                    "pair": pair,
                    "price": price,
                    "volume": quantity,
                    "timestamp": datetime.now().isoformat(),
                    "order_id": txid
                }
                self.log_order(order_details)
                return order_details
            else:
                logging.error(f"Failed to place {side} order for {pair}. No txid found.")
                return {}
        except requests.RequestException as e:
            logging.error(f"Error placing {side} order for {pair}: {e}")
            return {}


    def log_order(self, order_details):
        if 'order_id' not in order_details:
            logging.error("Order details are missing 'order_id', cannot log order.")
            return

        if not self.log_file:
            logging.error("Log file path is not set. Cannot log order.")
            return

        try:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        except OSError as e:
            logging.error(f"Failed to create directory for log file: {e}")
            return

        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r') as file:
                    try:
                        data = json.load(file)
                    except json.JSONDecodeError:
                        logging.warning("Existing log file is not valid JSON. Starting with empty list.")
                        data = []
            else:
                data = []

            data.append(order_details)

            with open(self.log_file, 'w') as file:
                json.dump(data, file, indent=4)
            logging.info(f"Order logged successfully: {order_details}")
        except Exception as e:
            logging.error(f"Failed to log order: {e}")

    def execute_grid_strategy(self, current_price: float, grid_percentage: float, lower_bound_percentage: float, pair: str, max_open_orders: int):
        logging.info(f"Executing Grid Strategy for {pair}: current_price={current_price}")
        
        # Calculate the initial interval and grid interval
        initial_interval = current_price * (grid_percentage / 2)  # Half of grid_percentage for first orders
        grid_interval = current_price * grid_percentage  # Full grid_percentage for subsequent orders
        
        lower_bound = current_price * (1 - lower_bound_percentage)
        upper_bound = current_price * (1 + lower_bound_percentage)
        
        logging.info(f"Bounds for {pair}: lower={lower_bound}, upper={upper_bound}")
        logging.info(f"Initial interval: {initial_interval:.2f}")
        logging.info(f"Grid interval: {grid_interval:.2f}")
        
        if pair not in self.open_buy_orders:
            self.open_buy_orders[pair] = []
        if pair not in self.open_sell_orders:
            self.open_sell_orders[pair] = []
        
        num_open_buy_orders = len(self.open_buy_orders[pair])
        num_open_sell_orders = len(self.open_sell_orders[pair])

        max_sell_orders = int(max_open_orders * 25)

        logging.info(f"Number of open buy orders for {pair}: {num_open_buy_orders}/{max_open_orders}")
        logging.info(f"Number of open sell orders for {pair}: {num_open_sell_orders}/{max_sell_orders}")
        
        self.api_counter.wait_until_ready(api_call_weight=0)
        
        # Place new buy orders
        num_new_buy_orders = max_open_orders - num_open_buy_orders
        if num_new_buy_orders > 0:
            buy_price = current_price - initial_interval
            for i in range(num_new_buy_orders):
                if buy_price < lower_bound:
                    logging.info(f"Skipping buy order: buy price {buy_price} is below the lower bound {lower_bound}")
                    break
                logging.info(f"Placing buy order for {pair} at {buy_price}")
                self._execute_buy_and_sell_orders(buy_price, current_price, grid_interval, pair, place_buy=True)
                buy_price -= grid_interval  # Use grid_interval for subsequent orders
        
        # Place new sell orders
        num_new_sell_orders = max_sell_orders - num_open_sell_orders
        if num_new_sell_orders > 0:
            sell_price = current_price + initial_interval
            for i in range(num_new_sell_orders):
                if sell_price > upper_bound:
                    logging.info(f"Skipping sell order: sell price {sell_price} is above the upper bound {upper_bound}")
                    break
                logging.info(f"Placing sell order for {pair} at {sell_price}")
                self._execute_buy_and_sell_orders(sell_price, current_price, grid_interval, pair, place_buy=False)
                sell_price += grid_interval  # Use grid_interval for subsequent orders
        
        self.update_log_file()





    def _execute_buy_and_sell_orders(self, price: float, current_price: float, grid_interval: float, pair: str, place_buy: bool):
        min_trade_size = self._get_min_trade_size(pair)

        if place_buy:
            buy_order = self.place_order("buy", price, min_trade_size, pair)
            if buy_order:
                self.open_buy_orders[pair].append(buy_order)
                self.position_tracker.update_position(pair, "buy", price, min_trade_size)
        else:
            sell_order = self.place_order("sell", price, min_trade_size, pair)
            if sell_order:
                self.open_sell_orders[pair].append(sell_order)
                self.position_tracker.update_position(pair, "sell", price, min_trade_size)

    def _round_price(self, pair: str, price: float) -> float:
        precision = self.price_precision.get(pair, 2) 
        return round(price, precision)

    def _round_volume(self, pair: str, volume: float) -> float:
        precision = self.volume_precision.get(pair, 4)  
        return round(volume, precision)

    def _get_min_trade_size(self, pair: str) -> float:
        return self.min_trade_size.get(pair, 0.0001) 
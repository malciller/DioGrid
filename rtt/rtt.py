import sys
import os
sys.path.append('..')

import sqlite3
import time
from kraken_utils.api import KrakenAuthBuilder, APICounter
from kraken_utils.fetcher import CryptoPriceFetcher
from dotenv import load_dotenv
import requests

load_dotenv()

KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

class TradingBot:
    def __init__(self, asset_pair: str, trade_amount_usd: float):
        self.asset_pair = asset_pair
        self.trade_amount_usd = trade_amount_usd
        self.db_path = "trading_bot.db"
        self.auth_builder = KrakenAuthBuilder(
            api_key=KRAKEN_API_KEY,
            api_secret=KRAKEN_API_SECRET
        )
        self.api_counter = APICounter()
        self.price_fetcher = CryptoPriceFetcher(self.auth_builder)
        
        # Initialize trading state
        self.current_position = None
        self.entry_price = None
        self.target_sell_price = None
        self.current_profit = 0
        self.total_trades = 0
        self.winning_trades = 0
        
        # Add fee constants
        self.maker_fee = 0.0025  
        self.taker_fee = 0.004  
        self.min_profit_margin = 0.03  # $0.03 minimum profit
        
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database with necessary tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create trades table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                order_id TEXT,
                type TEXT,
                price REAL,
                volume REAL,
                cost REAL,
                fee REAL,
                profit_loss REAL
            )
        ''')
        
        # Create orders table to track active orders
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                type TEXT,
                price REAL,
                volume REAL,
                status TEXT
            )
        ''')
        
        conn.commit()
        conn.close()

    def _record_trade(self, order_id: str, type: str, price: float, volume: float, cost: float, fee: float, profit_loss: float = 0):
        """Record trade details to database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO trades (order_id, type, price, volume, cost, fee, profit_loss)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (order_id, type, price, volume, cost, fee, profit_loss))
        conn.commit()
        conn.close()

    def _execute_buy(self, price: float):
        """Execute a buy order and immediately place a limit sell order."""
        try:
            volume = self.trade_amount_usd / price
            
            # Add rate limiting
            self.api_counter.wait_until_ready(1)
            
            # Place market buy order
            buy_data = {
                'nonce': str(int(time.time() * 1000)),
                'pair': self.asset_pair,
                'type': 'buy',
                'ordertype': 'market',
                'volume': f'{volume:.8f}'
            }
            
            uri_path = "/0/private/AddOrder"
            headers = self.auth_builder.get_headers(uri_path, buy_data)
            
            response = requests.post(
                f"https://api.kraken.com{uri_path}",
                headers=headers,
                data=buy_data
            ).json()
            
            if 'error' in response and response['error']:
                print(f"Buy order error: {response['error']}")
                return False
            
            buy_order_id = response['result']['txid'][0]
            
            # Wait for buy order to fill
            filled_buy_order = self._wait_for_order_fill(buy_order_id)
            if not filled_buy_order:
                return False
            
            # Extract buy order details
            buy_price = float(filled_buy_order['price'])
            volume = float(filled_buy_order['vol_exec'])
            buy_cost = float(filled_buy_order['cost'])
            buy_fee = float(filled_buy_order['fee'])
            
            # Calculate target sell price
            target_price = self.calculate_target_sell_price(buy_price)
            
            # Immediately place limit sell order
            self.api_counter.wait_until_ready(1)
            
            sell_data = {
                'nonce': str(int(time.time() * 1000)),
                'pair': self.asset_pair,
                'type': 'sell',
                'ordertype': 'limit',
                'price': f'{target_price:.1f}',  # Kraken requires specific price precision
                'volume': f'{volume:.8f}'
            }
            
            headers = self.auth_builder.get_headers(uri_path, sell_data)
            
            response = requests.post(
                f"https://api.kraken.com{uri_path}",
                headers=headers,
                data=sell_data
            ).json()
            
            if 'error' in response and response['error']:
                print(f"Sell order error: {response['error']}")
                return False
                
            sell_order_id = response['result']['txid'][0]
            
            # Update trading state
            self.current_position = "LONG"
            self.entry_price = buy_price
            self.target_sell_price = target_price
            
            # Record the buy trade
            self._record_trade(buy_order_id, "BUY", buy_price, volume, buy_cost, buy_fee)
            
            print(f"Buy executed at ${buy_price:,.2f}")
            print(f"Limit sell order placed at ${target_price:,.2f}")
            return True
            
        except Exception as e:
            print(f"Order execution error: {str(e)}")
            return False

    def _wait_for_order_fill(self, order_id: str, timeout: int = 5) -> dict:
        """Wait for an order to fill and return the filled order details."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            # Add rate limiting
            self.api_counter.wait_until_ready(1)
            
            data = {
                'nonce': str(int(time.time() * 1000)),
                'txid': order_id,
                'trades': True
            }
            
            # Get headers and make the request ourselves
            uri_path = "/0/private/QueryOrders"
            headers = self.auth_builder.get_headers(uri_path, data)
            
            response = requests.post(
                f"https://api.kraken.com{uri_path}",
                headers=headers,
                data=data
            ).json()
            
            if 'error' in response and response['error']:
                print(f"Error querying order: {response['error']}")
                return None
            
            order = response['result'][order_id]
            if order['status'] == 'closed':
                return order
            
            time.sleep(0.5)  # Check twice per second instead of once
        
        # If we hit timeout, we should investigate why a market order didn't fill
        print(f"Market order {order_id} failed to fill in {timeout} seconds - something is wrong!")
        return None 

    def run(self):
        """Main bot loop."""
        while True:
            try:
                # Fetch current price just to check if we should buy
                price_data = self.price_fetcher.get_best_price(self.asset_pair)
                
                if not price_data:
                    print("Error fetching price data: No data returned")
                    break

                current_price = price_data['midpoint_price']
                
                # Only look for buy opportunities since sells are limit orders
                if self.current_position is None:
                    if self._should_buy(current_price):
                        self._execute_buy(current_price)
                
                time.sleep(5)  # Can use longer sleep since we're not actively monitoring prices
                
            except KeyboardInterrupt:
                print("Trading bot stopped by user")
                break
            except Exception as e:
                print(f"Error occurred: {str(e)}")
                break

    def calculate_target_sell_price(self, buy_price: float) -> float:
        """Calculate target sell price to cover fees and minimum profit."""
        # Calculate fees for both transactions
        buy_fee = buy_price * self.taker_fee  # Market buy uses taker fee
        future_sell_fee = buy_price * self.maker_fee  # Limit sell uses maker fee
        
        # Calculate required price increase to cover fees and profit margin
        total_cost = buy_fee + future_sell_fee + self.min_profit_margin
        
        # Return price that covers fees and minimum profit
        return buy_price + total_cost

    def _should_buy(self, current_price: float) -> bool:
        """Return True if we don't have an open position."""
        return self.current_position is None

    def _should_sell(self, current_price: float) -> bool:
        """Check if current price meets or exceeds our target sell price."""
        if not self.target_sell_price or not self.current_position:
            return False
        return current_price >= self.target_sell_price

if __name__ == "__main__":
    # Initialize bot with BTC/USD pair and $100 trade amount
    bot = TradingBot(
        asset_pair="BTCUSD",  # BTC/USD pair
        trade_amount_usd=100.0  # Trade with $100
    )
    
    # Start the bot
    bot.run() 
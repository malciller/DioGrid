import json
import websockets
import asyncio
import os
import time
import hashlib
import hmac
import base64
import urllib.parse
import requests
from dotenv import load_dotenv
import traceback

class KrakenWebsocket:
    """Base class for Kraken WebSocket connections and authentication"""
    
    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv('KRAKEN_API_KEY')
        self.api_secret = os.getenv('KRAKEN_API_SECRET')
        if not self.api_key or not self.api_secret:
            raise ValueError("API Key and Secret must be set in the environment variables.")
        self.rest_url = "https://api.kraken.com"
        self.ws_url_v2 = "wss://ws-auth.kraken.com/v2"
        self.ws_pub_url = "wss://ws.kraken.com/v2"
        self.ws_url = "wss://ws.kraken.com"
        self.auth_url = "https://api.kraken.com"
        self.auth_token = None
        self.auth_expiry = None

    def get_auth_token(self):
        """Generate authentication token for Kraken websocket"""
        path = "/0/private/GetWebSocketsToken"
        url = self.rest_url + path
        nonce = str(int(time.time() * 1000))
        
        post_data = {"nonce": nonce}
        post_data_encoded = urllib.parse.urlencode(post_data)
        
        sha256_hash = hashlib.sha256((nonce + post_data_encoded).encode('utf-8')).digest()
        hmac_key = base64.b64decode(self.api_secret)
        hmac_message = path.encode('utf-8') + sha256_hash
        signature = hmac.new(hmac_key, hmac_message, hashlib.sha512)
        
        headers = {
            "API-Key": self.api_key,
            "API-Sign": base64.b64encode(signature.digest()).decode()
        }
        
        try:
            response = requests.post(url, headers=headers, data=post_data)
            response.raise_for_status()
            
            result = response.json()
            if 'error' in result and result['error']:
                print(f"Kraken API Error: {result['error']}")
                return None
                
            return result.get("result", {}).get("token")
            
        except Exception as e:
            print(f"Failed to get WebSocket token: {str(e)}")
            return None

    async def create_connection(self, url):
        """Create and return a websocket connection"""
        try:
            return await websockets.connect(url)
        except Exception as e:
            print(f"Failed to create connection to {url}: {str(e)}")
            return None

    async def get_ticker_price(self, symbol: str) -> float:
        """Get current market price for a symbol"""
        try:
            async with await self.create_connection(self.ws_pub_url) as websocket:
                subscribe_msg = {
                    "method": "subscribe",
                    "params": {
                        "channel": "ticker",
                        "symbol": [symbol]
                    }
                }
                await websocket.send(json.dumps(subscribe_msg))
                
                while True:
                    message = await websocket.recv()
                    msg_data = json.loads(message)
                    
                    if msg_data.get("channel") == "ticker" and msg_data.get("type") in ["snapshot", "update"]:
                        ticker_data = msg_data.get("data", [{}])[0]
                        if ticker_data.get("symbol") == symbol:
                            return float(ticker_data.get("last", 0))
                            
        except Exception as e:
            print(f"Failed to get ticker price: {str(e)}")
            return None

    async def create_websocket_connection(self):
        """Create a new websocket connection for order modifications"""
        try:
            websocket = await websockets.connect(self.ws_url_v2)
            token = self.get_auth_token()
            if not token:
                raise ValueError("Failed to get authentication token")

            # Initial authentication with executions channel
            auth_message = {
                "method": "subscribe",
                "params": {
                    "channel": "executions",
                    "token": token,
                    "snap_orders": True
                }
            }
            
            await websocket.send(json.dumps(auth_message))
            response = await websocket.recv()
            response_data = json.loads(response)
            
            if response_data.get("error"):
                raise ValueError(f"Authentication error: {response_data['error']}")
            
            print("Successfully created and authenticated new websocket connection")
            return websocket
            
        except Exception as e:
            print(f"Error creating websocket connection: {e}")
            raise

class BotConfiguration:
    """Class for handling grid trading bot configuration"""
    
    def __init__(self, trading_pairs: dict, grid_interval: float):
        """
        Initialize bot configuration
        
        Args:
            trading_pairs (dict): Dictionary of trading pairs and their quantities
                                 e.g., {'XBT/USD': 0.001, 'ETH/USD': 0.01}
            grid_interval (float): Percentage interval between grid levels (e.g., 0.01 for 1%)
        """
        self.trading_pairs = {pair.upper(): float(qty) for pair, qty in trading_pairs.items()}
        self.grid_interval = float(grid_interval)
        self.validate_config()
    
    def validate_config(self):
        """Validate the configuration parameters"""
        if not self.trading_pairs:
            raise ValueError("At least one trading pair must be specified")
            
        for symbol, qty in self.trading_pairs.items():
            if not symbol or '/' not in symbol:
                raise ValueError(f"Invalid symbol format for {symbol}. Must be in format 'BASE/QUOTE' (e.g., 'XBT/USD')")
            
            if qty <= 0:
                raise ValueError(f"Order quantity for {symbol} must be greater than 0")
        
        if self.grid_interval <= 0:
            raise ValueError("Grid interval must be greater than 0")
        
        if self.grid_interval >= 100:
            raise ValueError("Grid interval must be less than 100%")
    
    def calculate_grid_prices(self, current_price: float, num_grids: int = 5):
        """
        Calculate grid prices based on current price and interval
        
        Args:
            current_price (float): Current market price
            num_grids (int): Number of grid levels above and below current price
        
        Returns:
            tuple: (buy_prices, sell_prices) Lists of prices for buy and sell orders
        """
        interval_multiplier = 1 + (self.grid_interval / 100)
        
        buy_prices = []
        sell_prices = []
        
        # Calculate grid levels below current price (buy orders)
        price = current_price
        for _ in range(num_grids):
            price = price / interval_multiplier
            buy_prices.append(round(price, 1))
        
        # Calculate grid levels above current price (sell orders)
        price = current_price
        for _ in range(num_grids):
            price = price * interval_multiplier
            sell_prices.append(round(price, 1))
        
        return sorted(buy_prices), sorted(sell_prices)
    
    def __str__(self):
        """String representation of the configuration"""
        output = ["Bot Configuration:"]
        for symbol, qty in self.trading_pairs.items():
            output.append(f"  {symbol}: {qty}")
        output.append(f"  Grid Interval: {self.grid_interval}%")
        return "\n".join(output)

class MonitorOpenOrders:
    """Class for monitoring open orders via WebSocket"""
    
    def __init__(self, kraken_ws, config: BotConfiguration, symbol: str):
        self.kraken_ws = kraken_ws
        self.config = config
        self.symbol = symbol
        self.websocket = None  # Main websocket for order updates
        self.edit_websocket = None  # Separate websocket for order modifications
        self.active_buy_orders = {}  # Track active buy orders
        self.initial_snapshot_processed = False

    async def connect_edit_websocket(self):
        """Create separate websocket connection for order modifications"""
        if not self.edit_websocket:
            try:
                self.edit_websocket = await self.kraken_ws.create_websocket_connection()
                print(f"Created edit websocket connection for {self.symbol}")
            except Exception as e:
                print(f"Failed to create edit websocket for {self.symbol}: {e}")
                raise

    async def grid_interval_exceeded_update(self):
        """Monitor and adjust buy orders that exceed grid interval threshold"""
        await self.connect_edit_websocket()

        while True:
            try:
                current_price = await self.kraken_ws.get_ticker_price(self.symbol)
                if not current_price:
                    print(f"Failed to get current price for {self.symbol}")
                    await asyncio.sleep(5)
                    continue

                # Calculate the optimal grid price
                grid_interval = self.config.grid_interval / 100  # Convert 0.8% to 0.008
                optimal_price = round(current_price * (1 - grid_interval), 1)
                
                print(f"\nChecking grid intervals for {self.symbol}:")
                print(f"Current Price: {current_price}")
                print(f"Optimal Grid Price: {optimal_price}")
                print(f"Active Buy Orders: {self.active_buy_orders}")

                # Create a copy of active_buy_orders to avoid modification during iteration
                orders_to_check = self.active_buy_orders.copy()
                
                for order_id, execution_price in orders_to_check.items():
                    execution_price = float(execution_price)
                    print(f"Checking order {order_id} at price {execution_price}")

                    # Calculate percentage difference from current price
                    price_diff_pct = ((current_price - execution_price) / current_price) * 100
                    print(f"Price difference: {price_diff_pct:.2f}%")
                    
                    # If price difference deviates from target 0.8% by more than 0.02%
                    if abs(price_diff_pct - 0.8) > 0.02:  # Tightened threshold
                        print(f"Order {order_id} needs adjustment - Current diff: {price_diff_pct:.2f}%, Target: 0.8%")
                        print(f"Current execution price: {execution_price}")
                        print(f"New grid price: {optimal_price}")
                        
                        # Only modify if the new price is meaningfully different
                        if abs(optimal_price - execution_price) > 0.1:
                            try:
                                # Use the amend_order endpoint
                                modify_message = {
                                    "method": "amend_order",
                                    "params": {
                                        "order_id": order_id,
                                        "limit_price": optimal_price,
                                        "token": self.kraken_ws.get_auth_token()
                                    }
                                }
                                
                                print(f"Sending order modification: {json.dumps(modify_message)}")
                                await self.edit_websocket.send(json.dumps(modify_message))
                                
                                # Wait for and process response with timeout
                                response_received = False
                                start_time = time.time()
                                
                                while time.time() - start_time < 10:  # 10 second timeout
                                    try:
                                        response = await asyncio.wait_for(self.edit_websocket.recv(), timeout=5.0)
                                        response_data = json.loads(response)
                                        
                                        # Skip heartbeat messages
                                        if response_data.get("channel") == "heartbeat":
                                            continue
                                            
                                        # Check for successful amendment
                                        if response_data.get("method") == "amend_order":
                                            response_received = True
                                            if response_data.get("success") is True:
                                                print(f"Order {order_id} successfully amended to {optimal_price}")
                                                self.active_buy_orders[order_id] = optimal_price
                                            else:
                                                print(f"Failed to modify order {order_id}: {response_data.get('error')}")
                                            break
                                    except asyncio.TimeoutError:
                                        continue
                                
                                if not response_received:
                                    print(f"Timeout waiting for modification response for order {order_id}")
                                    # Reconnect websocket if needed
                                    await self.connect_edit_websocket()
                                    
                            except Exception as e:
                                print(f"Error modifying order {order_id}: {e}")
                                print(f"Full error details: {traceback.format_exc()}")
                                # Reconnect websocket on error
                                await self.connect_edit_websocket()
                    else:
                        print(f"Order {order_id} price {execution_price} is within target range (0.8% from current price)")

                await asyncio.sleep(5)

            except Exception as e:
                print(f"Error in grid_interval_exceeded_update for {self.symbol}: {e}")
                print(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(5)

    async def monitor(self):
        """Main monitoring loop"""
        # Start the grid_interval_exceeded_update task
        asyncio.create_task(self.grid_interval_exceeded_update())
        
        while True:
            try:
                self.websocket = await self.kraken_ws.create_connection(self.kraken_ws.ws_url_v2)
                await self.subscribe_to_executions(self.websocket)
                self.initial_snapshot_processed = False

                while True:
                    try:
                        message = await self.websocket.recv()
                        #print(f"Received message: {message}")  # Log incoming messages
                        msg_data = json.loads(message)
                        
                        if msg_data.get("channel") == "executions":
                            msg_type = msg_data.get("type")
                            
                            if msg_type in ["snapshot", "update"]:
                                for order in msg_data.get("data", []):
                                    order_id = order.get("order_id")
                                    exec_type = order.get("exec_type")
                                    order_status = order.get("order_status")
                                    
                                    # Handle new orders (from snapshot or new updates)
                                    if msg_type == "snapshot" or (exec_type in ["new", "pending_new"]):
                                        side = order.get("side", "").upper()
                                        order_symbol = order.get("symbol", "").upper()
                                        
                                        # Only process orders for our symbol and buy orders
                                        if order_symbol == self.symbol and side == "BUY":
                                            self.active_buy_orders[order_id] = order.get('limit_price')
                                            print(f"Order {order_id} added to active buy orders.")
                                            self.print_active_buy_orders()
                                    
                                    # Handle cancellations and fills
                                    elif order_id in self.active_buy_orders and (
                                        exec_type == "canceled" or 
                                        order_status == "canceled" or 
                                        order_status == "filled"
                                    ):
                                        del self.active_buy_orders[order_id]
                                        print(f"Order {order_id} was {order_status} and removed from active buy orders.")
                                        self.print_active_buy_orders()
                                        
                                        # Trigger new orders if no active buy orders
                                        if self.initial_snapshot_processed and not self.active_buy_orders:
                                            print("No active buy orders left, triggering new grid orders.")
                                            await self.create_grid_orders()
                                
                                # Mark initial snapshot as processed
                                if msg_type == "snapshot":
                                    self.initial_snapshot_processed = True
                                    # If no buy orders in initial snapshot, create one
                                    if not self.active_buy_orders:
                                        await self.create_grid_orders()

                    except websockets.exceptions.ConnectionClosed as e:
                        print(f"\nConnection closed for {self.symbol}: {e}. Attempting to reconnect...")
                        break

            except Exception as e:
                print(f"Monitor error for {self.symbol}: {e}")
                await asyncio.sleep(5)

    async def create_grid_orders(self):
        """Create new grid orders if none exist"""
        try:
            current_price = await self.kraken_ws.get_ticker_price(self.symbol)
            if not current_price:
                print(f"Failed to get current price for {self.symbol}")
                return

            # Calculate grid prices using 0.8% interval
            grid_interval = self.config.grid_interval / 100  # Convert 0.8% to 0.008
            buy_price = round(current_price * (1 - grid_interval), 1)
            sell_price = round(current_price * (1 + grid_interval), 1)
            
            print(f"\nCreating new grid orders for {self.symbol}:")
            print(f"Current Price: {current_price}")
            print(f"Grid Buy Price: {buy_price} (-{grid_interval*100:.1f}%)")
            print(f"Grid Sell Price: {sell_price} (+{grid_interval*100:.1f}%)")

            # Get order quantity for this symbol from config
            order_qty = self.config.trading_pairs.get(self.symbol)
            if not order_qty:
                print(f"No order quantity configured for {self.symbol}")
                return

            # Create buy order
            buy_order = {
                "method": "add_order",
                "params": {
                    "order_type": "limit",
                    "side": "buy",
                    "order_qty": order_qty,
                    "symbol": self.symbol,
                    "limit_price": buy_price,
                    "time_in_force": "gtc",  # Good till cancelled
                    "post_only": True,  # Ensure we're always a maker
                    "token": self.kraken_ws.get_auth_token()
                }
            }

            # Create sell order
            sell_order = {
                "method": "add_order",
                "params": {
                    "order_type": "limit",
                    "side": "sell",
                    "order_qty": order_qty,
                    "symbol": self.symbol,
                    "limit_price": sell_price,
                    "time_in_force": "gtc",  # Good till cancelled
                    "post_only": True,  # Ensure we're always a maker
                    "token": self.kraken_ws.get_auth_token()
                }
            }

            # Ensure we have a websocket connection for order placement
            if not self.edit_websocket:
                await self.connect_edit_websocket()

            # Place buy order
            print(f"Placing buy order: {buy_order}")
            await self.edit_websocket.send(json.dumps(buy_order))
            buy_response = await self.edit_websocket.recv()
            buy_data = json.loads(buy_response)
            if buy_data.get("success"):
                print(f"Successfully placed buy order: {buy_data.get('result', {}).get('order_id')}")
            else:
                print(f"Failed to place buy order: {buy_data.get('error')}")

            # Place sell order
            print(f"Placing sell order: {sell_order}")
            await self.edit_websocket.send(json.dumps(sell_order))
            sell_response = await self.edit_websocket.recv()
            sell_data = json.loads(sell_response)
            if sell_data.get("success"):
                print(f"Successfully placed sell order: {sell_data.get('result', {}).get('order_id')}")
            else:
                print(f"Failed to place sell order: {sell_data.get('error')}")

        except Exception as e:
            print(f"Error creating grid orders for {self.symbol}: {e}")
            print(f"Full error details: {traceback.format_exc()}")

    def process_order_data(self, order):
        """Process and format order data"""
        if "limit_price" not in order or order.get("symbol") != self.symbol:
            return None
            
        order_info = {
            "order_id": order.get("order_id", "UNKNOWN"),
            "symbol": order.get("symbol", "UNKNOWN"),
            "side": order.get("side", "UNKNOWN").upper(),
            "limit_price": float(order.get("limit_price", 0.0)),
            "status": order.get("order_status", "UNKNOWN")
        }
        
        return order_info

    async def subscribe_to_executions(self, websocket):
        """Subscribe to execution updates for the specified symbol"""
        token = self.kraken_ws.get_auth_token()
        if not token:
            raise ValueError("Failed to get authentication token")

        subscribe_message = {
            "method": "subscribe",
            "params": {
                "channel": "executions",
                "token": token,
                "snap_orders": True,  # Get initial snapshot
                "snap_trades": False,  # Don't need trade history
                "order_status": True   # Get all status transitions
            }
        }
        
        try:
            await websocket.send(json.dumps(subscribe_message))
            response = await websocket.recv()
            response_data = json.loads(response)
            
            if response_data.get("error"):
                raise ValueError(f"Subscription error: {response_data['error']}")
                
            print(f"Successfully subscribed to executions for {self.symbol}")
            
        except Exception as e:
            print(f"Failed to subscribe to executions for {self.symbol}: {e}")
            raise

    async def update_grid_prices(self):
        """Update current price and recalculate grid levels"""
        current_time = time.time()
        
        # Only update if enough time has passed since last update
        if current_time - self.last_grid_update >= self.grid_update_interval:
            new_price = await self.kraken_ws.get_ticker_price(self.symbol)
            if new_price:
                self.current_price = new_price
                self.buy_grid_prices, self.sell_grid_prices = self.config.calculate_grid_prices(new_price)
                self.last_grid_update = current_time
                '''
                print(f"\n=== Updated Grid for {self.symbol} ===")
                print(f"Buy Grid Levels:  {self.buy_grid_prices}")
                print(f"Current Price:    {self.current_price}")
                print(f"Sell Grid Levels: {self.sell_grid_prices}")
                '''
    
    def print_active_buy_orders(self):
        """Print the current active buy orders"""
        print(f"Active Buy Orders for {self.symbol}: {self.active_buy_orders}")

    async def modify_order(self, order_id: str, new_price: float) -> bool:
        """Send order modification request using amend_order endpoint"""
        try:
            # Format according to the WebSocket API v2 spec
            modify_message = {
                "method": "amend_order",  # Changed from editOrder to amend_order
                "params": {
                    "order_id": order_id,
                    "limit_price": float(new_price),  # Must be float
                    "token": self.kraken_ws.get_auth_token()
                }
            }
            
            print(f"Sending order modification: {json.dumps(modify_message)}")
            await self.edit_websocket.send(json.dumps(modify_message))
            
            # Wait for response
            while True:
                response = await self.edit_websocket.recv()
                #print(f"Received response: {response}")
                response_data = json.loads(response)
                
                # Skip heartbeat messages
                if response_data.get("channel") == "heartbeat":
                    continue
                    
                # Check for successful amendment
                if response_data.get("method") == "amend_order":
                    if response_data.get("success") is True:
                        print(f"Order {order_id} successfully amended to {new_price}")
                        return True
                    elif "error" in response_data:
                        print(f"Failed to modify order {order_id}: {response_data['error']}")
                        return False
                    
            print(f"Timeout waiting for modification response for order {order_id}")
            return False
            
        except Exception as e:
            print(f"Error modifying order {order_id}: {e}")
            print(f"Full error details: {traceback.format_exc()}")
            return False

async def main():
    # Example configuration with multiple pairs
    config = BotConfiguration(
        trading_pairs={
            "BTC/USD": 0.0001,
            "SOL/USD": 0.04,
        },
        grid_interval=0.8
    )
    
    kraken_ws = KrakenWebsocket()
    
    # Print overall configuration first
    print("\n=== Grid Trading Bot Configuration ===")
    for symbol, qty in config.trading_pairs.items():
        print(f"Symbol: {symbol:<10} | Order Quantity: {qty}")
    print(f"Grid Interval: {config.grid_interval}%\n")
    
    # Initialize monitors for each trading pair
    monitors = []
    
    # Print grid levels and current prices
    print("=== Grid Levels and Current Prices ===")
    for symbol in config.trading_pairs:
        current_price = await kraken_ws.get_ticker_price(symbol)
        if not current_price:
            print(f"Failed to get current market price for {symbol}. Skipping.")
            continue
            
        buy_prices, sell_prices = config.calculate_grid_prices(current_price)
        
        print(f"\n{symbol}:")
        print(f"Buy Grid Levels:  {buy_prices}")
        print(f"Current Price:    {current_price}")
        print(f"Sell Grid Levels: {sell_prices}")
        
        # Initialize monitor with grid information and symbol
        monitor = MonitorOpenOrders(kraken_ws, config, symbol)
        monitor.current_price = current_price
        monitor.buy_grid_prices = buy_prices
        monitor.sell_grid_prices = sell_prices
        monitors.append(monitor)
    
    print("\n=== Starting Order Monitoring ===")
    # Run all monitors concurrently
    await asyncio.gather(*(monitor.monitor() for monitor in monitors))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")

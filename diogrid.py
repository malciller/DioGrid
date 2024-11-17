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
        self.rate_counters = {}  # Track rate counter per pair
        self.last_decay_time = {}  # Track last decay time per pair
        self.tier_decay_rate = 2.34  # Assuming Intermediate tier (-2.34/second)
        self.tier_threshold = 125    # Assuming Intermediate tier threshold
        self.ticker_ws = None
        self.edit_websocket = None
        self.latest_prices = {}
        self.price_subscribers = set()
        self.ticker_initialized = asyncio.Event()
        self.price_queue = asyncio.Queue()
        self.order_queue = asyncio.Queue()
        self.ws_lock = asyncio.Lock()  # Add lock for websocket access

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

    async def start_ticker_stream(self):
        """Maintain a single websocket connection for all price updates"""
        #print("Starting ticker stream...")
        retry_delay = 5
        
        while True:
            try:
                if self.ticker_ws:
                    await self.ticker_ws.close()
                    await asyncio.sleep(1)
                
                #("Connecting to ticker websocket...")
                self.ticker_ws = await websockets.connect(self.ws_pub_url)
                print("Connected to ticker websocket")
                
                # Subscribe to all pairs
                subscribe_msg = {
                    "method": "subscribe",
                    "params": {
                        "channel": "ticker",
                        "symbol": list(self.price_subscribers)
                    }
                }
                print(f"Sending ticker subscription: {subscribe_msg}")
                await self.ticker_ws.send(json.dumps(subscribe_msg))
                
                # Track which symbols we've received initial data for
                received_symbols = set()
                
                while True:
                    message = await self.ticker_ws.recv()
                    msg_data = json.loads(message)
                    #print(f"Received ticker message: {msg_data}")
                    
                    if msg_data.get("channel") == "ticker":
                        msg_type = msg_data.get("type")
                        if msg_type in ["snapshot", "update"]:
                            ticker_data = msg_data.get("data", [{}])[0]
                            symbol = ticker_data.get("symbol")
                            if symbol:
                                ask_price = ticker_data.get("ask")
                                if ask_price and float(ask_price) > 0:
                                    await self.price_queue.put((symbol, float(ask_price)))
                                    #print(f"Queued price update for {symbol}: {ask_price}")
                                    received_symbols.add(symbol)
                                    
                                    # Set initialized once we have data for all symbols
                                    if received_symbols == self.price_subscribers:
                                        #print("Received initial data for all symbols")
                                        self.ticker_initialized.set()
                            
            except Exception as e:
                print(f"Ticker stream error: {e}")
                print(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(retry_delay)
                self.ticker_initialized.clear()

    async def process_price_updates(self):
        """Process price updates from queue"""
        while True:
            try:
                symbol, price = await self.price_queue.get()
                self.latest_prices[symbol] = price
                #print(f"Updated {symbol} price to {price}")
                self.ticker_initialized.set()
                self.price_queue.task_done()
            except Exception as e:
                print(f"Error processing price update: {e}")
                await asyncio.sleep(1)

    def subscribe_to_ticker(self, symbol: str):
        """Subscribe to ticker updates for a symbol"""
        #print(f"Subscribing to ticker for {symbol}")
        self.price_subscribers.add(symbol)
        #print(f"Current subscribers: {self.price_subscribers}")

    async def get_ticker_price(self, symbol: str) -> float:
        """Get current market price for a symbol from cached data"""
        try:
            await asyncio.wait_for(self.ticker_initialized.wait(), timeout=10)
            price = self.latest_prices.get(symbol)
            return price
        except asyncio.TimeoutError:
            print(f"Timeout waiting for ticker initialization")
            return None

    async def create_websocket_connection(self):
        """Create a new websocket connection for order modifications"""
        try:
            # Check if existing connection is closed
            if self.edit_websocket and self.edit_websocket.state == websockets.protocol.State.CLOSED:
                try:
                    await self.edit_websocket.close()
                except:
                    pass
                self.edit_websocket = None

            websocket = await websockets.connect(self.kraken_ws.ws_url_v2)  # Use kraken_ws instance
            token = self.kraken_ws.get_auth_token()  # Use kraken_ws instance
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
            
            return websocket
                
        except Exception as e:
            print(f"Error creating websocket connection: {e}")
            raise

    def update_rate_counter(self, symbol: str, action: str, order_age: float = None):
        """
        Update rate counter for a symbol based on action and order age
        Returns True if action is allowed, False if rate limit would be exceeded
        """
        current_time = time.time()
        
        # Initialize counters if needed
        if symbol not in self.rate_counters:
            self.rate_counters[symbol] = 0
            self.last_decay_time[symbol] = current_time
        
        # Apply decay since last update
        time_diff = current_time - self.last_decay_time[symbol]
        decay = self.tier_decay_rate * time_diff
        self.rate_counters[symbol] = max(0, self.rate_counters[symbol] - decay)
        self.last_decay_time[symbol] = current_time

        # Calculate increment based on action and order age
        increment = 1  # Fixed count for add/amend
        if order_age:
            if action == "amend":
                if order_age < 5: increment += 3
                elif order_age < 10: increment += 2
                elif order_age < 15: increment += 1
            elif action == "cancel":
                if order_age < 5: increment += 8
                elif order_age < 10: increment += 6
                elif order_age < 15: increment += 5
                elif order_age < 45: increment += 4
                elif order_age < 90: increment += 2
                elif order_age < 300: increment += 1

        # Check if action would exceed threshold
        if (self.rate_counters[symbol] + increment) > self.tier_threshold:
            return False

        # Apply increment
        self.rate_counters[symbol] += increment
        return True

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
        """Initialize monitor"""
        self.kraken_ws = kraken_ws
        self.config = config
        self.symbol = symbol
        self.websocket = None  # Main websocket for order updates
        self.edit_websocket = None  # Separate websocket for order modifications
        self.active_buy_orders = {}  # Track active buy orders
        self.initial_snapshot_processed = False
        self.ws_lock = asyncio.Lock()

    async def connect_edit_websocket(self):
        """Connect to the edit websocket with heartbeat handling"""
        try:
            self.edit_websocket = await websockets.connect(self.kraken_ws.ws_url_v2)
            token = self.kraken_ws.get_auth_token()
            if not token:
                print("Failed to get auth token")
                return False

            # Initial authentication
            auth_message = {
                "method": "subscribe",
                "params": {
                    "channel": "executions",
                    "token": token,
                    "snap_orders": True
                }
            }
            
            await self.edit_websocket.send(json.dumps(auth_message))
            response = await self.edit_websocket.recv()
            response_data = json.loads(response)
            
            if response_data.get("error"):
                print(f"Authentication error: {response_data['error']}")
                return False
            
            # Start heartbeat task
            self.heartbeat_task = asyncio.create_task(self.send_heartbeats())
            
            return True
        except Exception as e:
            print(f"Failed to connect edit websocket: {e}")
            return False

    async def send_heartbeats(self):
        """Send periodic heartbeats to keep connection alive"""
        while True:
            try:
                if self.edit_websocket and self.edit_websocket.state == websockets.protocol.State.OPEN:
                    heartbeat = {"method": "ping"}
                    await self.edit_websocket.send(json.dumps(heartbeat))
                await asyncio.sleep(15)  # Send heartbeat every 15 seconds
            except Exception as e:
                print(f"Heartbeat error: {e}")
                await asyncio.sleep(5)

    async def modify_order(self, order_id: str, new_price: float, order_age: float) -> bool:
        try:
            if not self.edit_websocket or self.edit_websocket.state != websockets.protocol.State.OPEN:
                print("Reconnecting edit websocket...")
                await self.connect_edit_websocket()
                if not self.edit_websocket:
                    return False

            modify_message = {
                "method": "amend_order",
                "params": {
                    "order_id": order_id,
                    "limit_price": new_price,
                    "token": self.kraken_ws.get_auth_token()
                }
            }
            
            print(f"Sending order modification: {json.dumps(modify_message)}")
            await self.edit_websocket.send(json.dumps(modify_message))
            
            # Wait longer and handle more response types
            start_time = time.time()
            while time.time() - start_time < 10:  # Wait up to 10 seconds
                try:
                    response = await asyncio.wait_for(self.edit_websocket.recv(), timeout=2.0)
                    response_data = json.loads(response)
                    
                    # Skip heartbeat messages
                    if response_data.get("channel") == "heartbeat":
                        continue
                        
                    # Check for successful amendment
                    if response_data.get("method") == "amend_order":
                        if response_data.get("success"):
                            print(f"Successfully modified order {order_id} to price {new_price}")
                            self.active_buy_orders[order_id] = new_price
                            return True
                        else:
                            error = response_data.get("error", "Unknown error")
                            if "Order not found" in error:
                                print(f"Order {order_id} not found - may have been filled or canceled")
                                return False
                            print(f"Failed to modify order {order_id}: {error}")
                            return False
                    
                    # Check for execution updates that might indicate success
                    if response_data.get("channel") == "executions":
                        for order in response_data.get("data", []):
                            if order.get("order_id") == order_id:
                                if float(order.get("limit_price", 0)) == new_price:
                                    print(f"Order {order_id} confirmed modified to {new_price}")
                                    self.active_buy_orders[order_id] = new_price
                                    return True
                
                except asyncio.TimeoutError:
                    continue  # Keep trying until overall timeout
                    
            print(f"Timeout waiting for modification confirmation for order {order_id}")
            return False
                
        except Exception as e:
            print(f"Error modifying order {order_id}: {e}")
            return False

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
                grid_interval = self.config.grid_interval / 100
                optimal_price = round(current_price * (1 - grid_interval), 1)
                
                print(f"\nChecking grid intervals for {self.symbol}:")
                print(f"Current Price: {current_price}")
                print(f"Optimal Grid Price: {optimal_price}")
                print(f"Active Buy Orders: {self.active_buy_orders}")

                orders_to_check = self.active_buy_orders.copy()
                
                for order_id, execution_price in orders_to_check.items():
                    execution_price = float(execution_price)
                    
                    # Calculate percentage difference from current price
                    price_diff_pct = ((current_price - execution_price) / current_price) * 100
                    #print(f"Order {order_id} price difference: {price_diff_pct:.2f}%")
                    
                    target = self.config.grid_interval  # e.g., 0.8%
                    buffer = 0.1  # 0.1% buffer
                    
                    # Only adjust if the difference is GREATER than target + buffer
                    if price_diff_pct > (target + buffer):
                        print(f"Order {order_id} needs adjustment - Current diff: {price_diff_pct:.2f}% exceeds target: {target}% + {buffer}%")
                        
                        # Only modify if the new price is meaningfully different
                        if abs(optimal_price - execution_price) > 0.1:
                            try:
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
                        print(f"Order {order_id} price difference ({price_diff_pct:.2f}%) is within acceptable range of target ({target}% ±{buffer}%)")

                await asyncio.sleep(10)

            except Exception as e:
                print(f"Error in grid_interval_exceeded_update for {self.symbol}: {e}")
                print(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(5)

    async def monitor(self):
        """Main monitoring loop"""
        while True:
            try:
                # Check if websocket is closed or not established
                if not self.websocket or self.websocket.state == websockets.protocol.State.CLOSED:
                    self.websocket = await self.kraken_ws.create_connection(self.kraken_ws.ws_url_v2)
                    await self.subscribe_to_executions(self.websocket)
                    self.initial_snapshot_processed = False

                message = await self.websocket.recv()
                msg_data = json.loads(message)
                
                if msg_data.get("channel") == "executions":
                    msg_type = msg_data.get("type")
                    
                    if msg_type in ["snapshot", "update"]:
                        for order in msg_data.get("data", []):
                            order_id = order.get("order_id")
                            exec_type = order.get("exec_type")
                            order_status = order.get("order_status")
                            
                            # Handle new orders - only add if we don't have any active buy orders
                            if msg_type == "snapshot" or (exec_type in ["new", "pending_new"]):
                                side = order.get("side", "").upper()
                                order_symbol = order.get("symbol", "").upper()
                                
                                if order_symbol == self.symbol and side == "BUY":
                                    if not self.active_buy_orders:  # Only add if no active buy orders
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
                                
                                # Create new grid orders in a separate task
                                if not self.active_buy_orders:
                                    asyncio.create_task(self.handle_empty_orders())
                        
                        if msg_type == "snapshot":
                            self.initial_snapshot_processed = True
                            if not self.active_buy_orders:
                                asyncio.create_task(self.handle_empty_orders())
                                
            except websockets.exceptions.ConnectionClosed:
                print(f"Websocket connection closed for {self.symbol}, reconnecting...")
                self.websocket = None
                await asyncio.sleep(5)
            except Exception as e:
                print(f"Monitor error for {self.symbol}: {e}")
                print(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(5)

    async def handle_empty_orders(self):
        """Handle creation of new orders when no active orders exist"""
        try:
            print(f"No active buy orders left for {self.symbol}, creating new grid orders.")
            await self.create_grid_orders()
        except Exception as e:
            print(f"Error creating new grid orders for {self.symbol}: {e}")
            print(f"Full error details: {traceback.format_exc()}")

    async def create_websocket_connection(self):
        """Create a new websocket connection for order modifications"""
        try:
            # Check if existing connection is closed
            if self.edit_websocket and self.edit_websocket.state == websockets.protocol.State.CLOSED:
                try:
                    await self.edit_websocket.close()
                except:
                    pass
                self.edit_websocket = None

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
            
            return websocket
                
        except Exception as e:
            print(f"Error creating websocket connection: {e}")
            raise

    async def send_order_with_timeout(self, order_message, timeout=10):
        """Send order with timeout and reconnection logic"""
        start_time = time.time()
        last_attempt_time = 0
        
        while time.time() - start_time < timeout:
            try:
                # Ensure minimum delay between attempts
                current_time = time.time()
                if current_time - last_attempt_time < 2:
                    await asyncio.sleep(2)
                
                if not self.edit_websocket or self.edit_websocket.state != websockets.protocol.State.OPEN:
                    print("Reconnecting websocket...")
                    success = await self.connect_edit_websocket()
                    if not success:
                        print("Failed to reconnect websocket")
                        await asyncio.sleep(1)
                        continue
                
                await self.edit_websocket.send(json.dumps(order_message))
                
                # Wait for response with shorter timeout
                try:
                    response = await asyncio.wait_for(self.edit_websocket.recv(), timeout=3.0)
                    response_data = json.loads(response)
                    
                    # Skip heartbeat messages
                    if response_data.get("channel") == "heartbeat":
                        continue
                        
                    return response_data
                    
                except asyncio.TimeoutError:
                    print("Response timeout, retrying...")
                    last_attempt_time = current_time
                    continue
                    
            except Exception as e:
                print(f"Error sending order: {e}")
                last_attempt_time = time.time()
                await asyncio.sleep(1)
                continue
                
        raise TimeoutError("Operation timed out after multiple attempts")

    async def create_grid_orders(self):
        """Create new grid orders if none exist"""
        try:
            if len(self.active_buy_orders) > 0:
                return

            current_price = await self.kraken_ws.get_ticker_price(self.symbol)
            if not current_price:
                return

            grid_interval = self.config.grid_interval / 100
            buy_price = round(current_price * (1 - grid_interval), 1)
            sell_price = round(current_price * (1 + grid_interval), 1)
            
            print(f"\nCreating new grid orders for {self.symbol}:")
            print(f"Current Price: {current_price}")
            print(f"Grid Buy Price: {buy_price} (-{grid_interval*100:.1f}%)")
            print(f"Grid Sell Price: {sell_price} (+{grid_interval*100:.1f}%)")

            order_qty = self.config.trading_pairs.get(self.symbol)
            if not order_qty:
                return

            # Ensure websocket connection before placing orders
            if not self.edit_websocket or self.edit_websocket.state != websockets.protocol.State.OPEN:
                success = await self.connect_edit_websocket()
                if not success:
                    print("Failed to connect websocket")
                    return

            async with self.ws_lock:
                # Place buy order
                buy_order = {
                    "method": "add_order",
                    "params": {
                        "order_type": "limit",
                        "side": "buy",
                        "order_qty": order_qty,
                        "symbol": self.symbol,
                        "limit_price": buy_price,
                        "time_in_force": "gtc",
                        "post_only": True,
                        "token": self.kraken_ws.get_auth_token()
                    }
                }

                print(f"Placing buy order: {buy_order}")
                try:
                    buy_response = await self.send_order_with_timeout(buy_order)
                    if not buy_response or not buy_response.get("success"):
                        print(f"Failed to place buy order: {buy_response.get('error') if buy_response else 'No response'}")
                        return
                    print(f"Successfully placed buy order at {buy_price}")
                except TimeoutError:
                    print("Timeout placing buy order")
                    return

                # Place sell order using the same websocket connection
                sell_order = {
                    "method": "add_order",
                    "params": {
                        "order_type": "limit",
                        "side": "sell",
                        "order_qty": order_qty,
                        "symbol": self.symbol,
                        "limit_price": sell_price,
                        "time_in_force": "gtc",
                        "post_only": True,
                        "token": self.kraken_ws.get_auth_token()
                    }
                }

                print(f"Placing sell order: {sell_order}")
                try:
                    sell_response = await self.send_order_with_timeout(sell_order)
                    if sell_response and sell_response.get("success"):
                        print(f"Successfully placed sell order at {sell_price}")
                    elif sell_response and sell_response.get("error") == "EOrder:Insufficient funds":
                        print("Insufficient funds for sell order - continuing normally")
                    else:
                        error_msg = sell_response.get('error') if sell_response else 'No response'
                        print(f"Failed to place sell order: {error_msg}")
                except TimeoutError:
                    print("Timeout placing sell order - continuing")

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

    async def wait_for_order_response(self, expected_method: str, timeout: int = 10) -> dict:
        """Wait for specific order response with timeout"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = await asyncio.wait_for(self.edit_websocket.recv(), timeout=2.0)
                response_data = json.loads(response)
                
                # Skip heartbeat messages
                if response_data.get("channel") == "heartbeat":
                    continue
                    
                # For new orders, we need the initial response
                if expected_method == "add_order":
                    if response_data.get("method") == "add_order":
                        if response_data.get("success"):
                            return response_data
                        else:
                            print(f"Order placement failed: {response_data.get('error')}")
                            return response_data
                
                # For amendments, we need the amendment confirmation
                elif expected_method == "amend_order":
                    if response_data.get("method") == "amend_order":
                        return response_data
                        
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Error waiting for response: {e}")
                await asyncio.sleep(0.5)
                
        print(f"Timeout waiting for {expected_method} response")
        return {"success": False, "error": "Timeout waiting for response"}

async def main():
    # Initialize everything
    config = BotConfiguration(
        trading_pairs={
            "BTC/USD": 0.0001,
            "SOL/USD": 0.04,
        },
        grid_interval=0.8
    )
    
    kraken_ws = KrakenWebsocket()
    
    # Subscribe to all pairs upfront
    for symbol in config.trading_pairs:
        kraken_ws.subscribe_to_ticker(symbol)
    
    # Start ticker stream and wait for initial data
    print("Starting ticker stream...")
    ticker_task = asyncio.create_task(kraken_ws.start_ticker_stream())
    try:
        await asyncio.wait_for(kraken_ws.ticker_initialized.wait(), timeout=30)
        print("Ticker initialized successfully")
    except asyncio.TimeoutError:
        print("Failed to initialize ticker stream")
        return

    # Initialize monitors
    monitors = [MonitorOpenOrders(kraken_ws, config, symbol) for symbol in config.trading_pairs]
    
    async def check_all_pairs():
        """Check and update all trading pairs sequentially"""
        while True:
            for symbol in config.trading_pairs:
                try:
                    monitor = next(m for m in monitors if m.symbol == symbol)
                    
                    # Wait for initial snapshot to be processed
                    if not monitor.initial_snapshot_processed:
                        print(f"Waiting for initial snapshot for {symbol}...")
                        await asyncio.sleep(2)
                        continue

                    current_price = await kraken_ws.get_ticker_price(symbol)
                    if not current_price:
                        print(f"Failed to get current price for {symbol}")
                        await asyncio.sleep(5)
                        continue

                    # Check if we have any active orders for this symbol
                    if not monitor.active_buy_orders:
                        print(f"No active orders found for {symbol}, creating new grid orders...")
                        await monitor.create_grid_orders()
                        await asyncio.sleep(2)  # Brief delay after creating orders
                        continue

                    # Rest of the existing price check and order modification logic...
                    grid_interval = config.grid_interval / 100
                    optimal_price = round(current_price * (1 - grid_interval), 1)
                    
                    print(f"\nChecking grid intervals for {symbol}:")
                    print(f"Current Price: {current_price}")
                    print(f"Optimal Grid Price: {optimal_price}")
                    print(f"Active Buy Orders: {monitor.active_buy_orders}")

                    orders_to_check = monitor.active_buy_orders.copy()
                    for order_id, execution_price in orders_to_check.items():
                        execution_price = float(execution_price)
                        price_diff_pct = ((current_price - execution_price) / current_price) * 100
                        #print(f"Order {order_id} price difference: {price_diff_pct:.2f}%")
                        
                        target = config.grid_interval
                        buffer = 0.1
                        
                        if price_diff_pct > (target + buffer):
                            #print(f"Order {order_id} needs adjustment - Current diff: {price_diff_pct:.2f}% exceeds target: {target}% + {buffer}%")
                            
                            if abs(optimal_price - execution_price) > 0.1:
                                # Wait for order modification to complete
                                success = await monitor.modify_order(order_id, optimal_price, None)
                                if not success:
                                    print(f"{symbol} order failed") # Skip to next symbol if modification fails
                                await asyncio.sleep(2)  # Wait between modifications
                        else:
                            print(f"Order {order_id} price difference ({price_diff_pct:.2f}%) is within acceptable range of target ({target}% ±{buffer}%)")
                    
                    # Add delay between checking different pairs
                    await asyncio.sleep(5)
                    
                except Exception as e:
                    print(f"Error processing {symbol}: {e}")
                    print(f"Full error details: {traceback.format_exc()}")
                    await asyncio.sleep(5)
    
    # Start all tasks
    await asyncio.gather(
        ticker_task,
        kraken_ws.process_price_updates(),
        check_all_pairs(),
        *(monitor.monitor() for monitor in monitors)
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")

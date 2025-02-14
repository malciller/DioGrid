import os
import json
import time
import hmac
import base64
import hashlib
import asyncio
import aiohttp
import websockets
import traceback
import urllib.parse
from dotenv import load_dotenv
import random
# TRADING CONFIGURATION
PASSIVE_INCOME = 0 
KRAKEN_FEE = 0.002 
STARTING_PORTFOLIO_INVESTMENT = 3000.0 
PROFIT_INCREMENT = 10 
TRADING_PAIRS = {
    pair: {
        'size': size,
        'grid_interval': grid,
        'trail_interval': trail,
        'precision': precision,
        'sell_multiplier': sell_multiplier 
    }
    for pair, (size, grid, trail, precision, sell_multiplier) in {
        "BTC/USD": (0.000875, 0.75, 0.75, 1, 0.999), # $87.50 @ 3.5x (0.00025 @ 1.0x @ $100k)
        "SOL/USD": (0.06, 2.5, 2.5, 2, 0.999), # 13-16%      
        "XRP/USD": (5.0, 3.5, 3.5, 5, 0.999), # 0%          
        "ADA/USD": (18.0, 3.5, 3.5, 6, 0.999), # 2-5% 
        "ETH/USD": (0.0045, 3.5, 3.5, 2, 0.999), # 2-7%   
        "TRX/USD": (55.0, 3.5, 3.5, 6, 0.999), # 4-7%      
        "DOT/USD": (2.5, 3.5, 3.5, 4, 0.999), # 12-18% 
        "INJ/USD": (0.81, 3.5, 3.5, 3, 0.999), # 7-11%
        "KSM/USD": (0.6, 3.5, 3.5, 2, 0.999), # 16-24%
    }.items()
}
SHORT_SLEEP_TIME = 0.1
LONG_SLEEP_TIME = 3
GRID_DECAY_TIME = 5
MAX_AMEND_RETRIES = 3
AMEND_RETRY_DELAY = 5 
AMEND_RESPONSE_TIMEOUT = 10  
load_dotenv()

class Logger:
    ERROR = '\033[91m'    
    WARNING = '\033[93m' 
    INFO = '\033[96m'     
    SUCCESS = '\033[92m' 
    RESET = '\033[0m'    
    @staticmethod
    def error(msg: str, exc_info: Exception = None):
        CURRENT_TIME = time.strftime('%H:%M:%S')
        error_msg = f"{Logger.ERROR}[{CURRENT_TIME}][ERROR] {msg}{Logger.RESET}"
        if exc_info:
            error_msg += f"\n{Logger.ERROR}Exception: {str(exc_info)}{Logger.RESET}"
        print(error_msg)
    @staticmethod
    def warning(msg: str):
        CURRENT_TIME = time.strftime('%H:%M:%S')
        print(f"{Logger.WARNING}[{CURRENT_TIME}][WARNING] {msg}{Logger.RESET}")
    @staticmethod
    def info(msg: str):
        CURRENT_TIME = time.strftime('%H:%M:%S')
        print(f"{Logger.INFO}[{CURRENT_TIME}][INFO] {msg}{Logger.RESET}")
    @staticmethod
    def success(msg: str):
        CURRENT_TIME = time.strftime('%H:%M:%S')
        print(f"{Logger.SUCCESS}[{CURRENT_TIME}][SUCCESS] {msg}{Logger.RESET}")

class KrakenAPIError(Exception):
    """Custom exception class for Kraken API errors"""
    def __init__(self, error_code, message=None):
        self.error_code = error_code
        self.message = message or error_code
        super().__init__(f"{error_code}: {self.message}")

class KrakenWebSocketClient:
    def __init__(self):
        self.api_key = os.getenv('KRAKEN_API_KEY')
        self.api_secret = os.getenv('KRAKEN_API_SECRET')
        self.ws_auth_url = "wss://ws-auth.kraken.com/v2"  
        self.ws_public_url = "wss://ws.kraken.com/v2"    
        self.rest_url = "https://api.kraken.com"
        self.websocket = None
        self.public_websocket = None  
        self.running = True
        self.connection_status = {
            'private': {'connected': False, 'last_ping': time.time(), 'last_pong': time.time()},
            'public': {'connected': False, 'last_ping': time.time(), 'last_pong': time.time()}
        }
        self.last_ticker_time = time.time()  
        self.ping_interval = 15
        self.pong_timeout = 5
        self.reconnect_delay = 3
        self.max_reconnect_attempts = 10
        self.execution_rate_limit = None
        self.subscriptions = {}
        self.handlers = {}
        self.balances = {}
        self.orders = {}
        self.maintenance_task = None
        self.message_task = None
        self.ticker_data = {}
        self.active_trading_pairs = set()  
        self.portfolio_value = 0.0
        self.last_portfolio_update = 0
        self.update_interval = 5  
        self.last_profit_take_time = 0
        self.profit_take_cooldown = 300  
        self.highest_portfolio_value = STARTING_PORTFOLIO_INVESTMENT
        self.ticker_subscriptions = {}  
        self.public_message_task = None
        self.earn_balances = {}  
        self.subscribed_channels = set()  
        self.subscription_lock = asyncio.Lock()  
        self.subscription_retry_delay = 1  
        self.ws_timeout = 30
        self.ws_options = {
            "ping_interval": None,
            "ping_timeout": None,
            "close_timeout": 5,
            "max_size": 2**23,
            "max_queue": 2**10
        }
        # Rate limiting parameters
        self.initial_backoff = 1    # Start with 1 second
        self.max_backoff = 300      # Max 5 minutes
        self.backoff_factor = 2     # Double the delay each time
        self.jitter = 0.1           # Add 10% random jitter
        self.earn_strategies = {}
        self.strategy_ids = {}
        self._public_ping_lock = asyncio.Lock()
    """
    Generates a Kraken API signature for authentication.
    
    Args:
        urlpath (str): The API endpoint path
        data (dict): The request data to be signed
        
    Returns:
        str: Base64 encoded signature for API authentication
    """
    def get_kraken_signature(self, urlpath, data):
        post_data = urllib.parse.urlencode(data)
        encoded = (data['nonce'] + post_data).encode('utf-8')
        message = urlpath.encode('utf-8') + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()
    """
    Retrieves a WebSocket authentication token from Kraken's REST API.
    
    Returns:
        str: Authentication token for WebSocket connection
        
    Raises:
        KrakenAPIError: If token retrieval fails
    """
    async def get_ws_token(self):
        """Get WebSocket authentication token from REST API"""
        path = "/0/private/GetWebSocketsToken"
        url = self.rest_url + path
        nonce = str(int(time.time() * 1000))
        post_data = {"nonce": nonce}
        headers = {
            "API-Key": self.api_key,
            "API-Sign": self.get_kraken_signature(path, post_data),
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=post_data) as response:
                    if response.status != 200:
                        raise KrakenAPIError('EService:Unavailable', f"HTTP request failed: {response.status}")
                    result = await response.json()
                    if 'error' in result and result['error']:
                        error_code = result['error'][0]
                        raise KrakenAPIError(error_code)
                    return result.get("result", {}).get("token")
        except aiohttp.ClientError as e:
            raise KrakenAPIError('EService:Unavailable', f"HTTP request failed: {str(e)}")
        except Exception as e:
            raise KrakenAPIError('EGeneral:Internal error', str(e))
    """
    Establishes WebSocket connections to Kraken's authenticated and public endpoints.
    Initializes message handling and maintenance tasks.
    
    Returns:
        str: Authentication token from successful connection
        
    Raises:
        Exception: If connection fails
    """
    async def connect_with_backoff(self):
        """Connect to WebSocket with exponential backoff."""
        current_backoff = self.initial_backoff
        attempt = 0
        
        while attempt < self.max_reconnect_attempts:
            try:
                # First try to get the auth token
                token = await self.get_ws_token()
                if not token:
                    raise KrakenAPIError('EAPI:Invalid key', 'Failed to obtain WebSocket token')

                # Try private connection first
                try:
                    self.websocket = await asyncio.wait_for(
                        websockets.connect(self.ws_auth_url, **self.ws_options),
                        timeout=self.ws_timeout
                    )
                    self.connection_status['private']['connected'] = True
                    Logger.success("CONNECTION: PRIVATE - 200 - OK")
                except Exception as e:
                    Logger.error(f"Private WebSocket connection failed: {str(e)}")
                    raise

                # Add delay before public connection attempt
                await asyncio.sleep(1)

                # Then try public connection
                try:
                    self.public_websocket = await asyncio.wait_for(
                        websockets.connect(self.ws_public_url, **self.ws_options),
                        timeout=self.ws_timeout
                    )
                    self.connection_status['public']['connected'] = True
                    Logger.success("CONNECTION: PUBLIC - 200 - OK")
                except Exception as e:
                    Logger.error(f"Public WebSocket connection failed: {str(e)}")
                    # Close private connection if public fails
                    if self.websocket:
                        await self.websocket.close()
                    raise

                return token

            except websockets.exceptions.InvalidStatus as e:
                if "HTTP 429" in str(e):
                    Logger.warning(f"Rate limit hit. Backing off for {current_backoff} seconds...")
                    jitter_amount = random.uniform(-self.jitter, self.jitter)
                    backoff_with_jitter = current_backoff * (1 + jitter_amount)
                    await asyncio.sleep(backoff_with_jitter)
                    current_backoff = min(current_backoff * self.backoff_factor, self.max_backoff)
                    attempt += 1
                    continue
                else:
                    raise

            except Exception as e:
                Logger.error(f"Connection attempt {attempt + 1} failed: {str(e)}")
                await asyncio.sleep(self.reconnect_delay)
                attempt += 1
                
                # Reset connection status
                self.connection_status['private']['connected'] = False
                self.connection_status['public']['connected'] = False

        raise Exception(f"Failed to connect after {self.max_reconnect_attempts} attempts")
    """
    Establishes initial WebSocket connection with backoff retry logic.
    Creates and starts maintenance tasks for connection monitoring and message handling.
    
    Returns:
        str: Authentication token from successful connection
        
    Raises:
        Exception: If initial connection fails
    """
    async def connect(self):
        """Initial connection with backoff."""
        try:
            token = await self.connect_with_backoff()
            # Start maintenance tasks
            self.maintenance_task = asyncio.create_task(self.maintain_connection())
            self.message_task = asyncio.create_task(self.handle_messages())
            self.public_message_task = asyncio.create_task(self.handle_public_messages())
            return token
        except Exception as e:
            Logger.error(f"Initial connection failed: {str(e)}")
            await self.disconnect()
            raise
    """
    Closes all WebSocket connections and cancels maintenance tasks.
    
    Raises:
        Exception: If error occurs during disconnect process
    """
    async def disconnect(self):
        """Disconnect from both WebSocket connections."""
        self.running = False
        # Cancel all tasks if they exist
        tasks = [self.maintenance_task, self.message_task, self.public_message_task]
        for task in tasks:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        # Reset task attributes
        self.maintenance_task = None
        self.message_task = None
        self.public_message_task = None
        # Close connections if they exist and are open
        try:
            if self.websocket:
                await self.websocket.close()
            if self.public_websocket:
                await self.public_websocket.close()
        except Exception as e:
            Logger.error(f"Error during disconnect: {str(e)}")
    """
    Monitors WebSocket connection health and handles reconnection attempts.
    Sends periodic ping messages and checks for pong responses.
    Reconnects if connection is lost or unresponsive.
    
    Raises:
        Exception: If maintenance encounters an error
    """
    async def maintain_connection(self):
        """Monitor and maintain WebSocket connection health."""
        reconnect_attempts = 0
        try:
            while self.running:
                current_time = time.time()
                needs_reconnect = False

                # Check private connection health
                if self.connection_status['private']['connected']:
                    time_since_pong = current_time - self.connection_status['private']['last_pong']
                    if current_time - self.connection_status['private']['last_ping'] >= self.ping_interval:
                        try:
                            await self.ping()
                            self.connection_status['private']['last_ping'] = current_time
                        except Exception as e:
                            Logger.warning(f"Failed to send private ping: {str(e)}")
                            needs_reconnect = True

                # Check public connection health using last ticker time instead of explicit ping
                if self.connection_status['public']['connected']:
                    time_since_ticker = current_time - self.last_ticker_time
                    if time_since_ticker > self.ping_interval * 2:  # Allow more time before triggering reconnect
                        Logger.warning("No recent ticker updates, connection may be stale")
                        needs_reconnect = True

                if needs_reconnect:
                    if reconnect_attempts < self.max_reconnect_attempts:
                        reconnect_attempts += 1
                        Logger.warning(f"Connection issues detected. Attempting reconnection (attempt {reconnect_attempts}/{self.max_reconnect_attempts})")
                        try:
                            await self.reconnect()
                            reconnect_attempts = 0
                            continue
                        except Exception as e:
                            Logger.error(f"Reconnection attempt failed: {str(e)}")
                            await asyncio.sleep(self.backoff_factor ** reconnect_attempts)
                    else:
                        Logger.error("Max reconnection attempts reached")
                        self.running = False
                        break

                await asyncio.sleep(LONG_SLEEP_TIME)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            Logger.error(f"Connection maintenance error: {str(e)}")
            self.running = False
    """
    Sends a ping message to verify connection health.
    
    Raises:
        Exception: If ping message fails to send
    """
    async def ping(self):
        """Send a ping message to the private connection."""
        current_time = int(time.time() * 1000)
        ping_message = {
            "method": "ping",
            "req_id": current_time
        }
        try:
            await self.websocket.send(json.dumps(ping_message))
        except Exception as e:
            Logger.error("Error sending ping", e)
    """
    Attempts to reestablish lost WebSocket connections.
    Handles reconnection to both private and public endpoints.
    
    Raises:
        Exception: If reconnection fails
    """
    async def reconnect(self):
        """Reconnect to WebSocket endpoints with backoff."""
        Logger.info("Starting reconnection process...")
        try:
            # Cancel existing tasks
            tasks = [self.maintenance_task, self.message_task, self.public_message_task]
            for task in tasks:
                if task is not None:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            # Reset task attributes
            self.maintenance_task = None
            self.message_task = None
            self.public_message_task = None

            # Close existing connections gracefully
            if self.websocket:
                try:
                    await self.websocket.close()
                except Exception as e:
                    Logger.warning(f"Error closing private connection: {str(e)}")
                self.websocket = None
            
            if self.public_websocket:
                try:
                    await self.public_websocket.close()
                except Exception as e:
                    Logger.warning(f"Error closing public connection: {str(e)}")
                self.public_websocket = None

            # Reset connection status
            self.connection_status['private']['connected'] = False
            self.connection_status['public']['connected'] = False

            # Wait before attempting reconnection
            await asyncio.sleep(self.reconnect_delay)

            # Connect with backoff
            token = await self.connect_with_backoff()

            # Reset connection status timestamps
            current_time = time.time()
            self.connection_status['private'].update({
                'last_ping': current_time,
                'last_pong': current_time
            })
            self.connection_status['public'].update({
                'last_ping': current_time,
                'last_pong': current_time
            })
            self.last_ticker_time = current_time

            # Restart message handling tasks
            self.message_task = asyncio.create_task(self.handle_messages())
            self.public_message_task = asyncio.create_task(self.handle_public_messages())
            self.maintenance_task = asyncio.create_task(self.maintain_connection())

            # Resubscribe to channels
            await self.subscribe(['balances', 'executions'], token)
            if self.active_trading_pairs:
                await self.subscribe_ticker(list(self.active_trading_pairs))

            Logger.success("Reconnection successful")
            return True

        except Exception as e:
            Logger.error(f"Reconnection failed: {str(e)}")
            # Reset websocket objects on failure
            self.websocket = None
            self.public_websocket = None
            self.connection_status['private']['connected'] = False
            self.connection_status['public']['connected'] = False
            raise
    """
    Processes incoming messages from the private WebSocket connection.
    Handles various message types including order updates and system messages.
    
    Raises:
        websockets.exceptions.ConnectionClosed: If connection is lost
        Exception: For other processing errors
    """
    async def handle_messages(self):
        """Handle incoming messages from the WebSocket."""
        while self.running:
            try:
                # Add timeout to receive operation
                message = await asyncio.wait_for(
                    self.websocket.recv(),
                    timeout=self.ws_timeout
                )
                await self.handle_message(message)
            except asyncio.TimeoutError:
                Logger.warning("Message receive timeout, checking connection...")
                if not self.websocket.open:
                    Logger.warning("WebSocket connection lost")
                    await self.reconnect()
                    break
            except websockets.exceptions.ConnectionClosed:
                Logger.warning("Private WebSocket connection closed")
                await self.reconnect()
                break
            except Exception as e:
                Logger.error(f"Error in message handling: {str(e)}")
                await asyncio.sleep(LONG_SLEEP_TIME)
    """
    Processes incoming messages from the public WebSocket connection.
    Handles ticker updates and heartbeat messages.
    
    Raises:
        websockets.exceptions.ConnectionClosed: If connection is lost
        Exception: For other processing errors
    """
    async def handle_public_messages(self):
        """Handle messages from public WebSocket."""
        while self.running:
            try:
                # Add timeout to receive operation
                message = await asyncio.wait_for(
                    self.public_websocket.recv(),
                    timeout=self.ws_timeout
                )
                data = json.loads(message)
                # Update last ticker time for any valid message
                self.last_ticker_time = time.time()
                if isinstance(data, dict):
                    if data.get('event') == 'error':
                        Logger.error(f"WebSocket error: {data.get('error')}")
                        continue
                    elif data.get('method') == 'pong':
                        continue
                    elif data.get('method') in ['subscribe', 'unsubscribe']:
                        if not data.get('success') and data.get('error'):
                            Logger.error(f"WebSocket subscription error: {data.get('error')}")
                        continue
                if data.get('channel') == 'ticker':
                    await self.handle_ticker(data) 
            except asyncio.TimeoutError:
                Logger.warning("Public message receive timeout, checking connection...")
                if not self.public_websocket.open:
                    Logger.warning("Public WebSocket connection lost")
                    await self.reconnect()
                    break
            except websockets.exceptions.ConnectionClosed:
                Logger.warning("Public WebSocket connection closed")
                await self.reconnect()
                break
            except Exception as e:
                Logger.error(f"Error in public message handling: {str(e)}")
                await asyncio.sleep(LONG_SLEEP_TIME)
    """
    Processes a single WebSocket message.
    Handles response matching, pong messages, and channel-specific data.
    
    Args:
        message (str): The raw message received from WebSocket
        
    Raises:
        json.JSONDecodeError: If message is not valid JSON
        Exception: For other processing errors
    """
    async def handle_message(self, message):
        """Process a single message from the WebSocket."""
        try:
            data = json.loads(message)
            # Handle subscription responses
            if isinstance(data, dict) and data.get('method') in ['subscribe', 'unsubscribe']:
                if data.get('success') is False:
                    error_msg = data.get('error', 'Unknown error')
                    if 'Already subscribed' not in error_msg:  
                        Logger.error(f"WebSocket subscription error: {error_msg}")
                return
            if isinstance(data, dict) and 'req_id' in data:
                req_id = data['req_id']
                if hasattr(self, '_response_futures') and req_id in self._response_futures:
                    if not self._response_futures[req_id].done():
                        self._response_futures[req_id].set_result(data)
                    return
            if isinstance(data, dict) and (data.get('event') == 'pong' or data.get('method') == 'pong'):
                self.connection_status['private']['last_pong'] = time.time()
                return
            if isinstance(data, dict) and data.get('method') == 'edit_order':
                return
            if 'channel' in data:
                channel = data['channel']
                if channel in self.handlers:
                    await self.handlers[channel](data)
            elif 'event' in data:
                if data.get('event') == 'systemStatus' and data.get('status') != 'online':
                    Logger.warning(f"System status: {data}")
        except json.JSONDecodeError as e:
            Logger.error("Invalid JSON message", e)
        except Exception as e:
            Logger.error("Error handling message", e)
    """
    Subscribes to specified WebSocket channels.
    
    Args:
        channels (list): List of channel names to subscribe to
        token (str): Authentication token for private channels
        
    Raises:
        Exception: If subscription fails
    """
    async def subscribe(self, channels, token):
        """Subscribe to the specified channels with duplicate protection."""
        async with self.subscription_lock:
            for channel in channels:
                if channel in self.subscribed_channels:
                    continue
                subscribe_message = {
                    "method": "subscribe",
                    "params": {
                        "channel": channel,
                        "token": token,
                    }
                }
                if channel == 'executions':
                    subscribe_message["params"].update({
                        "snap_orders": True,
                        "snap_trades": False
                    })
                elif channel == 'balances':
                    subscribe_message["params"]["snapshot"] = True
                try:
                    await self.websocket.send(json.dumps(subscribe_message))
                    self.subscribed_channels.add(channel)
                    await asyncio.sleep(self.subscription_retry_delay) 
                except Exception as e:
                    Logger.error(f"Error subscribing to {channel}: {str(e)}")
    """
    Unsubscribes from specified WebSocket channels.
    
    Args:
        channels (list): List of channel names to unsubscribe from
        
    Raises:
        websockets.exceptions.ConnectionClosed: If connection is lost
        Exception: If unsubscribe fails
    """
    async def unsubscribe(self, channels):
        """Unsubscribe from the specified channels."""
        if not self.websocket or self.websocket.close:
            return
        for channel in channels:
            try:
                unsubscribe_message = {
                    "method": "unsubscribe",
                    "params": {
                        "channel": channel,
                        "token": await self.get_ws_token()
                    }
                }
                await self.websocket.send(json.dumps(unsubscribe_message))
                Logger.warning(f"Unsubscribed from {channel} channel")
                await asyncio.sleep(SHORT_SLEEP_TIME)
            except websockets.exceptions.ConnectionClosed:
                Logger.warning(f"Connection closed while unsubscribing from {channel}")
                break
            except Exception as e:
                Logger.error(f"Error unsubscribing from {channel}", e)
    """
    Registers a handler function for a specific channel.
    
    Args:
        channel (str): Channel name to register handler for
        handler (callable): Function to handle channel messages
    """
    def set_handler(self, channel, handler):
        """Set a handler function for a specific channel."""
        self.handlers[channel] = handler
    """
    Calculates total portfolio value across all assets.
    Updates internal portfolio tracking and checks profit targets.
    
    Raises:
        Exception: If calculation fails
    """
    async def calculate_portfolio_value(self):
        """Calculate total portfolio value in USD."""
        total_value = 0.0
        for asset, balance in self.balances.items():
            if balance <= 0:
                continue
                
            if asset == 'USD':
                total_value += balance
            elif asset == 'USDC':
                # Use USDC/USD ticker if available, otherwise assume 1:1
                ticker = self.ticker_data.get('USDC/USD', {})
                price = float(ticker.get('last', 1.0))
                total_value += balance * price
            else:
                ticker_symbol = f"{asset}/USD"
                ticker = self.ticker_data.get(ticker_symbol)
                if ticker and ticker.get('last'):
                    price = float(ticker['last'])
                    value = balance * price
                    total_value += value
        self.portfolio_value = total_value
        Logger.success(f"BALANCE: 200 - OK")
        # Check if we should take profit
        if PASSIVE_INCOME == 1:
            await self.check_and_take_profit()
        else:
            return
    """
    Monitors portfolio value and executes profit-taking orders when targets are met.
    """
    async def check_and_take_profit(self):
        """Check if we should take profit and execute USDC order if needed."""
        current_time = time.time()
        # Check if we're still in cooldown
        if current_time - self.last_profit_take_time < self.profit_take_cooldown:
            return
        usdc_balance = self.balances.get('USDC', 0)
        profit_threshold = self.highest_portfolio_value + PROFIT_INCREMENT + usdc_balance
        if self.portfolio_value >= profit_threshold:
            # Update highest portfolio value
            self.highest_portfolio_value = self.portfolio_value - PROFIT_INCREMENT
            # Create market order for USDC
            order_message = {
                "method": "add_order",
                "params": {
                    "order_type": "market",
                    "side": "buy",
                    "cash_order_qty": PROFIT_INCREMENT, 
                    "symbol": "USDC/USD",
                    "token": await self.get_ws_token()
                },
                "req_id": int(time.time() * 1000)
            }
            try:
                await self.websocket.send(json.dumps(order_message))
                Logger.success(f"PROFIT: Taking profit of ${PROFIT_INCREMENT:.2f} USDC at portfolio value ${self.portfolio_value:.2f}")
                self.last_profit_take_time = current_time
            except Exception as e:
                Logger.error(f"Error taking profit: {str(e)}")
    """
    Processes balance updates and manages ticker subscriptions.
    Updates internal balance tracking and adjusts subscriptions based on holdings.
    
    Args:
        data (dict): Balance update data from WebSocket
        
    Raises:
        Exception: If update processing fails
    """
    async def handle_balance_updates(self, data):
        """Handle incoming balance updates and manage ticker subscriptions."""
        if data.get('type') in ['snapshot', 'update']:
            # Keep track of all assets with non-zero balances
            assets_with_balance = set()
            for asset_data in data.get('data', []):
                asset_code = asset_data.get('asset')
                # Reset earn balance for this asset
                self.earn_balances[asset_code] = 0.0
                # Process wallets
                wallets = asset_data.get('wallets', [])
                total_balance = 0.0
                if wallets:
                    # If we have wallet data, process each wallet
                    for wallet in wallets:
                        wallet_type = wallet.get('type')
                        wallet_balance = float(wallet.get('balance', 0))
                        # Store earn wallet balances separately
                        if wallet_type in ['earn', 'bonded', 'locked']:
                            self.earn_balances[asset_code] = wallet_balance
                        # Add to total balance regardless of wallet type
                        total_balance += wallet_balance
                else:
                    # Fallback to legacy format if no wallets field
                    total_balance = float(asset_data.get('balance', 0))
                # Update total balance
                self.balances[asset_code] = total_balance
                # Add to tracking set if non-zero balance and not USD
                if total_balance > 0 and asset_code != 'USD':
                    assets_with_balance.add(asset_code)
            # Now check all known balances for non-zero amounts
            # This ensures we don't lose tracking of assets not included in this update
            for asset_code, balance in self.balances.items():
                if balance > 0 and asset_code != 'USD':
                    assets_with_balance.add(asset_code)
            # Convert assets to trading pairs
            new_trading_pairs = {f"{asset}/USD" for asset in assets_with_balance}
            # Handle subscription changes if needed
            pairs_to_remove = self.active_trading_pairs - new_trading_pairs
            pairs_to_add = new_trading_pairs - self.active_trading_pairs
            if pairs_to_remove:
                await self.unsubscribe_ticker(list(pairs_to_remove))
            if pairs_to_add:
                await self.subscribe_ticker(list(pairs_to_add))
            self.active_trading_pairs = new_trading_pairs
            # Calculate portfolio value after balance update
            await self.calculate_portfolio_value()
    """
    Subscribes to ticker data for specified trading pairs.
    
    Args:
        symbols (list): List of trading pair symbols to subscribe to
        
    Raises:
        Exception: If subscription fails
    """
    async def subscribe_ticker(self, symbols):
        """Subscribe to ticker data with duplicate protection."""
        if not symbols:
            return
        async with self.subscription_lock:
            for symbol in symbols:
                if symbol in self.ticker_subscriptions:
                    continue
                subscribe_message = {
                    "method": "subscribe",
                    "params": {
                        "channel": "ticker",
                        "symbol": [symbol]
                    }
                }
                try:
                    await self.public_websocket.send(json.dumps(subscribe_message))
                    self.ticker_subscriptions[symbol] = True
                    await asyncio.sleep(self.subscription_retry_delay) 
                except Exception as e:
                    Logger.error(f"Error subscribing to ticker for {symbol}: {str(e)}")
    """
    Unsubscribes from ticker data for specified trading pairs.
    
    Args:
        symbols (list): List of trading pair symbols to unsubscribe from
        
    Raises:
        websockets.exceptions.ConnectionClosed: If connection is lost
        Exception: If unsubscribe fails
    """
    async def unsubscribe_ticker(self, symbols):
        """Unsubscribe from ticker data for specified symbols."""
        if not symbols or not self.public_websocket:
            return
        try:
            unsubscribe_message = {
                "method": "unsubscribe",
                "params": {
                    "channel": "ticker",
                    "symbol": symbols
                }
            }
            await self.public_websocket.send(json.dumps(unsubscribe_message))
            for symbol in symbols:
                self.ticker_subscriptions.pop(symbol, None)
            await asyncio.sleep(SHORT_SLEEP_TIME)
        except websockets.exceptions.ConnectionClosed:
            Logger.warning("Public connection closed while unsubscribing from tickers")
        except Exception as e:
            Logger.error(f"Error unsubscribing from tickers", e)
    """
    Processes incoming ticker updates and updates portfolio values.
    
    Args:
        data (dict): Ticker update data from WebSocket
        
    Raises:
        Exception: If ticker processing fails
    """
    async def handle_ticker(self, data):
        """Handle incoming ticker updates."""
        if data.get('channel') == 'ticker':
            self.last_ticker_time = time.time() 
            update_portfolio = False
            current_time = time.time()
            for ticker_data in data.get('data', []):
                symbol = ticker_data.get('symbol')
                self.ticker_data[symbol] = {
                    'last': ticker_data.get('last'),
                    'bid': ticker_data.get('bid'),
                    'ask': ticker_data.get('ask'),
                    'volume': ticker_data.get('volume'),
                    'vwap': ticker_data.get('vwap')
                }
                update_portfolio = True
            # Update portfolio value if enough time has passed
            if update_portfolio and (current_time - self.last_portfolio_update) >= self.update_interval:
                await self.calculate_portfolio_value()
                self.last_portfolio_update = current_time
    """
    Processes execution updates for orders.
    Updates internal order tracking and handles various execution types.
    
    Args:
        data (dict): Execution update data from WebSocket
        
    Raises:
        Exception: If execution processing fails
    """
    async def handle_execution_updates(self, data):
        """Handle incoming execution updates."""
        if data.get('type') == 'snapshot':
            self.orders = {}
            for execution in data.get('data', []):
                if execution.get('order_status') in ['new', 'partially_filled']:
                    order_id = execution.get('order_id')
                    if order_id:
                        self.orders[order_id] = execution
        elif data.get('type') == 'update':
            for execution in data.get('data', []):
                order_id = execution.get('order_id')
                if not order_id:
                    continue   
                exec_type = execution.get('exec_type')
                order_status = execution.get('order_status')
                symbol = execution.get('symbol')
                if exec_type in ['filled', 'canceled', 'expired']:
                    if order_id in self.orders:
                        removed_order = self.orders.pop(order_id)
                elif exec_type in ['new', 'pending_new']:
                    if order_id in self.orders:
                        self.orders[order_id].update(execution)
                    else:
                        self.orders[order_id] = execution
                        Logger.info(f"ORDER: NEW {symbol} - {order_id}")
                elif order_id in self.orders:
                    self.orders[order_id].update(execution)
    """
    Waits for a response to a specific request with timeout.
    
    Args:
        req_id: Request ID to wait for
        timeout (int): Maximum time to wait in seconds
        
    Returns:
        dict: Response data if received
        None: If timeout occurs
        
    Raises:
        Exception: If waiting fails
    """
    async def wait_for_response(self, req_id, timeout=5):
        """Wait for a response to a specific request."""
        # Create response future if it doesn't exist
        if not hasattr(self, '_response_futures'):
            self._response_futures = {}
        # Create future for this request
        future = asyncio.Future()
        self._response_futures[req_id] = future
        try:
            # Wait for response with timeout
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            Logger.warning(f"Timeout waiting for response to request {req_id}")
            return None
        finally:
            # Clean up future
            self._response_futures.pop(req_id, None)
    """
        Fetch available earn strategies from Kraken.
        
        Makes an authenticated POST request to Kraken's Earn/Strategies endpoint to retrieve
        available staking and earn opportunities.
        
        Returns:
            dict: Dictionary mapping asset codes to their earn strategy details including:
                - id: Strategy identifier
                - apr_estimate: Estimated annual percentage rate
                - lock_type: Type of lock period (if any)
                - can_allocate: Whether user can allocate funds
                - min_allocation: Minimum allocation amount
                - user_cap: Maximum allocation cap per user
                
        Raises:
            KrakenAPIError: If the API request fails, returns error, or service is unavailable
            Exception: For any other unexpected errors
    """     
    async def get_earn_strategies(self):
        path = "/0/private/Earn/Strategies"
        url = self.rest_url + path
        nonce = str(int(time.time() * 1000))
        post_data = {
            "nonce": nonce,
        }
        headers = {
            "API-Key": self.api_key,
            "API-Sign": self.get_kraken_signature(path, post_data),
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=post_data) as response:
                    if response.status != 200:
                        raise KrakenAPIError('EService:Unavailable', f"HTTP request failed: {response.status}")
                    result = await response.json()
                    if 'error' in result and result['error']:
                        error_code = result['error'][0]
                        raise KrakenAPIError(error_code)
                    # Process and store strategies
                    strategies = result.get('result', {}).get('items', [])
                    for strategy in strategies:
                        asset = strategy.get('asset')
                        if asset:
                            self.earn_strategies[asset] = {
                                'id': strategy.get('id'),
                                'apr_estimate': strategy.get('apr_estimate'),
                                'lock_type': strategy.get('lock_type', {}).get('type'),
                                'can_allocate': strategy.get('can_allocate', False),
                                'min_allocation': strategy.get('user_min_allocation'),
                                'user_cap': strategy.get('user_cap')
                            }
                            self.strategy_ids[asset] = strategy.get('id')
                    return self.earn_strategies
        except aiohttp.ClientError as e:
            raise KrakenAPIError('EService:Unavailable', f"HTTP request failed: {str(e)}")
        except Exception as e:
            raise KrakenAPIError('EGeneral:Internal error', str(e))
    """
        Calculate available spot balance for an asset by checking:
        1. Total balance
        2. Subtracting amount in open sell orders
        3. Subtracting amount in earn wallet
        
        Args:
            asset (str): Asset code (e.g., 'BTC', 'ETH')
            
        Returns:
            float: Available spot balance
    """
    async def get_available_spot_balance(self, asset: str) -> float:
        try:
            # Get total balance for the asset
            total_balance = self.balances.get(asset, 0.0)
            if total_balance <= 0:
                return 0.0

            if asset == "INJ":
                Logger.info(f"DEBUG {asset}: Total balance: {total_balance}")
            
            # Calculate amount in open sell orders
            amount_in_orders = 0.0
            for order in self.orders.values():
                if (order.get('symbol', '').startswith(asset + '/') and 
                    order.get('side') == 'sell' and
                    order.get('order_status') in ['new', 'partially_filled']):
                    order_qty = float(order.get('order_qty', 0.0))
                    amount_in_orders += order_qty
 

            amount_in_earn = self.earn_balances.get(asset, 0.0)
            
            if asset == "INJ":
                Logger.info(f"DEBUG {asset}: Amount in orders: {amount_in_orders}, Amount in earn: {amount_in_earn}")
            
            # Calculate available balance
            available_balance = total_balance - amount_in_orders - amount_in_earn
            
            if asset == "INJ":
                Logger.info(f"DEBUG {asset}: Final available balance: {available_balance}")
            
            return max(0.0, available_balance)
        except Exception as e:
            Logger.error(f"Error calculating available balance for {asset}: {str(e)}")
            return 0.0

    async def initialize(self):
        """Initialize async components"""
        try:
            self.earn_strategies = await self.get_earn_strategies()
            self.strategy_ids = {asset: data['id'] for asset, data in self.earn_strategies.items()}
            return self
        except Exception as e:
            Logger.error(f"Failed to initialize earn strategies: {str(e)}")
            return self

class KrakenGridBot:
    def __init__(self, client: KrakenWebSocketClient):
        self.client = client
        self.active_grids = {} 
        self.grid_settings = {
            pair: {
                'buy_order_size': settings['size'],
                'sell_order_size': settings['size'],
                'grid_interval': settings['grid_interval'],
                'trail_interval': settings['trail_interval'],
                'active_orders': set(),
                'last_order_time': 0 
            }
            for pair, settings in TRADING_PAIRS.items()
        }
        self.grid_orders = {
            pair: {'buy': None, 'sell': None} 
            for pair in TRADING_PAIRS.keys()
        }
    """
    Format price with appropriate decimal precision for a trading pair.
    
    Looks up the configured precision for the trading pair and formats the price
    accordingly. Each trading pair can have different precision requirements.
    
    Args:
        trading_pair (str): The trading pair symbol (e.g. "BTC/USD")
        price (float): The raw price value to format
        
    Returns:
        float: The price formatted with the appropriate precision
        
    Example:
        >>> format_price_for_pair("BTC/USD", 50123.4567)
        50123.5  # With precision=1
    """
    def format_price_for_pair(self, trading_pair: str, price: float) -> float:
        # Get precision from trading pair settings, default to 2 if not found
        precision = TRADING_PAIRS.get(trading_pair, {}).get('precision', 2)
        # Format the price with the specified precision
        formatted_price = float(f"{price:.{precision}f}")
        return formatted_price
    """
    Initialize and start the grid trading strategy.
    
    Sets up handlers for order updates and market data.
    Initializes orders for each configured trading pair.
    
    Raises:
        Exception: If initialization or order setup fails
    """
    async def start(self):
        """Initialize and start the grid trading strategy."""
        # Subscribe to all trading pairs defined in configuration
        trading_pairs = list(TRADING_PAIRS.keys())
        await self.client.subscribe_ticker(trading_pairs)
    
    """
    Cancels an existing order.
    
    Args:
        order_id (str): ID of order to cancel
        
    Raises:
        Exception: If cancellation fails
    """
    async def cancel_order(self, order_id: str):
        """Cancel an existing order using WebSocket API."""
        cancel_message = {
            "method": "cancel_order",
            "params": {
                "order_id": [order_id],  
                "token": await self.client.get_ws_token()
            },
            "req_id": int(time.time() * 1000)
        }
        try:
            await self.client.websocket.send(json.dumps(cancel_message))
            # Wait briefly for the cancel to process
            await asyncio.sleep(LONG_SLEEP_TIME)
            # Verify the order was removed from client.orders
            if order_id not in self.client.orders:
                Logger.success(f"ORDER: CANCELED {order_id}")
            else:
                Logger.warning(f"ORDER: {order_id} may not have been canceled")    
        except Exception as e:
            Logger.error(f"Error sending cancel request: {str(e)}")
            raise
    """
    Checks for open orders for a trading pair.
    
    Args:
        trading_pair (str): Trading pair to check
        
    Returns:
        dict: Open order details if found
        None: If no open orders
    """
    async def check_open_orders(self, trading_pair: str):
        """Check if there are any open orders for a trading pair."""
        # Get the asset code from the trading pair (e.g., 'BTC' from 'BTC/USD')
        asset = trading_pair.split('/')[0]
        # Only check if this is a configured trading pair
        if trading_pair not in TRADING_PAIRS:
            return
        # Check and log available spot balance
        available_balance = await self.client.get_available_spot_balance(asset)
        # Filter orders for the specified trading pair that are open
        open_orders = [
            order for order in self.client.orders.values()
            if (order.get('symbol') == trading_pair and 
                order.get('side') == 'buy' and  # Only look for buy orders
                order.get('order_status') in ['new', 'partially_filled'] and
                order.get('order_id'))
        ]
        if open_orders:
            # Only check earn opportunities if the asset is eligible
            if asset in self.client.earn_strategies:
                strategy = self.client.earn_strategies[asset]
                # Get minimum allocation from strategy
                min_allocation = float(strategy['min_allocation'])
                # Get current price from ticker data
                ticker = self.client.ticker_data.get(trading_pair)
                if ticker and ticker.get('last'):
                    current_price = float(ticker['last'])
                    # Calculate USD value of available balance
                    available_balance_usd = available_balance * current_price
                    if strategy.get('can_allocate') and available_balance_usd >= min_allocation:
                        Logger.info(f"Found stakeable {asset} balance: {available_balance:.8f} {asset} (${available_balance_usd:.2f}, min: ${min_allocation:.2f})")
                        # Format amount to match asset precision
                        formatted_amount = f"{available_balance:.8f}".rstrip('0').rstrip('.')
                        # Prepare allocation request
                        nonce = str(int(time.time() * 1000))
                        path = "/0/private/Earn/Allocate"
                        post_data = {
                            "nonce": nonce,
                            "strategy_id": strategy['id'],
                            "amount": formatted_amount
                        }
                        url = self.client.rest_url + path
                        headers = {
                            "API-Key": self.client.api_key,
                            "API-Sign": self.client.get_kraken_signature(path, post_data)
                        }                        
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.post(url, headers=headers, data=post_data) as response:
                                    result = await response.json()                                    
                                    if result.get('error'):
                                        Logger.error(f"Error allocating {asset} to earn: {result['error']}")
                                    else:
                                        Logger.success(f"EARN: 200 - OK")
                        except Exception as e:
                            Logger.error(f"Error during earn allocation for {asset}: {str(e)}")
        else:
            await self.place_orders(trading_pair)
            return None
        # If multiple buy orders exist (shouldn't happen), keep most recent
        if len(open_orders) > 1:
            Logger.warning(f"ORDER: TOO MANY {trading_pair} ORDERS")
            kept_order = sorted(open_orders, key=lambda x: x.get('time', 0), reverse=True)[0]
            for order in open_orders:
                if order['order_id'] != kept_order['order_id']:
                    await self.cancel_order(order['order_id'])
            return kept_order
        # Return the single buy order
        return open_orders[0] if open_orders else None
    """
    Verifies if an open order is within acceptable grid interval.
    
    Args:
        trading_pair (str): Trading pair to check
        open_order (dict): Order details to verify
        
    Returns:
        bool: True if order is within interval, False otherwise
    """
    async def check_open_orders_open_order_interval(self, trading_pair: str, open_order: dict):
        """Check if an open order is still within the grid interval."""
        if not open_order:
            Logger.warning(f"No open order provided for {trading_pair}")
            return False  
        # Get current market data
        ticker = self.client.ticker_data.get(trading_pair)
        if not ticker or 'last' not in ticker:
            Logger.warning(f"No ticker data available for {trading_pair}")
            return False
        # Get current market price and order details
        current_market_price = float(ticker['last'])
        order_price = float(open_order.get('limit_price', 0))
        is_buy_order = open_order.get('side') == 'buy'
        if not order_price:
            Logger.warning(f"Could not determine limit price for order {open_order['order_id']}")
            return False
        # Get grid settings for this pair
        settings = TRADING_PAIRS[trading_pair]
        grid_interval = settings['grid_interval']
        trail_interval = settings['trail_interval']
        # Calculate percentage difference from current price
        price_difference = current_market_price - order_price
        percentage_difference = (price_difference / current_market_price) * 100
        # For buy orders, check if the order is too far from current market price
        if is_buy_order:
            # Calculate the maximum allowed difference using grid spacing
            max_allowed_difference = trail_interval + grid_interval
            # Order is too far below market price (exceeds grid spacing)
            if percentage_difference > max_allowed_difference:
                Logger.warning(f"ORDER: {trading_pair} - Current: ${current_market_price:.2f}, Order: ${order_price:.2f}")
                Logger.warning(f"ORDER: Price difference: {percentage_difference:.2f}% (max allowed: {max_allowed_difference:.2f}%)")
                # Set new price at exactly grid spacing below current price 
                target_price = current_market_price * (1 - (grid_interval / 100))
                # Add a small buffer (0.01%) to avoid rounding issues
                target_price = target_price * 1.0001
                await self.update_order_price(trading_pair, open_order, target_price)
                return False
        return True
    """
    Updates an existing order's price using the amend_order endpoint.
    
    Args:
        trading_pair (str): Trading pair of order
        order (dict): Order details to update
        new_price (float): New price for order
        
    Returns:
        bool: True if update successful, False otherwise
        
    Raises:
        Exception: If update fails
    """
    async def update_order_price(self, trading_pair: str, order: dict, new_price: float):
        if not order or not order.get('order_id'):
            Logger.warning("No valid order provided to update")
            return None
        for attempt in range(MAX_AMEND_RETRIES):
            try:
                # Format price based on trading pair precision
                formatted_price = self.format_price_for_pair(trading_pair, new_price)
                req_id = int(time.time() * 1000)
                amend_message = {
                    "method": "amend_order",
                    "params": {
                        "order_id": order['order_id'],
                        "limit_price": float(formatted_price),
                        "order_qty": float(order.get('order_qty', self.grid_settings[trading_pair]['buy_order_size'])),
                        "token": await self.client.get_ws_token()
                    },
                    "req_id": req_id
                }
                await self.client.websocket.send(json.dumps(amend_message))
                response = await self.client.wait_for_response(req_id, timeout=AMEND_RESPONSE_TIMEOUT)
                if response and response.get('success') is True:
                    Logger.success(f"ORDER: UPDATE PRICE - 200 - OK")
                    return True
                else:
                    if attempt < MAX_AMEND_RETRIES - 1:
                        Logger.warning(f"Amend attempt {attempt + 1} failed: {response}. Retrying in {AMEND_RETRY_DELAY} seconds...")
                        await asyncio.sleep(AMEND_RETRY_DELAY)
                    continue
            except Exception as e:
                if attempt < MAX_AMEND_RETRIES - 1:
                    Logger.warning(f"Error on amend attempt {attempt + 1}: {str(e)}. Retrying in {AMEND_RETRY_DELAY} seconds...")
                    await asyncio.sleep(AMEND_RETRY_DELAY)
                continue
        Logger.error(f"All {MAX_AMEND_RETRIES} amend attempts failed. Falling back to cancel/replace")
        await self.cancel_order(order['order_id'])
        await self.place_orders(trading_pair)
        return False
    """
    Places new grid orders for a trading pair.
    
    Args:
        trading_pair (str): Trading pair to place orders for
        
    Raises:
        Exception: If order placement fails
    """
    async def place_orders(self, trading_pair: str):
        """Execute the trade strategy for a trading pair."""
        # Check decay timer
        current_time = time.time()
        last_order_time = self.grid_settings[trading_pair]['last_order_time']
        if current_time - last_order_time < GRID_DECAY_TIME:
            time_left = GRID_DECAY_TIME - (current_time - last_order_time)
            return  
        ticker = self.client.ticker_data.get(trading_pair)
        if not ticker or 'last' not in ticker:
            Logger.error(f"No ticker data available for {trading_pair}")
            return
        # Update last order time before placing orders
        self.grid_settings[trading_pair]['last_order_time'] = current_time
        current_price = float(ticker['last'])
        grid_interval = self.grid_settings[trading_pair]['grid_interval']
        # Get buy amount from TRADING_PAIRS
        buy_amount = TRADING_PAIRS[trading_pair]['size']
        # Calculate optimal sell amount
        sell_amount = buy_amount * TRADING_PAIRS[trading_pair]['sell_multiplier']
        # Calculate grid prices
        interval_amount = current_price * (grid_interval / 100)
        buy_price = current_price - interval_amount
        sell_price = current_price + interval_amount
        # Format prices using the precision from TRADING_PAIRS configuration
        buy_price = self.format_price_for_pair(trading_pair, buy_price)
        sell_price = self.format_price_for_pair(trading_pair, sell_price) 
        try:
            # Generate request ID for buy order
            buy_req_id = int(time.time() * 1000)
            # Place buy order with correct buy_size
            buy_order_msg = {
                "method": "add_order",
                "params": {
                    "order_type": "limit",
                    "side": "buy",
                    "symbol": trading_pair,
                    "order_qty": buy_amount,
                    "limit_price": buy_price,
                    "token": await self.client.get_ws_token()
                },
                "req_id": buy_req_id
            }
            try:
                await self.client.websocket.send(json.dumps(buy_order_msg))
                buy_response = await self.client.wait_for_response(buy_req_id)
                if not buy_response:
                    Logger.error(f"No response received for buy order on {trading_pair}")
                    return    
                if not buy_response.get('success'):
                    error_msg = buy_response.get('error', 'Unknown error')
                    Logger.error(f"Buy order failed for {trading_pair}: {error_msg}")
                    return   
                order_id = buy_response.get('result', {}).get('order_id')
                if not order_id:
                    Logger.error(f"No order ID received for {trading_pair}")
                    return    
                self.grid_settings[trading_pair]['active_orders'].add(order_id)
                self.grid_orders[trading_pair]['buy'] = order_id
                Logger.success(f"ORDER: {trading_pair} ID: {order_id}")
                # Try to place corresponding sell order
                try:
                    sell_req_id = int(time.time() * 1000)
                    sell_order_msg = {
                        "method": "add_order",
                        "params": {
                            "order_type": "limit",
                            "side": "sell",
                            "symbol": trading_pair,
                            "order_qty": sell_amount,
                            "limit_price": sell_price,
                            "token": await self.client.get_ws_token()
                        },
                        "req_id": sell_req_id
                    }
                    await self.client.websocket.send(json.dumps(sell_order_msg))
                    sell_response = await self.client.wait_for_response(sell_req_id)
                    if sell_response and sell_response.get('success'):
                        sell_order_id = sell_response.get('result', {}).get('order_id')
                        if sell_order_id:
                            self.grid_orders[trading_pair]['sell'] = sell_order_id
                            Logger.success(f"ORDER: {trading_pair} ID: {sell_order_id}")
                        else:
                            Logger.warning(f"ORDER: Sell order placed but no ID received for {trading_pair}")
                    else:
                        pass
                except Exception as sell_error:
                    Logger.error(f"Error placing sell order for {trading_pair}: {str(sell_error)}")
            except Exception as send_error:
                Logger.error(f"Error sending buy order for {trading_pair}: {str(send_error)}")
        except Exception as e:
            Logger.error(f"Critical error placing orders for {trading_pair}: {str(e)}")
            # Reset the decay timer on error
            self.grid_settings[trading_pair]['last_order_time'] = 0

async def main():
    """Main function to establish WebSocket connection and manage subscriptions."""
    client = await KrakenWebSocketClient().initialize()
    grid_bot = KrakenGridBot(client)
    max_retries = 3
    retry_delay = 5
    retry_count = 0
    while True:
        try:
            # Attempt the WebSocket connection
            token = await client.connect()
            if not token:
                raise Exception("Failed to obtain connection token")
            # Set up WebSocket handlers
            client.set_handler('balances', client.handle_balance_updates)
            client.set_handler('executions', client.handle_execution_updates)
            client.set_handler('ticker', client.handle_ticker)
            # Subscribe to the channels we need
            await client.subscribe(['balances', 'executions'], token)
            # On successful connect/subscription, reset retry counter
            retry_count = 0
            # Start the grid bot
            await grid_bot.start()
            # Main trading loop
            while client.running:
                try:
                    all_orders_valid = True
                    for pair in TRADING_PAIRS:
                        current_order = await grid_bot.check_open_orders(pair)
                        if current_order:
                            order_valid = await grid_bot.check_open_orders_open_order_interval(pair, current_order)
                            all_orders_valid = all_orders_valid and order_valid
                    if all_orders_valid:
                        Logger.success("ORDERS: 200 - OK")
                    await asyncio.sleep(LONG_SLEEP_TIME)
                except websockets.exceptions.ConnectionClosed:
                    # If the connection drops in the trading loop, propagate to outer try/except
                    Logger.warning("Connection lost in trading loop, attempting to reconnect...")
                    raise
                except asyncio.CancelledError:
                    # If the task is cancelled externally, we re-raise so it stops gracefully
                    raise
                except Exception as e:
                    Logger.error(f"Error in trading loop: {str(e)}")
                    await asyncio.sleep(LONG_SLEEP_TIME)
        except (websockets.exceptions.ConnectionClosed, KrakenAPIError) as e:
            # We hit a connection-level issue or a Kraken-level error
            if retry_count < max_retries:
                retry_count += 1
                Logger.warning(f"Connection lost. Attempting reconnection {retry_count}/{max_retries}")
                # Clean up existing connection before retrying
                try:
                    await client.disconnect()
                except Exception as cleanup_error:
                    Logger.error(f"Error during cleanup: {str(cleanup_error)}")

                await asyncio.sleep(retry_delay)
                continue
            else:
                Logger.error("Max reconnection attempts reached. Exiting.")
                break
        except KeyboardInterrupt:
            # Graceful shutdown on Ctrl+C
            Logger.info("Keyboard interrupt received. Shutting down...")
            break
        except Exception as e:
            Logger.error(f"Unexpected error: {str(e)}\n{traceback.format_exc()}")
            if retry_count < max_retries:
                retry_count += 1
                await asyncio.sleep(retry_delay)
                continue
            else:
                Logger.error("Max error retries reached. Exiting.")
                break
    try:
        await client.disconnect()
    except Exception:
        pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        Logger.info("\nKeyboardInterrupt received. Exiting...")
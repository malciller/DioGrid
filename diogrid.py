import os
import json
import time
import hmac
import base64
import hashlib
import asyncio
import aiohttp
import websockets
import urllib.parse
from dotenv import load_dotenv


# TRADING CONFIGURATION
PASSIVE_INCOME = 0 # 0 = accumulation trading, 1 = take profit in USDC
KRAKEN_FEE = 0.002 # current Kraken maker fee
STARTING_PORTFOLIO_INVESTMENT = 2700.0 # Starting USD portfolio balance
PROFIT_INCREMENT = 10 # Profit taking increment in USDC, ignored if PASSIVE_INCOME = 0
SELL_AMOUNT_MULTIPLIER = 0.999 # Multiplier for sell order size
TRADING_PAIRS = {
    pair: {
        'size': size,
        'grid_interval': grid,
        'grid_spacing': spacing,
        'trail_interval': spacing,
        'precision': precision
    }
    for pair, (size, grid, spacing, precision) in {
        "BTC/USD": (0.00085, 0.75, 0.75, 1), # $80.00 @ 3.2x (0.00025 @ 1.0x @ $100k)
        "SOL/USD": (0.06, 1.5, 1.5, 2), # 13-16%      
        "XRP/USD": (5.0, 2.5, 2.5, 5), # 0%          
        "ADA/USD": (18.0, 3.5, 3.5, 6), # 2-5% 
        "ETH/USD": (0.0045, 3.5, 3.5, 2), # 2-7%   
        "TRX/USD": (55.0, 2.5, 2.5, 6), # 4-7%      
        "DOT/USD": (2.5, 2.5, 2.5, 4), # 12-18% 
        "KSM/USD": (0.6, 2.5, 2.5, 2), # 16-24%
        "INJ/USD": (0.81, 2.5, 2.5, 3), # 7-11%
    }.items()
}



# SLEEP TIMES
SHORT_SLEEP_TIME = 0.1
LONG_SLEEP_TIME = 3
GRID_DECAY_TIME = 5  # 5 second decay time



load_dotenv()


class Logger:
    ERROR = '\033[91m'    # Red
    WARNING = '\033[93m'  # Yellow
    INFO = '\033[94m'     # Blue
    SUCCESS = '\033[92m'  # Green
    RESET = '\033[0m'     # Reset color

    @staticmethod
    def error(msg: str, exc_info: Exception = None):
        error_msg = f"{Logger.ERROR}[ERROR] {msg}{Logger.RESET}"
        if exc_info:
            error_msg += f"\n{Logger.ERROR}Exception: {str(exc_info)}{Logger.RESET}"
        print(error_msg)

    @staticmethod
    def warning(msg: str):
        print(f"{Logger.WARNING}[WARNING] {msg}{Logger.RESET}")

    @staticmethod
    def info(msg: str):
        print(f"{Logger.INFO}[INFO] {msg}{Logger.RESET}")

    @staticmethod
    def success(msg: str):
        print(f"{Logger.SUCCESS}[SUCCESS] {msg}{Logger.RESET}")

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
        self.ws_auth_url = "wss://ws-auth.kraken.com/v2"  # For private data
        self.ws_public_url = "wss://ws.kraken.com/v2"     # For public data
        self.rest_url = "https://api.kraken.com"
        self.websocket = None
        self.public_websocket = None  # Add new websocket connection
        self.running = True
        # Only track private connection status
        self.connection_status = {
            'private': {'last_ping': time.time(), 'last_pong': time.time()},
        }
        self.last_ticker_time = time.time()  # Track last ticker message
        self.ping_interval = 30
        self.pong_timeout = 10  # Time to wait for pong response
        self.reconnect_delay = 5  # Seconds to wait before reconnecting
        self.max_reconnect_attempts = 3
        self.execution_rate_limit = None
        self.subscriptions = {}
        self.handlers = {}
        self.balances = {}
        self.orders = {}
        self.maintenance_task = None
        self.message_task = None
        self.ticker_data = {}
        self.active_trading_pairs = set()  # Track active trading pairs
        self.portfolio_value = 0.0
        self.last_portfolio_update = 0
        self.update_interval = 5  # Update portfolio value every 5 seconds
        self.last_profit_take_time = 0
        self.profit_take_cooldown = 300  # 5 minutes in seconds
        self.highest_portfolio_value = STARTING_PORTFOLIO_INVESTMENT
        self.ticker_subscriptions = {}  # Track individual ticker subscriptions
        self.public_message_task = None
        self.earn_strategies = {}  # Store strategy details by asset
        self.strategy_ids = {}     # Store strategy IDs by asset
        self.earn_balances = {}  
        self.subscribed_channels = set()  # Track which channels are already subscribed
        self.subscription_lock = asyncio.Lock()  # Add lock for thread-safe subscription management
        self.subscription_retry_delay = 1  # Delay between subscription attempts

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
    async def connect(self):
        """Connect to Kraken's WebSocket APIs."""
        try:
            # Get WebSocket token and connect
            token = await self.get_ws_token()
            if not token:
                raise KrakenAPIError('EAPI:Invalid key', 'Failed to obtain WebSocket token')
                
            # Fetch earn strategies before establishing connections
            await self.get_earn_strategies()
            
            self.websocket = await websockets.connect(self.ws_auth_url)
            self.public_websocket = await websockets.connect(self.ws_public_url)
            
            # Start maintenance tasks
            self.maintenance_task = asyncio.create_task(self.maintain_connection())
            self.message_task = asyncio.create_task(self.handle_messages())
            self.public_message_task = asyncio.create_task(self.handle_public_messages())
            
            return token
            
        except Exception as e:
            Logger.error(f"Error during connection: {str(e)}")
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

                status = self.connection_status['private']
                time_since_pong = current_time - status['last_pong']
                
                if current_time - status['last_ping'] >= self.ping_interval:
                    await self.ping()
                    status['last_ping'] = current_time
                
                if time_since_pong > self.ping_interval + self.pong_timeout:
                    Logger.warning(f"Missing pong response for private connection (last pong was {time_since_pong:.1f}s ago)")
                    needs_reconnect = True

                time_since_ticker = current_time - self.last_ticker_time
                if time_since_ticker > self.ping_interval + self.pong_timeout:
                    Logger.warning(f"No ticker data received for {time_since_ticker:.1f}s")
                    needs_reconnect = True

                if needs_reconnect:
                    if reconnect_attempts < self.max_reconnect_attempts:
                        reconnect_attempts += 1
                        Logger.warning(f"Connection lost. Attempting reconnection (attempt {reconnect_attempts}/{self.max_reconnect_attempts})")
                        await self.reconnect()
                        continue
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
        """Reconnect to WebSocket endpoints."""
        #Logger.info("Reconnecting to WebSocket...")
        
        # Close existing connections
        if self.websocket:
            await self.websocket.close()
        if self.public_websocket:
            await self.public_websocket.close()
            
        # Wait before reconnecting
        await asyncio.sleep(self.reconnect_delay)
        
        try:
            # Reconnect and resubscribe
            token = await self.connect()
            await self.subscribe(['balances', 'executions'], token)
            
            # Resubscribe to active trading pairs
            if self.active_trading_pairs:
                await self.subscribe_ticker(list(self.active_trading_pairs))
            
            Logger.success("Successfully reconnected to WebSocket")
            
        except Exception as e:
            Logger.error("Error during reconnection", e)
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
        try:
            while self.running:
                message = await self.websocket.recv()
                await self.handle_message(message)
        except asyncio.CancelledError:
            pass
        except websockets.exceptions.ConnectionClosed:
            Logger.warning("WebSocket connection closed")
            self.running = False
        except Exception as e:
            Logger.error("Error in message handling", e)
            self.running = False

    """
    Processes incoming messages from the public WebSocket connection.
    Handles ticker updates and heartbeat messages.
    
    Raises:
        websockets.exceptions.ConnectionClosed: If connection is lost
        Exception: For other processing errors
    """
    async def handle_public_messages(self):
        """Handle messages from public WebSocket."""
        try:
            while self.running:
                message = await self.public_websocket.recv()
                data = json.loads(message)
                
                if isinstance(data, dict) and data.get('event') == 'error':
                    Logger.error(f"WebSocket error: {data.get('error')}")
                    continue
                
                # Handle other message types
                if data.get('channel') == 'heartbeat':
                    continue
                if data.get('channel') == 'status':
                    continue
                if isinstance(data, dict) and data.get('method') in ['subscribe', 'unsubscribe']:
                    if not data.get('success') and data.get('error'):
                        Logger.error(f"WebSocket subscription error: {data.get('error')}")
                    continue
                
                if data.get('channel') == 'ticker':
                    await self.handle_ticker(data)
        except asyncio.CancelledError:
            pass
        except websockets.exceptions.ConnectionClosed:
            Logger.warning("Public WebSocket connection closed")
        except Exception as e:
            Logger.error("Error in public message handling", e)

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
                    if 'Already subscribed' not in error_msg:  # Only log non-duplicate subscription errors
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
                #Logger.info(f"Order edit response: {data}")
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
                    #Logger.info(f"Already subscribed to {channel}, skipping...")
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
                    await asyncio.sleep(self.subscription_retry_delay)  # Add delay between subscriptions
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
        current_time = time.strftime('%H:%M:%S')
        Logger.info(f"Portfolio Value: ${total_value:,.2f} ({current_time})")
        
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
                    "cash_order_qty": PROFIT_INCREMENT,  # Buy $5 worth of USDC
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
            
            # Process all balances in the update
            #Logger.info(f"Balance update received - Type: {data.get('type')}")
            #Logger.info(f"Raw balance data: {json.dumps(data, indent=2)}")
            
            for asset_data in data.get('data', []):
                asset_code = asset_data.get('asset')
                
                # Reset earn balance for this asset
                self.earn_balances[asset_code] = 0.0
                
                # Process wallets
                wallets = asset_data.get('wallets', [])
                total_balance = 0.0
                
                if wallets:
                    # If we have wallet data, process each wallet
                    #Logger.info(f"Processing wallets for {asset_code}:")
                    for wallet in wallets:
                        wallet_type = wallet.get('type')
                        wallet_balance = float(wallet.get('balance', 0))
                        #Logger.info(f"  Wallet type: {wallet_type}, Balance: {wallet_balance:.8f}")
                        
                        # Store earn wallet balances separately
                        if wallet_type in ['earn', 'bonded', 'locked']:
                            self.earn_balances[asset_code] = wallet_balance
                        
                        # Add to total balance regardless of wallet type
                        total_balance += wallet_balance
                else:
                    # Fallback to legacy format if no wallets field
                    total_balance = float(asset_data.get('balance', 0))
                    #Logger.info(f"Legacy format for {asset_code} - Balance: {total_balance:.8f}")
                
                # Update total balance
                self.balances[asset_code] = total_balance
                #Logger.info(f"Updated total balance for {asset_code}: {total_balance:.8f}")
                
                # Add to tracking set if non-zero balance and not USD
                if total_balance > 0 and asset_code != 'USD':
                    assets_with_balance.add(asset_code)
            
            # Now check all known balances for non-zero amounts
            # This ensures we don't lose tracking of assets not included in this update
            for asset_code, balance in self.balances.items():
                if balance > 0 and asset_code != 'USD':
                    assets_with_balance.add(asset_code)
            
            #Logger.info(f"Assets with non-zero balances: {assets_with_balance}")
            
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
                    #Logger.info(f"Already subscribed to ticker for {symbol}, skipping...")
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
                    await asyncio.sleep(self.subscription_retry_delay)  # Add delay between subscriptions
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
                #Logger.info(f"Unsubscribed from ticker for: {symbol}")
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
            self.last_ticker_time = time.time()  # Update ticker timestamp
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
                #print(f"TICKER: {symbol} Last={ticker_data.get('last')} Bid={ticker_data.get('bid')} Ask={ticker_data.get('ask')}")
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
                        #Logger.info(f"Order {order_id} for {removed_order.get('symbol')} {exec_type}")
                elif exec_type in ['new', 'pending_new']:
                    if order_id in self.orders:
                        self.orders[order_id].update(execution)
                    else:
                        self.orders[order_id] = execution
                        Logger.info(f"New order {order_id} for {symbol}")
                elif order_id in self.orders:
                    self.orders[order_id].update(execution)

    """
    Formats and prints execution details for logging purposes.
    
    Args:
        execution (dict): Execution data to print
    """
    def print_execution(self, execution):
        """Print execution details."""
        exec_type = execution.get('exec_type')
        if exec_type in ['trade', 'filled']:
            print(f"ORDER: Trade {execution.get('symbol')} {execution.get('side')} {execution.get('last_qty')}@{execution.get('last_price')} ID={execution.get('order_id')} Status={execution.get('order_status')}")
        else:
            # Filter out fields that are N/A
            details = {
                'symbol': execution.get('symbol'),
                'side': execution.get('side'),
                'qty': execution.get('order_qty'),
                'price': execution.get('limit_price'),
                'status': execution.get('order_status'),
                'id': execution.get('order_id')
            }
            # Remove None or N/A values
            details = {k: v for k, v in details.items() if v not in [None, 'N/A']}
            details_str = ' '.join(f"{k}={v}" for k, v in details.items())
            #print(f"ORDER: {details_str}")

    """
    Processes execution messages for order updates.
    Maintains order state and handles various execution types.
    
    Args:
        message (dict): Execution message from WebSocket
    """
    async def handle_execution_message(self, message):
        """Handle execution messages for order updates."""
        if message['type'] == 'snapshot':
            self.orders = {}
            for execution in message['data']:
                if execution['order_status'] not in ['filled', 'canceled', 'expired']:
                    self.orders[execution['order_id']] = execution
        
        elif message['type'] == 'update':
            for execution in message['data']:
                order_id = execution['order_id']
                
                if execution['exec_type'] in ['filled', 'canceled', 'expired']:
                    # Remove completed orders
                    self.orders.pop(order_id, None)
                else:
                    # Update or add order
                    if order_id in self.orders:
                        self.orders[order_id].update(execution)
                    else:
                        self.orders[order_id] = execution

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

    async def get_earn_strategies(self):
        """Fetch available earn strategies from Kraken."""
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
                            
                    #Logger.info(f"Fetched {len(self.earn_strategies)} earn strategies")
                    #Logger.info("Available earn strategies:")
                    for asset, details in self.earn_strategies.items():
                        apr_range = details.get('apr_estimate', {})
                        apr_low = apr_range.get('low', 'N/A')
                        apr_high = apr_range.get('high', 'N/A')
                        #Logger.info(f"{asset}: ID={details['id']}, APR={apr_low}-{apr_high}%, Type={details['lock_type']}")
                    
                    return self.earn_strategies

        except aiohttp.ClientError as e:
            raise KrakenAPIError('EService:Unavailable', f"HTTP request failed: {str(e)}")
        except Exception as e:
            raise KrakenAPIError('EGeneral:Internal error', str(e))

    async def get_available_spot_balance(self, asset: str) -> float:
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
        try:
            # Get total balance for the asset
            total_balance = self.balances.get(asset, 0.0)
            if total_balance <= 0:
                return 0.0

            # Calculate amount in open sell orders
            amount_in_orders = 0.0
            for order in self.orders.values():
                if (order.get('symbol', '').startswith(asset + '/') and 
                    order.get('side') == 'sell' and
                    order.get('order_status') in ['new', 'partially_filled']):
                    order_qty = float(order.get('order_qty', 0.0))
                    amount_in_orders += order_qty

            # Get amount in earn wallet
            amount_in_earn = self.earn_balances.get(asset, 0.0)

            # Calculate available balance
            available_balance = total_balance - amount_in_orders - amount_in_earn
            
            # Format numbers for consistent decimal places in logging
            #Logger.info(f"\nBalance Calculation for {asset}:")
            #Logger.info(f"  Total Balance:     {total_balance:.8f}")
            #Logger.info(f"  - Sell Orders:     {amount_in_orders:.8f}")
            #Logger.info(f"  - Earn Balance:    {amount_in_earn:.8f}")
            #Logger.info(f"  = Available:       {max(0.0, available_balance):.8f}")
            
            if amount_in_orders > 0:
                #Logger.info("\n  Sell Orders Detail:")
                for order in self.orders.values():
                    if (order.get('symbol', '').startswith(asset + '/') and 
                        order.get('side') == 'sell' and
                        order.get('order_status') in ['new', 'partially_filled']):
                        order_qty = float(order.get('order_qty', 0.0))
                        #Logger.info(f"    Order {order.get('order_id')}: {order_qty:.8f}")

            return max(0.0, available_balance)

        except Exception as e:
            Logger.error(f"Error calculating available balance for {asset}: {str(e)}")
            return 0.0


class KrakenGridBot:
    def __init__(self, client: KrakenWebSocketClient):
        self.client = client
        self.active_grids = {}  # Dictionary to track active grid trades per trading pair
        self.grid_settings = {
            pair: {
                'buy_order_size': settings['size'],
                'sell_order_size': settings['size'],
                'grid_interval': settings['grid_interval'],
                'trail_interval': settings['trail_interval'],
                'active_orders': set(),  # Track order IDs for this grid
                'last_order_time': 0  # Add tracking for last order time
            }
            for pair, settings in TRADING_PAIRS.items()
        }
        self.grid_orders = {
            pair: {'buy': None, 'sell': None} 
            for pair in TRADING_PAIRS.keys()
        }

    def format_price_for_pair(self, trading_pair: str, price: float) -> float:
        """Format price with appropriate precision for each trading pair."""
        # Get precision from trading pair settings, default to 2 if not found
        precision = TRADING_PAIRS.get(trading_pair, {}).get('precision', 2)
        
        # Format the price with the specified precision
        formatted_price = float(f"{price:.{precision}f}")
        
        #Logger.info(f"Formatted {trading_pair} price from {price} to {formatted_price} with {precision} decimals")
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
        #Logger.info(f"DEBUG: Subscribing to trading pairs: {trading_pairs}")
        await self.client.subscribe_ticker(trading_pairs)
        
        #Logger.info("Starting grid bot monitoring...")
    
    """
    Calculates buy price for a trading pair based on grid settings.
    
    Args:
        trading_pair (str): Trading pair symbol
        
    Returns:
        float: Calculated buy price
        None: If price data unavailable
    """
    async def get_buy_price(self, trading_pair: str) -> float:
        # Get current ticker data for the trading pair
        ticker = self.client.ticker_data.get(trading_pair)
        if not ticker or 'last' not in ticker:
            Logger.warning(f"No ticker data available for {trading_pair}")
            return None
            
        # Get current price from last trade
        current_price = float(ticker['last'])
        
        # Calculate grid interval in absolute terms
        interval_amount = current_price * (self.grid_settings[trading_pair]['grid_interval']/ 100)
        
        # Calculate buy price (current price minus interval)
        buy_price = current_price - interval_amount
        
        #Logger.info(f"Grid price for {trading_pair}: Current=${current_price:.2f}, Buy=${buy_price:.2f}, Interval=${interval_amount:.2f}")
        return buy_price

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
                "order_id": [order_id],  # API expects an array of order IDs
                "token": await self.client.get_ws_token()
            },
            "req_id": int(time.time() * 1000)
        }
        
        try:
            await self.client.websocket.send(json.dumps(cancel_message))
            #Logger.info(f"Sent cancel request for order {order_id}")
            
            # Wait briefly for the cancel to process
            await asyncio.sleep(LONG_SLEEP_TIME)
            
            # Verify the order was removed from client.orders
            if order_id not in self.client.orders:
                Logger.success(f"Successfully canceled order {order_id}")
            else:
                Logger.warning(f"Warning: Order {order_id} may not have been canceled")
                
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
        
        # Skip if this asset isn't in both TRADING_PAIRS and earn strategies
        if trading_pair not in TRADING_PAIRS or asset not in self.client.earn_strategies:
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
                                    Logger.success(f"Successfully initiated earn allocation for {formatted_amount} {asset}")
                    except Exception as e:
                        Logger.error(f"Error during earn allocation for {asset}: {str(e)}")
                else:
                    if not strategy.get('can_allocate'):
                        Logger.warning(f"{asset} earn strategy not currently accepting allocations")
                    elif available_balance > 0:  # Only log if there's actually a balance
                        #Logger.info(f"{asset} available balance ({available_balance:.8f} = ${available_balance_usd:.2f}) below minimum allocation (${min_allocation:.2f})")
                        pass

        else:
            await self.place_orders(trading_pair)
            return None

        # If multiple buy orders exist (shouldn't happen), keep most recent
        if len(open_orders) > 1:
            Logger.warning(f"Warning: Found {len(open_orders)} buy orders for {trading_pair}")
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

        # Calculate grid parameters using asset-specific intervals
        grid_interval = self.grid_settings[trading_pair]['grid_interval']
        trail_interval = self.grid_settings[trading_pair]['trail_interval']
        
        # Calculate the maximum allowed distance for order validity check
        max_interval = current_market_price * ((grid_interval + trail_interval) / 100)
        
        # For buy orders, check if the order is too far from current market price
        if is_buy_order:
            price_difference = current_market_price - order_price
            
            # Order is too far below market price (exceeds grid + trail interval)
            if price_difference > max_interval:
                Logger.warning(f"ORDER: Market Price: ${current_market_price:.2f}, Max Interval: ${max_interval:.2f}, Price Difference: ${price_difference:.2f}, Unacceptable")
                # Set new price at exactly grid interval below current price (not max interval)
                target_price = current_market_price - (current_market_price * grid_interval / 100)
                await self.update_order_price(trading_pair, open_order, target_price)
                return False
            else:
                # Log that order is within acceptable range
                #Logger.info(f"ORDER: Market Price: ${current_market_price:.2f}, Max Interval: ${max_interval:.2f}, Price Difference: ${price_difference:.2f}, Acceptable")
                pass
        
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
        
        try:
            # Format price based on trading pair precision
            formatted_price = self.format_price_for_pair(trading_pair, new_price)
            
            req_id = int(time.time() * 1000)
            
            # Create amend order message with only supported parameters
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
            
            # Send amend order and wait for response
            await self.client.websocket.send(json.dumps(amend_message))
            
            # Wait for response
            response = await self.client.wait_for_response(req_id)
            if response and response.get('success') is True:
                Logger.success(f"ORDER: Successfully updated order price to ${formatted_price}")
                return True
            else:
                Logger.warning(f"Failed to update order price: {response}")
                # If amend fails, cancel the order and place a new one
                await self.cancel_order(order['order_id'])
                await self.place_orders(trading_pair)
                return False
            
        except Exception as e:
            Logger.error(f"Error updating order price: {str(e)}")
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
            #Logger.info(f"Decay timer active for {trading_pair}. {time_left:.1f}s remaining")
            return
            
        #Logger.info(f"Executing trade strategy for {trading_pair}")
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
        sell_amount = buy_amount * SELL_AMOUNT_MULTIPLIER
        
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
            
            #Logger.info(f"Placing buy order for {trading_pair} - Price: ${buy_price:.2f}, Quantity: {buy_amount}")
            
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
                Logger.success(f"Grid buy order placed for {trading_pair} with ID: {order_id}")
                
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
                    
                    #Logger.info(f"Placing sell order for {trading_pair} - Price: ${sell_price:.2f}, Quantity: {sell_amount}")
                    
                    await self.client.websocket.send(json.dumps(sell_order_msg))
                    sell_response = await self.client.wait_for_response(sell_req_id)
                    
                    if sell_response and sell_response.get('success'):
                        sell_order_id = sell_response.get('result', {}).get('order_id')
                        if sell_order_id:
                            self.grid_orders[trading_pair]['sell'] = sell_order_id
                            Logger.success(f"Grid sell order placed for {trading_pair} with ID: {sell_order_id}")
                        else:
                            Logger.warning(f"Sell order placed but no ID received for {trading_pair}")
                    else:
                        #Logger.info(f"Could not place sell order for {trading_pair} (likely insufficient funds)")
                        pass
                        
                except Exception as sell_error:
                    Logger.error(f"Error placing sell order for {trading_pair}: {str(sell_error)}")
                    
            except Exception as send_error:
                Logger.error(f"Error sending buy order for {trading_pair}: {str(send_error)}")
                
        except Exception as e:
            Logger.error(f"Critical error placing orders for {trading_pair}: {str(e)}")
            # Reset the decay timer on error
            self.grid_settings[trading_pair]['last_order_time'] = 0

    """
    Calculates the optimal sell amount to ensure minimum profit after fees.
    
    Args:
        trading_pair (str): Trading pair symbol
        buy_amount (float): Buy amount for the trading pair
        current_price (float): Current price of the trading pair
        
    Returns:
        float: Optimal sell amount
    """
    async def calculate_optimal_sell_amount(self, trading_pair: str, buy_amount: float, current_price: float) -> float:
        """Calculate sell amount - will sell 100% of bought amount."""
        grid_interval = self.grid_settings[trading_pair]['grid_interval']
        trail_interval = self.grid_settings[trading_pair]['trail_interval']
        interval_amount = current_price * (grid_interval / 100)
        
        # Calculate prices
        buy_price = current_price - ((current_price * trail_interval)/100)
        sell_price = current_price + interval_amount
        
        # Calculate total cost of buy (including fees)
        buy_cost = buy_amount * buy_price
        buy_fee = buy_cost * KRAKEN_FEE
        total_buy_cost = buy_cost + buy_fee
        
        # Calculate expected sell revenue and profit
        sell_revenue = buy_amount * sell_price
        sell_fee = sell_revenue * KRAKEN_FEE
        expected_profit = sell_revenue - sell_fee - total_buy_cost
        
        #Logger.info(f"\nCalculated sell parameters for {trading_pair}:")
        #Logger.info(f"Buy amount: {buy_amount}")
        #Logger.info(f"Buy price: ${buy_price:.2f}")
        #Logger.info(f"Sell price: ${sell_price:.2f}")
        #Logger.info(f"Total buy cost (inc. fee): ${total_buy_cost:.4f}")
        #Logger.info(f"Expected sell revenue: ${sell_revenue:.4f}")
        #Logger.info(f"Total fees: ${(buy_fee + sell_fee):.4f}")
        #Logger.info(f"Expected profit: ${expected_profit:.4f}")
        
        # Return the full buy amount as the sell amount
        return buy_amount


"""
Main function to run the grid trading bot.
Establishes connections and manages the trading loop.

Raises:
    Exception: If fatal error occurs during execution
"""
async def main():
    """Main function to establish WebSocket connection and manage subscriptions."""
    client = KrakenWebSocketClient()
    grid_bot = KrakenGridBot(client)  # Create a single instance of KrakenGridBot

    try:
        # Connect and set up WebSocket handlers
        token = await client.connect()
        client.set_handler('balances', client.handle_balance_updates)
        client.set_handler('executions', client.handle_execution_updates)
        client.set_handler('ticker', client.handle_ticker)
        await client.subscribe(['balances', 'executions'], token)

        # Start the grid bot
        await grid_bot.start()

        while client.running:
            try:
                for pair in TRADING_PAIRS:
                    # Check if we have valid orders within our grid
                    current_order = await grid_bot.check_open_orders(pair)
                    if current_order:
                        # If we have an order, check if it's still valid
                        is_valid = await grid_bot.check_open_orders_open_order_interval(pair, current_order)
                        #Logger.info(f"ORDER: validity for {pair}: {is_valid}")
                        pass
                    
                await asyncio.sleep(LONG_SLEEP_TIME)
            except asyncio.CancelledError:
                break
    except KrakenAPIError as e:
        Logger.error(f"Kraken API error: {e.error_code} - {e.message}")
    except Exception as e:
        Logger.error(f"Unexpected error: {str(e)}")
    finally:
        #Logger.info("\nGracefully shutting down...")
        try:
            # First unsubscribe from all channels if connection is still open
            if client.websocket:
                await client.unsubscribe(['balances', 'executions'])
                if client.active_trading_pairs: 
                    await client.unsubscribe_ticker(list(client.active_trading_pairs))
                # Wait briefly for unsubscribe confirmations
                await asyncio.sleep(LONG_SLEEP_TIME)
            # Then disconnect
            await client.disconnect()
            Logger.success("Successfully disconnected from all Kraken WebSocket streams.")
        except Exception as e:
            Logger.error(f"Error during shutdown: {str(e)}")
            # Optionally, add more detailed error information for debugging
            import traceback
            Logger.error(f"Shutdown error details:\n{traceback.format_exc()}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        Logger.info("\nKeyboardInterrupt received. Exiting...")
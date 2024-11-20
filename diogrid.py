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
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

PROFIT_USD_TARGET = 0.02
KRAKEN_FEE = 0.002
STARTING_PORTFOLIO_INVESTMENT = 500.0
PROFIT_INCREMENT = 5
GRID_INTERVAL_GRACE = 0.05
TRADING_PAIRS = {
    "BTC/USD": {
        'size': 0.0001,
        'grid_interval': 0.75
    },
    "SOL/USD": {
        'size': 0.04,
        'grid_interval': 2.0
    },
}
SHORT_SLEEP_TIME = 0.1
LONG_SLEEP_TIME = 3
GRID_DECAY_TIME = 5  # 5 second decay time



load_dotenv()

class KrakenAPIError(Exception):
    """Custom exception class for Kraken API errors"""

    """
    Initializes a new DioGridError instance.
    
    Args:
        error_code (str): The error code identifier
        message (str, optional): Custom error message. If not provided, 
                               a default message is fetched based on the error code.
    """
    def __init__(self, error_code, message=None):
        self.error_code = error_code
        self.message = message or self._get_error_description(error_code)
        super().__init__(f"{error_code}: {self.message}")

    def _get_error_description(self, error_code):
        error_descriptions = {
            # General Errors
            'EGeneral:Invalid arguments': 'The request payload is malformed, incorrect or ambiguous',
            'EGeneral:Invalid arguments:Index unavailable': 'Index pricing is unavailable for stop/profit orders on this pair',
            'EGeneral:Temporary lockout': 'Too many sequential EAPI:Invalid key errors',
            'EGeneral:Permission denied': 'API key lacks required permissions',
            'EGeneral:Internal error': 'Internal error. Please contact support',

            # Service Errors
            'EService:Unavailable': 'The matching engine or API is offline',
            'EService:Market in cancel_only mode': 'Request cannot be made at this time',
            'EService:Market in post_only mode': 'Request cannot be made at this time',
            'EService:Deadline elapsed': 'The request timed out according to the default or specified deadline',

            # API Authentication Errors
            'EAPI:Invalid key': 'Invalid API key provided',
            'EAPI:Invalid signature': 'Invalid API signature',
            'EAPI:Invalid nonce': 'Invalid nonce value',

            # Order Errors
            'EOrder:Cannot open opposing position': 'User/tier is ineligible for margin trading',
            'EOrder:Cannot open position': 'User/tier is ineligible for margin trading',
            'EOrder:Margin allowance exceeded': 'User has exceeded their margin allowance',
            'EOrder:Margin level too low': 'Client has insufficient equity or collateral',
            'EOrder:Margin position size exceeded': 'Client would exceed the maximum position size for this pair',
            'EOrder:Insufficient margin': 'Exchange does not have available funds for this margin trade',
            'EOrder:Insufficient funds': 'Client does not have the necessary funds',
            'EOrder:Order minimum not met': 'Order size does not meet ordermin',
            'EOrder:Cost minimum not met': 'Cost (price * volume) does not meet costmin',
            'EOrder:Tick size check failed': 'Price submitted is not a valid multiple of the pair\'s tick_size',
            'EOrder:Orders limit exceeded': 'Order rate limit exceeded',
            'EOrder:Rate limit exceeded': 'Rate limit exceeded',
            'EOrder:Invalid price': 'Invalid price specified',
            'EOrder:Domain rate limit exceeded': 'Domain-specific rate limit exceeded',
            'EOrder:Positions limit exceeded': 'Maximum positions limit exceeded',
            'EOrder:Reduce only:Non-PC': 'Invalid reduce-only order',
            'EOrder:Reduce only:No position exists': 'Cannot submit reduce-only order when no position exists',
            'EOrder:Reduce only:Position is closed': 'Reduce-only order would flip position',
            'EOrder:Scheduled orders limit exceeded': 'Maximum scheduled orders limit exceeded',
            'EOrder:Unknown position': 'Position not found',

            # Account Errors
            'EAccount:Invalid permissions': 'Account has invalid permissions',

            # Authentication Errors
            'EAuth:Account temporary disabled': 'Account is temporarily disabled',
            'EAuth:Account unconfirmed': 'Account is not confirmed',
            'EAuth:Rate limit exceeded': 'Authentication rate limit exceeded',
            'EAuth:Too many requests': 'Too many authentication requests',

            # Trade Errors
            'ETrade:Invalid request': 'Invalid trade request',

            # Business/Regulatory Errors
            'EBM:limit exceeded:CAL': 'Exceeded Canadian Acquisition Limits',

            # Funding Errors
            'EFunding:Max fee exceeded': 'Processed fee exceeds max_fee set in Withdraw request'
        }
        return error_descriptions.get(error_code, 'Unknown error')


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
        self.email_manager = EmailManager()

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
            # Connect to authenticated endpoint
            token = await self.get_ws_token()
            if not token:
                raise KrakenAPIError('EAPI:Invalid key', 'Failed to obtain WebSocket token')
            self.websocket = await websockets.connect(self.ws_auth_url)
            
            # Connect to public endpoint
            self.public_websocket = await websockets.connect(self.ws_public_url)
            
            # Start maintenance tasks
            self.maintenance_task = asyncio.create_task(self.maintain_connection())
            self.message_task = asyncio.create_task(self.handle_messages())
            self.public_message_task = asyncio.create_task(self.handle_public_messages())
            
            return token
        except Exception as e:
            print(f"Error during connection: {str(e)}")
            # Clean up any partially created tasks/connections
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
            print(f"Error during disconnect: {str(e)}")

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
                status = self.connection_status['private']
                time_since_pong = current_time - status['last_pong']
                
                if current_time - status['last_ping'] >= self.ping_interval:
                    #print(f"Sending ping for private connection")
                    await self.ping()
                    status['last_ping'] = current_time
                
                if time_since_pong > self.ping_interval + self.pong_timeout:
                    print(f"Warning: Missing pong response for private connection "
                          f"(last pong was {time_since_pong:.1f}s ago)")
                    needs_reconnect = True

                # Check public connection health via ticker data
                time_since_ticker = current_time - self.last_ticker_time
                if time_since_ticker > self.ping_interval + self.pong_timeout:
                    print(f"Warning: No ticker data received for {time_since_ticker:.1f}s")
                    needs_reconnect = True

                if needs_reconnect:
                    if reconnect_attempts < self.max_reconnect_attempts:
                        reconnect_attempts += 1
                        print(f"Connection lost. Attempting reconnection "
                              f"(attempt {reconnect_attempts}/{self.max_reconnect_attempts})")
                        await self.reconnect()
                        continue
                    else:
                        print("Error: Max reconnection attempts reached")
                        self.running = False
                        break

                await asyncio.sleep(LONG_SLEEP_TIME)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error in connection maintenance: {str(e)}")
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
            print(f"Error sending ping: {str(e)}")

    """
    Attempts to reestablish lost WebSocket connections.
    Handles reconnection to both private and public endpoints.
    
    Raises:
        Exception: If reconnection fails
    """
    async def reconnect(self):
        """Reconnect to WebSocket endpoints."""
        print("Reconnecting to WebSocket...")
        
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
            
            print("Successfully reconnected to WebSocket")
            
        except Exception as e:
            print(f"Error during reconnection: {str(e)}")
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
            print("WebSocket connection closed.")
            self.running = False
        except Exception as e:
            print(f"Error in message handling: {e}")
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
                
                # Handle pong responses
                if isinstance(data, dict) and (data.get('event') == 'pong' or data.get('method') == 'pong'):
                    self.connection_status['public']['last_pong'] = time.time()
                    return
                
                # Handle other message types
                if data.get('channel') == 'heartbeat':
                    continue
                if data.get('channel') == 'status':
                    continue
                if isinstance(data, dict) and data.get('method') in ['subscribe', 'unsubscribe']:
                    if not data.get('success') and data.get('error'):
                        print(f"WebSocket error: {data.get('error')}")
                    continue
                
                if data.get('channel') == 'ticker':
                    await self.handle_ticker(data)
        except asyncio.CancelledError:
            pass
        except websockets.exceptions.ConnectionClosed:
            print("Public WebSocket connection closed.")
        except Exception as e:
            print(f"Error in public message handling: {e}")

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
            
            # Check if this is a response to a pending request
            if isinstance(data, dict) and 'req_id' in data:
                req_id = data['req_id']
                if hasattr(self, '_response_futures') and req_id in self._response_futures:
                    if not self._response_futures[req_id].done():
                        self._response_futures[req_id].set_result(data)
                    return
            
            # Handle other message types as before
            if isinstance(data, dict) and (data.get('event') == 'pong' or data.get('method') == 'pong'):
                self.connection_status['private']['last_pong'] = time.time()
                return
            
            # Handle edit_order responses
            if isinstance(data, dict) and data.get('method') == 'edit_order':
                print(f"Received edit_order response: {data}")
                return
            
            if 'channel' in data:
                channel = data['channel']
                if channel in self.handlers:
                    await self.handlers[channel](data)
            elif 'event' in data:
                # Only print important system status events
                if data.get('event') == 'systemStatus' and data.get('status') != 'online':
                    print(f"System status: {data}")
        except json.JSONDecodeError as e:
            print(f"Invalid JSON message: {str(e)}")
        except Exception as e:
            print(f"Error handling message: {str(e)}")

    """
    Subscribes to specified WebSocket channels.
    
    Args:
        channels (list): List of channel names to subscribe to
        token (str): Authentication token for private channels
        
    Raises:
        Exception: If subscription fails
    """
    async def subscribe(self, channels, token):
        """Subscribe to the specified channels."""
        for channel in channels:
            subscribe_message = {
                "method": "subscribe",
                "params": {
                    "channel": channel,
                    "token": token,
                }
            }
            
            # Add specific parameters for executions channel
            if channel == 'executions':
                subscribe_message["params"].update({
                    "snap_orders": True,
                    "snap_trades": False
                })
            elif channel == 'balances':
                subscribe_message["params"]["snapshot"] = True

            await self.websocket.send(json.dumps(subscribe_message))
            print(f"Subscribed to {channel} channel.")

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
                print(f"Unsubscribed from {channel} channel.")
                # Wait briefly for unsubscribe confirmation
                await asyncio.sleep(SHORT_SLEEP_TIME)
            except websockets.exceptions.ConnectionClosed:
                print(f"Connection closed while unsubscribing from {channel}")
                break
            except Exception as e:
                print(f"Error unsubscribing from {channel}: {str(e)}")

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
        print(f"PORTFOLIO: ${total_value:,.2f} ({current_time})")
        
        # Check if we should take profit
        await self.check_and_take_profit()

    """
    Monitors portfolio value and executes profit-taking orders when targets are met.
    Sends email notifications for profit-taking attempts.
    
    Raises:
        Exception: If profit-taking order fails
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

            email_body = (
                f"Attempting to take profit of ${PROFIT_INCREMENT:.2f} USDC\n"
                f"Amount: ${PROFIT_INCREMENT:.2f} USDC\n"
                f"Portfolio Value: ${self.portfolio_value:.2f}\n"
                f"Previous High: ${self.highest_portfolio_value:.2f}"
            )
            await self.email_manager.send_email(
                subject=f"Diophant Grid Bot - Profit Take Attempt {current_time}",
                body=email_body,
                notification_type="profit_taking",
                cooldown_minutes=15
            )
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
                print(f"PROFIT: Taking profit of ${PROFIT_INCREMENT:.2f} USDC at portfolio value ${self.portfolio_value:.2f}")
                self.last_profit_take_time = current_time
            except Exception as e:
                print(f"Error taking profit: {str(e)}")

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
            for asset in data.get('data', []):
                asset_code = asset.get('asset')
                balance = float(asset.get('balance', 0))
                self.balances[asset_code] = balance
                
                # Add to tracking set if non-zero balance and not USD
                if balance > 0 and asset_code != 'USD':
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
        """Subscribe to ticker data for specified symbols."""
        if not symbols:
            return
            
        for symbol in symbols:
            subscribe_message = {
                "method": "subscribe",
                "params": {
                    "channel": "ticker",
                    "symbol": [symbol]  # Subscribe to one symbol at a time
                }
            }
            try:
                await self.public_websocket.send(json.dumps(subscribe_message))
                self.ticker_subscriptions[symbol] = True
                print(f"Subscribed to ticker for: {symbol}")
                # Small delay between subscriptions to avoid overwhelming the connection
                await asyncio.sleep(SHORT_SLEEP_TIME)
            except Exception as e:
                print(f"Error subscribing to ticker for {symbol}: {str(e)}")

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
                    "symbol": symbols  # Send all symbols at once
                }
            }
            await self.public_websocket.send(json.dumps(unsubscribe_message))
            for symbol in symbols:
                self.ticker_subscriptions.pop(symbol, None)
                print(f"Unsubscribed from ticker for: {symbol}")
            await asyncio.sleep(SHORT_SLEEP_TIME)
        except websockets.exceptions.ConnectionClosed:
            print("Public connection closed while unsubscribing from tickers")
        except Exception as e:
            print(f"Error unsubscribing from tickers: {str(e)}")

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
                print(f"TICKER: {symbol} Last={ticker_data.get('last')} Bid={ticker_data.get('bid')} Ask={ticker_data.get('ask')}")
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
        #print(f"Received execution update: {data}")
        
        if data.get('type') == 'snapshot':
            # Clear existing orders and replace with snapshot
            self.orders = {}
            for execution in data.get('data', []):
                if execution.get('order_status') in ['new', 'partially_filled']:
                    order_id = execution.get('order_id')
                    if order_id:
                        self.orders[order_id] = execution
                        print(f"ORDERS: Added order {order_id} for {execution.get('symbol')}")
        
        elif data.get('type') == 'update':
            for execution in data.get('data', []):
                order_id = execution.get('order_id')
                if not order_id:
                    continue
                    
                exec_type = execution.get('exec_type')
                order_status = execution.get('order_status')
                
                print(f"Processing execution update: ID={order_id} Type={exec_type} Status={order_status}")
                
                if exec_type in ['filled', 'canceled', 'expired']:
                    # Remove completed orders
                    if order_id in self.orders:
                        removed_order = self.orders.pop(order_id)
                        print(f"Removed {exec_type} order {order_id} for {removed_order.get('symbol')}")
                elif exec_type in ['new', 'pending_new']:
                    # For new orders, merge with existing data if available
                    if order_id in self.orders:
                        self.orders[order_id].update(execution)
                    else:
                        # If this is first time seeing order, store complete execution data
                        self.orders[order_id] = execution
                    print(f"{'Updated' if order_id in self.orders else 'Added new'} order {order_id} for {execution.get('symbol')}")
                elif order_id in self.orders:
                    # Update existing orders with new data
                    self.orders[order_id].update(execution)
                    print(f"Updated order {order_id} status: {order_status}")
        
        # Debug print current orders
        print("\nCurrent open orders:")
        for order_id, order in self.orders.items():
            symbol = order.get('symbol', 'None')
            side = order.get('side', 'None')
            status = order.get('order_status', 'None')
            print(f"ORDER: {order_id}: {symbol} {side} {status}")

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
            print(f"ORDER: {details_str}")

    """
    Processes execution messages for order updates.
    Maintains order state and handles various execution types.
    
    Args:
        message (dict): Execution message from WebSocket
    """
    async def handle_execution_message(self, message):
        """Handle execution messages for order updates."""
        if message['type'] == 'snapshot':
            # Clear existing orders and replace with snapshot
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
            print(f"Timeout waiting for response to request {req_id}")
            return None
        finally:
            # Clean up future
            self._response_futures.pop(req_id, None)


class KrakenGridBot:
    def __init__(self, client: KrakenWebSocketClient):
        self.client = client
        self.active_grids = {}  # Dictionary to track active grid trades per trading pair
        self.email_manager = EmailManager()
        self.grid_settings = {
            pair: {
                'buy_order_size': settings['size'],
                'sell_order_size': settings['size'],
                'grid_interval': settings['grid_interval'],  # Now using per-asset grid interval
                'active_orders': set(),  # Track order IDs for this grid
                'last_order_time': 0  # Add tracking for last order time
            }
            for pair, settings in TRADING_PAIRS.items()
        }
        self.grid_orders = {
            pair: {'buy': None, 'sell': None} 
            for pair in TRADING_PAIRS.keys()
        }
    """
    Initialize and start the grid trading strategy.
    
    Sets up handlers for order updates and market data.
    Initializes orders for each configured trading pair.
    
    Raises:
        Exception: If initialization or order setup fails
    """
    async def start(self):
        """Initialize and start the grid trading strategy."""
        # Set up handlers for order updates and market data

        # Initialize orders for each trading pair
        print("Starting grid bot monitoring...")
    
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
            print(f"No ticker data available for {trading_pair}")
            return None
            
        # Get current price from last trade
        current_price = float(ticker['last'])
        
        # Calculate grid interval in absolute terms
        interval_amount = current_price * (self.grid_settings[trading_pair]['grid_interval']/ 100)
        
        # Calculate buy price (current price minus interval)
        buy_price = current_price - interval_amount
        
        print(f"Grid price for {trading_pair}: Current=${current_price:.2f}, Buy=${buy_price:.2f}, Interval=${interval_amount:.2f}")
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
            print(f"Sent cancel request for order {order_id}")
            
            # Wait briefly for the cancel to process
            await asyncio.sleep(LONG_SLEEP_TIME)
            
            # Verify the order was removed from client.orders
            if order_id not in self.client.orders:
                print(f"Successfully canceled order {order_id}")
            else:
                print(f"Warning: Order {order_id} may not have been canceled")
                
        except Exception as e:
            print(f"Error sending cancel request: {str(e)}")
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
        #print(f"Checking open orders for {trading_pair}")
        
        # Filter orders for the specified trading pair that are open
        open_orders = [
            order for order in self.client.orders.values()
            if (order.get('symbol') == trading_pair and 
                order.get('side') == 'buy' and  # Only look for buy orders
                order.get('order_status') in ['new', 'partially_filled'] and
                order.get('order_id'))
        ]
        
        # If no open buy orders exist, place new order
        if not open_orders:
            print(f"No buy orders found for {trading_pair}")
            await self.place_orders(trading_pair)
            return None

        # If multiple buy orders exist (shouldn't happen), keep most recent
        if len(open_orders) > 1:
            print(f"Warning: Found {len(open_orders)} buy orders for {trading_pair}")
            # Keep most recent buy order
            kept_order = sorted(open_orders, key=lambda x: x.get('time', 0), reverse=True)[0]
            # Cancel others
            for order in open_orders:
                if order['order_id'] != kept_order['order_id']:
                    await self.cancel_order(order['order_id'])
            return kept_order
        
        # Return the single buy order
        return open_orders[0]

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
            print(f"No open order provided for {trading_pair}")
            return False
            
        # Get current market data
        ticker = self.client.ticker_data.get(trading_pair)
        if not ticker or 'last' not in ticker:
            print(f"No ticker data available for {trading_pair}")
            return False

        # Get current market price and order details
        current_market_price = float(ticker['last'])
        order_price = float(open_order.get('limit_price', 0))
        is_buy_order = open_order.get('side') == 'buy'
        
        if not order_price:
            print(f"Could not determine limit price for order {open_order['order_id']}")
            return False

        # Calculate grid parameters using asset-specific interval
        grid_interval = self.grid_settings[trading_pair]['grid_interval']
        interval_amount = current_market_price * ((grid_interval + GRID_INTERVAL_GRACE) / 100)
        
        # For buy orders, check if the order is too far from current market price
        if is_buy_order:
            price_difference = current_market_price - order_price
            
            # Order is too far below market price (> 1 grid interval away)
            if price_difference > interval_amount:
                print(f"ORDER: Market Price: ${current_market_price:.2f}, Grid Interval: ${interval_amount:.2f}, Price Difference: ${price_difference:.2f}, Unacceptable")
                target_price = current_market_price - interval_amount
                await self.update_order_price(trading_pair, open_order, target_price)
                return False
        
        # Log order status
        print(f"ORDER: Market Price: ${current_market_price:.2f}, Grid Interval: ${interval_amount:.2f}, Price Difference: ${price_difference:.2f}, Acceptable")
        
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
        #print(f"Updating order price for {trading_pair}")
        
        if not order or not order.get('order_id'):
            print("No valid order provided to update")
            return None
        
        try:
            # Format price to proper number of decimal places
            formatted_price = f"{new_price:.1f}"
            req_id = int(time.time() * 1000)
            
            # Create amend order message with all required parameters
            amend_message = {
                "method": "amend_order",
                "params": {
                    "order_id": order['order_id'],
                    "limit_price": float(formatted_price),
                    "order_qty": float(order.get('order_qty', self.grid_settings[trading_pair]['buy_order_size'])),
                    "symbol": trading_pair,
                    "side": order.get('side', 'buy'),  # Include original side
                    "order_type": order.get('order_type', 'limit'),  # Include original order type
                    "token": await self.client.get_ws_token()
                },
                "req_id": req_id
            }
            
            # Send amend order and wait for response
            await self.client.websocket.send(json.dumps(amend_message))
            print(f"ORDER: Sent amend request for order {order['order_id']} to price ${formatted_price}")
            
            # Wait for response
            response = await self.client.wait_for_response(req_id)
            if response and response.get('success') is True:
                print(f"ORDER: Successfully updated order price to ${formatted_price}")
                return True
            else:
                print(f"Failed to update order price: {response}")
                # If amend fails, cancel the order and place a new one
                await self.cancel_order(order['order_id'])
                await self.place_orders(trading_pair)
                return False
            
        except Exception as e:
            print(f"Error updating order price: {str(e)}")
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
            print(f"Decay timer active for {trading_pair}. {time_left:.1f}s remaining")
            return
            
        print(f"Executing trade strategy for {trading_pair}")
        ticker = self.client.ticker_data.get(trading_pair)
        if not ticker or 'last' not in ticker:
            print(f"No ticker data available for {trading_pair}")
            return
        
        # Update last order time before placing orders
        self.grid_settings[trading_pair]['last_order_time'] = current_time
        
        current_price = float(ticker['last'])
        grid_interval = self.grid_settings[trading_pair]['grid_interval']
        
        # Get buy amount from TRADING_PAIRS
        buy_amount = TRADING_PAIRS[trading_pair]['size']
        
        # Calculate optimal sell amount
        sell_amount = await self.calculate_optimal_sell_amount(
            trading_pair, 
            buy_amount, 
            current_price
        )
        
        if sell_amount is None:
            print(f"Skipping orders for {trading_pair} - cannot achieve minimum profit")
            return
        
        # Calculate grid prices
        interval_amount = current_price * (grid_interval / 100)
        buy_price = current_price - interval_amount
        sell_price = current_price + interval_amount
        
        # Format prices to appropriate decimal places
        buy_price = float(f"{buy_price:.1f}")
        sell_price = float(f"{sell_price:.1f}")
        
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
                    "order_qty": buy_amount,  # Use the correct buy size
                    "limit_price": buy_price,
                    "token": await self.client.get_ws_token()
                },
                "req_id": buy_req_id
            }
            
            print(f"ORDER: Placing buy order for {trading_pair}, Current price: ${current_price:.2f}, "
                  f"Grid interval: ${interval_amount:.2f}, Buy price: ${buy_price:.2f}, "
                  f"Buy quantity: {buy_amount}")
            
            # Send buy order and wait for response
            await self.client.websocket.send(json.dumps(buy_order_msg))
            buy_response = await self.client.wait_for_response(buy_req_id)
            
            if buy_response and buy_response.get('success'):
                order_id = buy_response.get('result', {}).get('order_id')
                if order_id:
                    # Add order ID to grid settings tracking
                    self.grid_settings[trading_pair]['active_orders'].add(order_id)
                    # Update grid orders tracking
                    self.grid_orders[trading_pair]['buy'] = order_id
                    print(f"Grid buy order placed for {trading_pair} with ID: {order_id}")
                    
                    # Try to place corresponding sell order
                    try:
                        sell_req_id = int(time.time() * 1000)
                        sell_order_msg = {
                            "method": "add_order",
                            "params": {
                                "order_type": "limit",
                                "side": "sell",
                                "symbol": trading_pair,
                                "order_qty": sell_amount,  # Use the correct sell size
                                "limit_price": sell_price,
                                "token": await self.client.get_ws_token()
                            },
                            "req_id": sell_req_id
                        }
                        
                        print(f"ORDER: Placing corresponding sell order, Sell price: ${sell_price:.2f}, "
                              f"Sell quantity: {sell_amount}")
                        
                        await self.client.websocket.send(json.dumps(sell_order_msg))
                        # We don't track the response for sell orders
                        
                    except Exception as sell_error:
                        # Ignore sell order errors (likely insufficient funds)
                        print(f"Note: Could not place sell order (likely insufficient funds)")
                        
                else:
                    print(f"ORDER: placed but no order ID received in response: {buy_response}")
            else:
                print(f"Failed to place buy order: {buy_response}")
            
        except Exception as e:
            print(f"Error placing orders for {trading_pair}: {str(e)}")

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
        """Calculate the optimal sell amount to ensure minimum profit after fees."""
        grid_interval = self.grid_settings[trading_pair]['grid_interval']
        interval_amount = current_price * (grid_interval / 100)
        
        # Calculate prices
        buy_price = current_price - ((current_price * GRID_INTERVAL_GRACE)/100)
        sell_price = current_price + interval_amount
        
        # Calculate total cost of buy (including fees)
        buy_cost = buy_amount * buy_price
        buy_fee = buy_cost * KRAKEN_FEE
        total_buy_cost = buy_cost + buy_fee
        
        # Calculate how much asset we need to sell to cover costs and PROFIT_USD_TARGET
        # Formula: (total_buy_cost + PROFIT_USD_TARGET) / (sell_price * (1 - KRAKEN_FEE))
        required_sell_amount = (total_buy_cost + PROFIT_USD_TARGET) / (sell_price * (1 - KRAKEN_FEE))
        
        # Round to appropriate decimal places
        decimals = 8 if trading_pair.startswith('BTC') else 5
        optimal_sell_amount = round(required_sell_amount, decimals)
        
        # If required sell amount exceeds buy amount, sell entire buy amount
        if required_sell_amount > buy_amount:
            print(f"Warning: Required sell amount ({required_sell_amount}) exceeds buy amount ({buy_amount})")
            print("Will sell entire buy amount")
            optimal_sell_amount = buy_amount
        
        # Calculate actual profit with final sell amount
        sell_revenue = optimal_sell_amount * sell_price
        sell_fee = sell_revenue * KRAKEN_FEE
        actual_profit = sell_revenue - sell_fee - total_buy_cost
        asset_kept = buy_amount - optimal_sell_amount
        
        print(f"\nCalculated optimal sell amount for {trading_pair}:")
        print(f"Buy amount: {buy_amount}")
        print(f"Buy price: ${buy_price:.2f}")
        print(f"Sell price: ${sell_price:.2f}")
        print(f"Total buy cost (inc. fee): ${total_buy_cost:.4f}")
        print(f"Amount needed to sell: {optimal_sell_amount}")
        print(f"Amount kept: {asset_kept}")
        print(f"Expected profit: ${actual_profit:.4f}")
        print(f"Total fees: ${(buy_fee + sell_fee):.4f}")
        
        return optimal_sell_amount


class EmailManager:
    """Handles email notifications for the trading bot."""
    
    def __init__(self):
        self.sender_email = os.getenv("SENDER_EMAIL")
        self.receiver_email = os.getenv("RECEIVER_EMAIL") 
        self.app_password = os.getenv("EMAIL_APP_PASSWORD")
        self.smtp_server = os.getenv("SMTP_SERVER")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.last_notification_time = {}  # Track last notification time per type
        
    """
    Sends email notification with cooldown tracking.
    
    Args:
        subject (str): Email subject
        body (str): Email body
        notification_type (str, optional): Type of notification for cooldown
        cooldown_minutes (int): Minutes between notifications of same type
        
    Raises:
        Exception: If email sending fails
    """
    async def send_email(self, subject: str, body: str, notification_type: str = None, cooldown_minutes: int = 15):
        try:
            # Check cooldown if notification type is specified
            if notification_type:
                current_time = time.time()
                last_time = self.last_notification_time.get(notification_type, 0)
                
                # If within cooldown period, skip sending
                if current_time - last_time < (cooldown_minutes * 60):
                    print(f"Skipping {notification_type} notification - within cooldown period")
                    return
                
                # Update last notification time
                self.last_notification_time[notification_type] = current_time
            
            # Create message
            message = MIMEMultipart()
            message["From"] = self.sender_email
            message["To"] = self.receiver_email
            message["Subject"] = subject
            
            # Add body
            message.attach(MIMEText(body, "plain"))
            
            # Create SMTP session
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.app_password)
                server.send_message(message)
            
            print(f"Email notification sent: {subject}")
            
        except Exception as e:
            print(f"Failed to send email notification: {str(e)}")


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
                        print(f"ORDER: validity for {pair}: {is_valid}")
                    else:
                        print(f"ORDER: No current orders for {pair}")
                    
                await asyncio.sleep(LONG_SLEEP_TIME)
            except asyncio.CancelledError:
                break
    except KrakenAPIError as e:
        print(f"Kraken API error: {e.error_code} - {e.message}")
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
    finally:
        print("\nGracefully shutting down...")
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
            print("Successfully disconnected from all Kraken WebSocket streams.")
        except Exception as e:
            print(f"Error during shutdown: {str(e)}")
            # Optionally, add more detailed error information for debugging
            import traceback
            print(f"Shutdown error details:\n{traceback.format_exc()}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received. Exiting...")

import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import numpy as np
from .fetcher import CryptoPriceFetcher

# Set logging level to ERROR to suppress INFO messages
logging.basicConfig(level=logging.CRITICAL)

class PositionTracker:
    def __init__(self):
        self.positions = {}

    def update_position(self, symbol: str, side: str, price: float, quantity: float):
        if symbol not in self.positions:
            self.positions[symbol] = {"balance": 0, "usd_balance": 0, "trades": []}

        if side == "buy":
            self.positions[symbol]["balance"] += quantity
            self.positions[symbol]["usd_balance"] -= price * quantity
        elif side == "sell":
            self.positions[symbol]["balance"] -= quantity
            self.positions[symbol]["usd_balance"] += price * quantity
        
        self.positions[symbol]["trades"].append({
            "timestamp": datetime.now(),
            "side": side,
            "price": price,
            "quantity": quantity
        })

    def get_current_position(self, symbol: str):
        return self.positions.get(symbol, {"balance": 0, "usd_balance": 0})
    
class MarketAnalyzer:
    def __init__(self, price_fetcher: CryptoPriceFetcher):
        self.price_fetcher = price_fetcher
        self.price_history = {}

    def update_price_history(self, symbol: str):
        current_price = self.price_fetcher.get_best_price(symbol)['midpoint_price']
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        self.price_history[symbol].append({"timestamp": datetime.now(), "price": current_price})
        
        # Keep only the last 24 hours of price data
        self.price_history[symbol] = [p for p in self.price_history[symbol] if p["timestamp"] > datetime.now() - timedelta(days=1)]

    def calculate_volatility(self, symbol: str):
        if len(self.price_history.get(symbol, [])) < 2:
            return None
        
        prices = [p["price"] for p in self.price_history[symbol]]
        returns = np.diff(np.log(prices))
        return np.std(returns) * np.sqrt(len(self.price_history[symbol]))

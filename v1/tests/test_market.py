import unittest
import time
from unittest.mock import patch, MagicMock
import unittest.mock
import logging
from datetime import datetime, timedelta
from kraken_utils.market import PositionTracker, MarketAnalyzer
from kraken_utils.fetcher import CryptoPriceFetcher

logging.basicConfig(level=logging.CRITICAL)


class TestPositionTracker(unittest.TestCase):
    def setUp(self):
        self.position_tracker = PositionTracker()

    def test_update_position_buy(self):
        self.position_tracker.update_position("BTCUSD", "buy", 30000, 1)
        position = self.position_tracker.get_current_position("BTCUSD")
        self.assertEqual(position["balance"], 1)
        self.assertEqual(position["usd_balance"], -30000)

    def test_update_position_sell(self):
        self.position_tracker.update_position("BTCUSD", "buy", 30000, 1)
        self.position_tracker.update_position("BTCUSD", "sell", 31000, 0.5)
        position = self.position_tracker.get_current_position("BTCUSD")
        self.assertEqual(position["balance"], 0.5)
        self.assertEqual(position["usd_balance"], -14500)

    def test_get_current_position_nonexistent(self):
        position = self.position_tracker.get_current_position("ETHUSD")
        self.assertEqual(position["balance"], 0)
        self.assertEqual(position["usd_balance"], 0)

    def test_multiple_trades(self):
        self.position_tracker.update_position("BTCUSD", "buy", 30000, 1)
        self.position_tracker.update_position("BTCUSD", "sell", 31000, 0.5)
        self.position_tracker.update_position("BTCUSD", "buy", 29000, 0.2)
        position = self.position_tracker.get_current_position("BTCUSD")
        self.assertEqual(position["balance"], 0.7)
        self.assertAlmostEqual(position["usd_balance"], -20300, places=2)




class TestMarketAnalyzer(unittest.TestCase):
    def setUp(self):
        self.mock_price_fetcher = unittest.mock.Mock(spec=CryptoPriceFetcher)
        self.market_analyzer = MarketAnalyzer(self.mock_price_fetcher)

    def test_update_price_history(self):
        self.mock_price_fetcher.get_best_price.return_value = {'midpoint_price': 100.0}
        self.market_analyzer.update_price_history('BTCUSD')
        self.assertEqual(len(self.market_analyzer.price_history['BTCUSD']), 1)
        self.assertEqual(self.market_analyzer.price_history['BTCUSD'][0]['price'], 100.0)

    def test_calculate_volatility_insufficient_data(self):
        self.assertIsNone(self.market_analyzer.calculate_volatility('BTCUSD'))

    def test_calculate_volatility(self):
        # Simulate price history
        self.market_analyzer.price_history['BTCUSD'] = [
            {'timestamp': datetime.now() - timedelta(hours=i), 'price': price}
            for i, price in enumerate([100, 101, 99, 102, 98])
        ]
        volatility = self.market_analyzer.calculate_volatility('BTCUSD')
        self.assertIsNotNone(volatility)
        self.assertGreater(volatility, 0)

    def test_price_history_cleanup(self):
        # Add old price data
        old_time = datetime.now() - timedelta(days=2)
        self.market_analyzer.price_history['BTCUSD'] = [
            {'timestamp': old_time, 'price': 100.0}
        ]
        
        # Add new price data
        self.mock_price_fetcher.get_best_price.return_value = {'midpoint_price': 101.0}
        self.market_analyzer.update_price_history('BTCUSD')
        
        # Check that old data is removed
        self.assertEqual(len(self.market_analyzer.price_history['BTCUSD']), 1)
        self.assertEqual(self.market_analyzer.price_history['BTCUSD'][0]['price'], 101.0)

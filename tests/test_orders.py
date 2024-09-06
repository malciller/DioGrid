import unittest
from unittest.mock import Mock, patch
from datetime import datetime
import json
import os
import tempfile
import logging

from kraken_utils.api import KrakenAuthBuilder, APICounter
from kraken_utils.fetcher import CryptoPriceFetcher
from kraken_utils.market import PositionTracker, MarketAnalyzer
from kraken_utils.orders import OrderManager

logging.basicConfig(level=logging.CRITICAL)

class TestOrderManager(unittest.TestCase):

    def setUp(self):
        self.auth_builder = Mock(spec=KrakenAuthBuilder)
        self.position_tracker = Mock(spec=PositionTracker)
        self.market_analyzer = Mock(spec=MarketAnalyzer)
        self.price_fetcher = Mock(spec=CryptoPriceFetcher)
        
        # Create a temporary directory for the log file
        self.temp_dir = tempfile.mkdtemp()
        self.test_log_file = os.path.join(self.temp_dir, 'test_bot_orders.json')
        
        self.order_manager = OrderManager(
            self.auth_builder,
            self.position_tracker,
            self.market_analyzer,
            self.price_fetcher
        )
        self.order_manager.log_file = self.test_log_file

    def tearDown(self):
        # Remove the temporary directory and its contents after each test
        for root, dirs, files in os.walk(self.temp_dir, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(self.temp_dir)

    @patch('requests.post')
    def test_place_order_success(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {'result': {'txid': ['OABC123']}}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        self.position_tracker.get_current_position.return_value = {'balance': 1.0}

        result = self.order_manager.place_order("buy", 50000.0, 0.001, "BTC/USD")
        
        self.assertIsInstance(result, dict)
        self.assertEqual(result['order_id'], 'OABC123')
        self.assertEqual(result['side'], 'buy')
        self.assertEqual(result['pair'], 'BTC/USD')
        self.assertEqual(result['price'], 50000.0)
        self.assertEqual(result['volume'], 0.001)

        # Check if the order was logged
        with open(self.test_log_file, 'r') as f:
            logged_orders = json.load(f)
        self.assertEqual(len(logged_orders), 1)
        self.assertEqual(logged_orders[0]['order_id'], 'OABC123')

    @patch('requests.post')
    def test_place_order_insufficient_balance(self, mock_post):
        self.position_tracker.get_current_position.return_value = {'balance': 0.0001}

        result = self.order_manager.place_order("sell", 50000.0, 0.001, "BTC/USD")
        
        self.assertEqual(result, {})
        mock_post.assert_not_called()

    def test_execute_grid_strategy(self):
        self.order_manager._execute_buy_and_sell_orders = Mock()
        self.market_analyzer.update_price_history = Mock()
        self.market_analyzer.calculate_volatility = Mock(return_value=0.05)

        self.order_manager.execute_grid_strategy(
            current_price=50000.0,
            grid_percentage=0.01,
            lower_bound_percentage=0.05,
            pair="BTC/USD",
            max_open_orders=5
        )

        self.assertEqual(self.order_manager._execute_buy_and_sell_orders.call_count, 10)

    def test_check_and_update_open_orders(self):
        self.order_manager.check_order_status = Mock(side_effect=[True, False, True])

        self.order_manager.open_buy_orders = {
            "BTC/USD": [
                {"order_id": "123", "pair": "BTC/USD", "side": "buy"},
                {"order_id": "456", "pair": "BTC/USD", "side": "buy"}
            ]
        }
        self.order_manager.open_sell_orders = {
            "BTC/USD": [
                {"order_id": "789", "pair": "BTC/USD", "side": "sell"}
            ]
        }

        self.order_manager.check_and_update_open_orders(["BTC/USD"])

        self.assertEqual(len(self.order_manager.open_buy_orders["BTC/USD"]), 1)
        self.assertEqual(len(self.order_manager.open_sell_orders["BTC/USD"]), 1)
        self.assertEqual(self.order_manager.open_buy_orders["BTC/USD"][0]["order_id"], "123")
        self.assertEqual(self.order_manager.open_sell_orders["BTC/USD"][0]["order_id"], "789")

    def test_load_open_orders(self):
        test_orders = [
            {"pair": "BTC/USD", "order_id": "123", "side": "buy"},
            {"pair": "ETH/USD", "order_id": "456", "side": "sell"}
        ]
        with open(self.test_log_file, 'w') as f:
            json.dump(test_orders, f)

        self.order_manager.check_order_status = Mock(return_value=True)

        self.order_manager.load_open_orders()

        self.assertEqual(len(self.order_manager.open_buy_orders["BTC/USD"]), 1)
        self.assertEqual(len(self.order_manager.open_sell_orders["ETH/USD"]), 1)
        self.assertEqual(self.order_manager.open_buy_orders["BTC/USD"][0]["order_id"], "123")
        self.assertEqual(self.order_manager.open_sell_orders["ETH/USD"][0]["order_id"], "456")

    def test_round_price(self):
        self.assertEqual(self.order_manager._round_price("BTC/USD", 50000.123), 50000.1)
        self.assertEqual(self.order_manager._round_price("ETH/USD", 3000.5678), 3000.57)
        self.assertEqual(self.order_manager._round_price("XRP/USD", 0.54321), 0.5432)

    def test_round_volume(self):
        self.assertEqual(self.order_manager._round_volume("BTC/USD", 1.23456789), 1.2346)
        self.assertEqual(self.order_manager._round_volume("ETH/USD", 5.6789), 5.6789)
        self.assertEqual(self.order_manager._round_volume("XRP/USD", 100.5), 100)

    def test_get_min_trade_size(self):
        self.assertEqual(self.order_manager._get_min_trade_size("BTC/USD"), 0.0002)
        self.assertEqual(self.order_manager._get_min_trade_size("ETH/USD"), 0.002)
        self.assertEqual(self.order_manager._get_min_trade_size("XRP/USD"), 10.0)
        self.assertEqual(self.order_manager._get_min_trade_size("UNKNOWN/USD"), 0.0001)

    @patch('requests.post')
    def test_check_order_status(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            'result': {
                'OABC123': {
                    'status': 'open'
                }
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = self.order_manager.check_order_status('OABC123')
        self.assertTrue(result)

        mock_response.json.return_value = {
            'result': {
                'OABC123': {
                    'status': 'closed'
                }
            }
        }
        result = self.order_manager.check_order_status('OABC123')
        self.assertFalse(result)

    def test_log_order(self):
        order_details = {
            "order_id": "TEST123",
            "side": "buy",
            "pair": "BTC/USD",
            "price": 50000.0,
            "volume": 0.1
        }
        self.order_manager.log_order(order_details)

        with open(self.test_log_file, 'r') as f:
            logged_orders = json.load(f)
        
        self.assertEqual(len(logged_orders), 1)
        self.assertEqual(logged_orders[0], order_details)

    def test_log_order_missing_order_id(self):
        order_details = {
            "side": "buy",
            "pair": "BTC/USD",
            "price": 50000.0,
            "volume": 0.1
        }
        self.order_manager.log_order(order_details)

        self.assertFalse(os.path.exists(self.test_log_file))
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

        real_config_file = 'config.json'

        self.temp_dir = tempfile.mkdtemp()
        self.test_log_file = os.path.join(self.temp_dir, 'test_bot_orders.json')

        self.order_manager = OrderManager(
            self.auth_builder,
            self.position_tracker,
            self.market_analyzer,
            self.price_fetcher,
            grid_percentage=0.01,  # Set grid percentage for testing
            config_file=real_config_file
        )
        self.order_manager.log_file = self.test_log_file

    def tearDown(self):
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


    def test_execute_grid_strategy_with_minimum_interval(self):
        self.order_manager._execute_buy_and_sell_orders = Mock()
        self.market_analyzer.update_price_history = Mock()
        self.market_analyzer.calculate_volatility = Mock(return_value=0.05)

        current_price = 50000.0
        grid_percentage = 0.01  # 1% grid
        lower_bound_percentage = 0.05
        max_open_orders = 5

        self.order_manager.execute_grid_strategy(
            current_price=current_price,
            grid_percentage=grid_percentage,
            lower_bound_percentage=lower_bound_percentage,
            pair="BTC/USD",
            max_open_orders=max_open_orders
        )

        initial_interval = current_price * (grid_percentage / 2)
        grid_interval = current_price * grid_percentage
        lower_bound = current_price * (1 - lower_bound_percentage)
        upper_bound = current_price * (1 + lower_bound_percentage)

        expected_buy_orders = 0
        expected_sell_orders = 0
        buy_price = current_price - initial_interval
        sell_price = current_price + initial_interval

        # Calculate the number of expected buy orders
        while buy_price > lower_bound and expected_buy_orders < max_open_orders:
            expected_buy_orders += 1
            buy_price -= grid_interval

        # Calculate the number of expected sell orders
        while sell_price < upper_bound and expected_sell_orders < max_open_orders:
            expected_sell_orders += 1
            sell_price += grid_interval

        total_expected_orders = expected_buy_orders + expected_sell_orders
        self.assertEqual(self.order_manager._execute_buy_and_sell_orders.call_count, total_expected_orders)

        # Verify the prices of the orders
        call_args_list = self.order_manager._execute_buy_and_sell_orders.call_args_list
        buy_price = current_price - initial_interval
        sell_price = current_price + initial_interval

        for i, call_args in enumerate(call_args_list):
            args, kwargs = call_args
            price = args[0]  # Unpack the price from the args tuple
            if kwargs['place_buy']:
                self.assertAlmostEqual(price, buy_price, delta=0.01)
                buy_price -= grid_interval
            else:
                self.assertAlmostEqual(price, sell_price, delta=0.01)
                sell_price += grid_interval



    @patch('requests.post')
    def test_execute_grid_strategy_with_minimum_threshold(self, mock_post):
        self.order_manager._execute_buy_and_sell_orders = Mock()
        self.market_analyzer.update_price_history = Mock()
        self.market_analyzer.calculate_volatility = Mock(return_value=0.01)  # Low volatility

        current_price = 50000.0
        grid_percentage = 0.01  # 1% grid
        lower_bound_percentage = 0.05
        max_open_orders = 5

        self.order_manager.execute_grid_strategy(
            current_price=current_price,
            grid_percentage=grid_percentage,
            lower_bound_percentage=lower_bound_percentage,
            pair="BTC/USD",
            max_open_orders=max_open_orders
        )

        initial_interval = current_price * (grid_percentage / 2)
        grid_interval = current_price * grid_percentage
        lower_bound = current_price * (1 - lower_bound_percentage)
        upper_bound = current_price * (1 + lower_bound_percentage)

        expected_buy_orders = 0
        expected_sell_orders = 0
        buy_price = current_price - initial_interval
        sell_price = current_price + initial_interval

        # Calculate the number of expected buy orders
        while buy_price > lower_bound and expected_buy_orders < max_open_orders:
            expected_buy_orders += 1
            buy_price -= grid_interval

        # Calculate the number of expected sell orders
        while sell_price < upper_bound and expected_sell_orders < max_open_orders:
            expected_sell_orders += 1
            sell_price += grid_interval

        total_expected_orders = expected_buy_orders + expected_sell_orders
        self.assertEqual(self.order_manager._execute_buy_and_sell_orders.call_count, total_expected_orders)

        # Verify the prices of the orders
        call_args_list = self.order_manager._execute_buy_and_sell_orders.call_args_list
        buy_price = current_price - initial_interval
        sell_price = current_price + initial_interval

        for i, call_args in enumerate(call_args_list):
            args, kwargs = call_args
            price = args[0]  # Unpack the price from the args tuple
            if kwargs['place_buy']:
                self.assertAlmostEqual(price, buy_price, delta=0.01)
                buy_price -= grid_interval
            else:
                self.assertAlmostEqual(price, sell_price, delta=0.01)
                sell_price += grid_interval

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


    @patch('requests.post')
    def test_no_orders_below_lower_bound_or_above_upper_bound(self, mock_post):
        self.order_manager._execute_buy_and_sell_orders = Mock()
        self.market_analyzer.update_price_history = Mock()
        self.market_analyzer.calculate_volatility = Mock(return_value=0.05)

        current_price = 50000.0
        grid_percentage = 0.01
        lower_bound_percentage = 0.05
        max_open_orders = 5

        self.order_manager.execute_grid_strategy(
            current_price=current_price,
            grid_percentage=grid_percentage,
            lower_bound_percentage=lower_bound_percentage,
            pair="BTC/USD",
            max_open_orders=max_open_orders
        )

        lower_bound = current_price * (1 - lower_bound_percentage)
        upper_bound = current_price * (1 + lower_bound_percentage)

        for call_args in self.order_manager._execute_buy_and_sell_orders.call_args_list:
            price = call_args[0][0]
            if call_args[1]['place_buy']:
                self.assertGreaterEqual(price, lower_bound)
            else:
                self.assertLessEqual(price, upper_bound)

import unittest
from unittest.mock import patch, MagicMock
import unittest.mock
import requests
import logging
from kraken_utils.api import KrakenAuthBuilder, API_KEY, API_SECRET
from kraken_utils.fetcher import CryptoPriceFetcher


logging.basicConfig(level=logging.CRITICAL)


class TestCryptoPriceFetcher(unittest.TestCase):
    def setUp(self):
        self.auth_builder = KrakenAuthBuilder(api_key=API_KEY, api_secret=API_SECRET)
        self.price_fetcher = CryptoPriceFetcher(self.auth_builder)

    @patch('kraken_utils.fetcher.requests.get')
    def test_get_best_price_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "XXBTZUSD": {
                    "a": ["30000.00000", "1", "1.000"],
                    "b": ["29999.00000", "1", "1.000"]
                }
            }
        }
        mock_get.return_value = mock_response

        result = self.price_fetcher.get_best_price('BTCUSD')

        self.assertEqual(result['symbol'], 'BTCUSD')
        self.assertEqual(result['best_bid'], 29999.0)
        self.assertEqual(result['best_ask'], 30000.0)
        self.assertEqual(result['midpoint_price'], 29999.5)

    @patch('kraken_utils.fetcher.requests.get')
    def test_get_best_price_failure(self, mock_get):
        mock_get.side_effect = requests.RequestException("API Error")

        result = self.price_fetcher.get_best_price('BTCUSD')

        self.assertIsNone(result)

    def test_parse_best_price_response_success(self):
        data = {
            "result": {
                "XXBTZUSD": {
                    "a": ["30000.00000", "1", "1.000"],
                    "b": ["29999.00000", "1", "1.000"]
                }
            }
        }

        result = self.price_fetcher._parse_best_price_response(data, 'BTCUSD')

        self.assertEqual(result['symbol'], 'BTCUSD')
        self.assertEqual(result['best_bid'], 29999.0)
        self.assertEqual(result['best_ask'], 30000.0)
        self.assertEqual(result['midpoint_price'], 29999.5)

    def test_parse_best_price_response_failure(self):
        data = {"result": {}}

        result = self.price_fetcher._parse_best_price_response(data, 'BTCUSD')

        self.assertIsNone(result)
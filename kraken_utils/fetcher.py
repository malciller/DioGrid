import logging
from dotenv import load_dotenv
import requests
from .api import KrakenAuthBuilder

# Set logging level to ERROR to suppress INFO messages
logging.basicConfig(level=logging.CRITICAL)


class CryptoPriceFetcher:
    def __init__(self, auth_builder: KrakenAuthBuilder):
        self.auth_builder = auth_builder
        self.base_url = "https://api.kraken.com"
        self.kraken_pairs = {
            'BTCUSD': 'XXBTZUSD',
            'ETHUSD': 'XETHZUSD',
            'XRPUSD': 'XXRPZUSD',
            'SOLUSD': 'SOLUSD',
            'ADAUSD': 'ADAUSD'
        }

    def get_best_price(self, pair: str) -> dict:
        kraken_pair = self.kraken_pairs.get(pair, pair)
        endpoint = "/0/public/Ticker"
        params = {"pair": kraken_pair}
        url = self.base_url + endpoint
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return self._parse_best_price_response(response.json(), pair)
        except requests.RequestException as e:
            logging.error(f"Error fetching best prices for {pair}: {e}")
            return None

    def _parse_best_price_response(self, data: dict, pair: str) -> dict:
        kraken_pair = self.kraken_pairs.get(pair, pair)
        if 'result' in data and kraken_pair in data['result']:
            price_info = data['result'][kraken_pair]
            best_bid = float(price_info['b'][0])
            best_ask = float(price_info['a'][0])
            midpoint_price = (best_bid + best_ask) / 2
            return {
                "symbol": pair,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "midpoint_price": midpoint_price,
            }
        logging.error(f"No price data found for {pair}")
        return None
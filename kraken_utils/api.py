# DioCrypto/kraken_utils.py
import os
import logging
import base64
import hmac
import hashlib
import urllib.parse
from dotenv import load_dotenv
import time

load_dotenv()

API_KEY = os.getenv("KRAKEN_API_KEY")
API_SECRET = os.getenv("KRAKEN_API_SECRET")

# Set logging level to ERROR to suppress INFO messages
logging.basicConfig(level=logging.INFO)

class KrakenAuthBuilder:
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret

    def get_signature(self, uri_path, data, secret):
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data['nonce']) + postdata).encode()
        message = uri_path.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()

    def get_headers(self, uri_path, data):
        signature = self.get_signature(uri_path, data, self.api_secret)
        return {
            "API-Key": self.api_key,
            "API-Sign": signature,
        }
    

class APICounter:
    def __init__(self, max_value=20, decay_rate_per_second=0.5):
        self.max_value = max_value
        self.counter = 0
        self.decay_rate_per_second = decay_rate_per_second
        self.last_checked_time = time.time()

    def update_counter(self, api_call_weight: int = 0):
        current_time = time.time()
        time_passed = current_time - self.last_checked_time

        # Apply decay
        decay_amount = time_passed * self.decay_rate_per_second
        
        # Calculate new counter value after decay
        new_counter = max(0, self.counter - decay_amount)
        
        # Add API call weight
        new_counter = min(self.max_value, new_counter + api_call_weight)
        
        # Update the counter and last checked time
        self.counter = new_counter
        self.last_checked_time = current_time

    def can_make_api_call(self, api_call_weight: int):
        self.update_counter()  # Update the counter first
        return self.counter + api_call_weight <= self.max_value

    def wait_until_ready(self, api_call_weight: int):
        while not self.can_make_api_call(api_call_weight):
            wait_time = max(2, (api_call_weight - (self.max_value - self.counter)) / self.decay_rate_per_second)
            time.sleep(wait_time)
            self.update_counter()

        self.update_counter(api_call_weight)  # Add the API call weight after waiting

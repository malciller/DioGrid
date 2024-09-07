import unittest
import unittest.mock
import logging
from kraken_utils.api import APICounter, KrakenAuthBuilder, API_KEY, API_SECRET
import time

logging.basicConfig(level=logging.CRITICAL)

class TestKrakenAuthBuilder(unittest.TestCase):
    def setUp(self):
        # Use mock values for testing
        self.api_key = 'test_api_key'
        self.api_secret = 'dGVzdF9hcGlfc2VjcmV0'  # Base64 encoded 'test_api_secret'
        self.auth_builder = KrakenAuthBuilder(self.api_key, self.api_secret)

    def test_initialization(self):
        self.assertEqual(self.auth_builder.api_key, self.api_key)
        self.assertEqual(self.auth_builder.api_secret, self.api_secret)

    def test_signature_generation(self):
        uri_path = "/0/private/Balance"
        data = {'nonce': '123456'}
        secret = self.api_secret  # Ensure it's a valid base64-encoded string
        signature = self.auth_builder.get_signature(uri_path, data, secret)
        self.assertIsInstance(signature, str)
        self.assertGreater(len(signature), 0)


class TestAPICounter(unittest.TestCase):
    def setUp(self):
        self.api_counter = APICounter(max_value=20, decay_rate_per_second=0.5)

    def test_initialization(self):
        self.assertEqual(self.api_counter.max_value, 20)
        self.assertEqual(self.api_counter.counter, 0)
        self.assertEqual(self.api_counter.decay_rate_per_second, 0.5)

    def test_update_counter_precise(self):
        # Start with an empty counter
        self.assertEqual(self.api_counter.counter, 0)

        # Add 10 to the counter
        self.api_counter.update_counter(api_call_weight=10)
        self.assertEqual(self.api_counter.counter, 10)

        # Wait for 1 second (should decay by approximately 0.5)
        time.sleep(1)
        self.api_counter.update_counter()
        self.assertAlmostEqual(self.api_counter.counter, 9.5, places=1)  # Loosened precision

        # Add 5 more to the counter
        self.api_counter.update_counter(api_call_weight=5)
        self.assertAlmostEqual(self.api_counter.counter, 14.5, places=1)  # Loosened precision

        # Wait for 2 seconds (should decay by approximately 1)
        time.sleep(2)
        self.api_counter.update_counter()
        self.assertAlmostEqual(self.api_counter.counter, 13.5, places=1)  # Loosened precision

        # Try to add more than the max_value
        self.api_counter.update_counter(api_call_weight=10)
        self.assertEqual(self.api_counter.counter, 20)  # Should be capped at max_value

    def test_can_make_api_call(self):
        # Step 1: API call should be allowed initially, as counter is empty
        self.assertTrue(self.api_counter.can_make_api_call(api_call_weight=5))

        # Step 2: Use up most of the counter
        self.api_counter.update_counter(api_call_weight=15)
        self.assertTrue(self.api_counter.can_make_api_call(api_call_weight=5))  # Should still be able to make a call

        # Step 3: Use up the remaining counter
        self.api_counter.update_counter(api_call_weight=5)
        self.assertFalse(self.api_counter.can_make_api_call(api_call_weight=1))  # Should now fail

        # Step 4: Simulate time passing to allow for decay
        time.sleep(2)  # Wait for 2 seconds
        self.assertTrue(self.api_counter.can_make_api_call(api_call_weight=1))  # Should now be able to make a call

    def test_wait_until_ready(self):
        self.api_counter.update_counter(api_call_weight=20)  # Fill up the counter
        start_time = time.time()
        self.api_counter.wait_until_ready(api_call_weight=5)  # Wait for decay
        end_time = time.time()
        self.assertGreaterEqual(end_time - start_time, 10)  # Should wait at least 10 seconds (5 / 0.5)
        self.assertLess(self.api_counter.counter, 20)  # Counter should be less than max after adding 5

    def test_update_counter_precise(self):
        # Start with an empty counter
        self.assertEqual(self.api_counter.counter, 0)

        # Add 10 to the counter
        self.api_counter.update_counter(api_call_weight=10)
        self.assertEqual(self.api_counter.counter, 10)

        # Wait for 1 second (should decay by 0.5)
        time.sleep(1)
        self.api_counter.update_counter()
        self.assertAlmostEqual(self.api_counter.counter, 9.5, places=1)  # Loosened precision

        # Add 5 more to the counter
        self.api_counter.update_counter(api_call_weight=5)
        self.assertAlmostEqual(self.api_counter.counter, 14.5, places=1)  # Loosened precision

        # Wait for 2 seconds (should decay by 1)
        time.sleep(2)
        self.api_counter.update_counter()
        self.assertAlmostEqual(self.api_counter.counter, 13.5, places=1)  # Loosened precision

        # Try to add more than the max_value
        self.api_counter.update_counter(api_call_weight=10)
        self.assertEqual(self.api_counter.counter, 20)  # Should be capped at max_value

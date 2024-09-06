
# DioGrid

DioGrid is a Python-based cryptocurrency grid trading bot built to interact with Kraken's API. 
It leverages Python scripts to execute buy and sell orders at grid intervals based on predefined conditions.

## Project Structure

```
DioCrypto/
│
├── kraken_utils/
│   ├── __init__.py                # Package initialization file
│   ├── p_api.py                   # Contains KrakenAuthBuilder and APICounter
│   ├── p_fetcher.py               # Contains CryptoPriceFetcher
│   ├── p_market.py                # Contains MarketAnalyzer and PositionTracker
│   └── p_orders.py                # Contains OrderManager for placing orders
│
├── main.py                        # The main script for running the trading bot
├── .env                           # Environment variables for API keys (not included in the repository)
├── logs/
│   └── bot_orders.json            # Stores logs of buy/sell orders
├── tests/
│   ├── __init__.py                # Test package initialization
│   ├── test_api.py                # Unit tests for API-related classes
│   ├── test_fetcher.py            # Unit tests for CryptoPriceFetcher
│   ├── test_market.py             # Unit tests for MarketAnalyzer and PositionTracker
│   └── test_orders.py             # Unit tests for OrderManager
└── README.md                      # Project documentation
```

## Features

- **Grid Trading**: Automatically places buy and sell orders at predefined grid intervals.
- **Kraken API Integration**: Handles Kraken's API authentication, rate limiting, and order placement.
- **Customizable Grid Parameters**: Modify grid intervals, upper and lower bounds, and the number of open orders.
- **Order Tracking**: Tracks and logs open buy/sell orders in JSON format.
- **Unit Tests**: Comprehensive unit testing for key components using `unittest`.

## Installation

### 1. Clone the Repository
```bash
git clone <your-repo-url>
cd DioCrypto
```

### 2. Install Dependencies
Ensure you have `python3` and `pip` installed. Then install the dependencies:
```bash
pip install -r requirements.txt
```

### 3. Set Up Environment Variables
Create a `.env` file in the root directory with your Kraken API credentials:
```
KRAKEN_API_KEY=<your_api_key>
KRAKEN_API_SECRET=<your_api_secret>
```

### 4. Run Unit Tests
Ensure everything is set up correctly by running the unit tests:
```bash
python3 -m unittest discover tests
```

## Usage

To start the grid trading bot, run `main.py` with the necessary arguments:

```bash
python3 main.py --grid-percentage 0.01 --lower-bound-percentage 0.05 --max-open-orders 3 --time-range 60 --pairs BTCUSD ETHUSD
```

### Arguments:

- `--grid-percentage`: Grid interval as a percentage of the current price.
- `--lower-bound-percentage`: Lower bound for grid trading.
- `--max-open-orders`: Maximum number of open buy/sell orders.
- `--time-range`: Time range in seconds for checking and updating open orders.
- `--pairs`: List of cryptocurrency pairs to trade (e.g., `BTCUSD`, `ETHUSD`).

## License

This project is licensed under the MIT License.

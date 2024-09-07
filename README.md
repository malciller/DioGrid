
# DioGrid

DioGrid is a Python-based cryptocurrency grid trading bot built to interact with Kraken's API. 
It leverages Python scripts to execute buy and sell orders at grid intervals based on predefined conditions. For those interested in expanding on this, the Kraken REST api documentation can be found here: https://docs.kraken.com/api/docs/rest-api/add-order

## Project Structure

```
DioGrid/
│
├── kraken_utils/
│   ├── __init__.py                # Package initialization file
│   ├── api.py                     # Contains KrakenAuthBuilder and APICounter
│   ├── fetcher.py                 # Contains CryptoPriceFetcher
│   ├── market.py                  # Contains MarketAnalyzer and PositionTracker
│   └── orders.py                  # Contains OrderManager for placing orders
│
├── main.py                        # The main script for running the trading bot
├── .env                           # Environment variables for API keys (not included in the repository)
├── config.json                    # Configuration file for managing trading parameters (see below)
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
git clone https://github.com/malciller/DioGrid
cd DioGrid
```

### 2. Install Dependencies

Ensure you have `python` and `pip` installed. Then install the dependencies:

```bash
pip install -r requirements.txt
```

### 3. Set Up Environment Variables

Create a `.env` file in the root directory with your Kraken API credentials:

```
KRAKEN_API_KEY=<your_api_key>
KRAKEN_API_SECRET=<your_api_secret>
```

### 4. Manage and Update the Configuration File

The `config.json` file is where you manage the cryptocurrency pairs, price precisions, volume precisions, and minimum trade sizes.

Kraken pairs can be found here: https://support.kraken.com/hc/en-us/articles/201893658-Currency-pairs-available-for-trading-on-Kraken

Kraken price precision and min order volume can be found here: https://support.kraken.com/hc/en-us/articles/4521313131540-Price-decimal-precision

#### Example `config.json` file:
```json
{
    "kraken_pairs": {
      "BTCUSD": "XXBTZUSD",
      "ETHUSD": "XETHZUSD",
      "XRPUSD": "XXRPZUSD",
      "SOLUSD": "SOLUSD",
      "ADAUSD": "ADAUSD"
    },
    "price_precisions": {
      "BTC/USD": 1,
      "ETH/USD": 2,
      "XRP/USD": 4,
      "SOL/USD": 2,
      "ADA/USD": 5
    },
    "volume_precisions": {
      "BTC/USD": 4,
      "ETH/USD": 4,
      "XRP/USD": 0,
      "SOL/USD": 2,
      "ADA/USD": 0
    },
    "min_trade_sizes": {
      "BTC/USD": 0.0002,
      "ETH/USD": 0.002,
      "XRP/USD": 10.0,
      "SOL/USD": 0.02,
      "ADA/USD": 20.0
    }
  }
  
```

### Steps to Update:

1. **Modify Pairs**: Add or remove cryptocurrency pairs in the `config.json` file under the `price_precisions`, `volume_precisions`, and `min_trade_sizes` fields.
2. **Save Changes**: Ensure you save the `config.json` file after making changes.
3. **Restart the Bot**: Restart the grid trading bot to apply the new configuration.

### 5. Run Unit Tests

Ensure everything is set up correctly by running the unit tests:

```bash
python -m unittest discover tests
```

## Usage

To start the grid trading bot, run `main.py` with the necessary arguments:

```bash
python main.py --grid-percentage 0.01 --lower-bound-percentage 0.05 --max-open-orders 3 --time-range 60 --pairs BTCUSD ETHUSD
```

### Arguments:

- `--grid-percentage`: Grid interval as a percentage of the current price.
- `--lower-bound-percentage`: Lower bound for grid trading.
- `--max-open-orders`: Maximum number of open buy orders.
- `--time-range`: Time range in seconds for checking and updating open orders.
- `--pairs`: List of cryptocurrency pairs to trade (e.g., `BTCUSD`, `ETHUSD`).

## License

This project is licensed under the MIT License.

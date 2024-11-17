
# DioGrid

DioGrid is a Python-based cryptocurrency grid trading bot designed to interact with Kraken's API. 
This bot leverages a consolidated Python script to execute buy and sell orders at grid intervals based on predefined conditions.
For more details about Kraken's API, visit: https://docs.kraken.com/api/

## Features

- **Grid Trading**: Places buy and sell orders at predefined intervals automatically.
- **Single-Script Architecture**: All functionality is encapsulated in a single Python file (`diogrid.py`) for simplicity.
- **Customizable Parameters**: Grid intervals, trading pairs, order quantities, and more can be easily adjusted.
- **Order Tracking**: Tracks and logs all open and completed orders.
- **Kraken API Integration**: Seamlessly integrates with Kraken's Websocket v2 API.

## Project Structure

```
DioGrid/
├── diogrid.py                     # The main and only script for the trading bot
└── README.md                      # Project documentation
```

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/malciller/DioGrid
cd DioGrid
```

### 2. Install Dependencies

Ensure `python` and `pip` are installed. Then, install the required libraries:

```bash
pip install -r requirements.txt
```

### 3. Set Up Environment Variables

Create a `.env` file in the root directory with your Kraken API credentials:

```
KRAKEN_API_KEY=<your_api_key>
KRAKEN_API_SECRET=<your_api_secret>
```

### 4. Update Configuration in the Script

All configuration (e.g., trading pairs, grid intervals, and API details) is managed directly within `diogrid.py` at the top of the main method declaration. Modify the following sections to fit your trading preferences:

#### Example Configuration in `diogrid.py`:
```python
# Example configuration section
    config = BotConfiguration(
        trading_pairs={
            "BTC/USD": 0.0001, # amount of BTC to trade
            "SOL/USD": 0.04, # amount of SOL to trade
        },
        grid_interval=0.8 # percentage of the price to trade around 
    )
```
Update these parameters to match your desired trading setup.

## Usage

Run the bot by executing the script:

```bash
python diogrid.py
```

Will be adding a docker file soon.

### Key Features and Parameters:

- `trading_pairs`: Dictionary of cryptocurrency pairs and amount to trade.
- `grid_interval`: Percentage interval for placing buy/sell orders around the current price.


## License

This project is licensed under the MIT License.

## Disclaimer

This software is provided for personal use and educational purposes only. It is not intended to provide financial advice, nor is it an invitation to engage in cryptocurrency trading. The authors of this software are not responsible for any financial losses or damages incurred while using the software. Users should consult with a licensed financial advisor before engaging in cryptocurrency trading. Cryptocurrency trading carries a high level of risk, and users should only trade with funds they can afford to lose.

# DioGrid
Follow DioGrid's Performance: https://portfolio.diophantsolutions.com

DioGrid is a Python-based cryptocurrency grid trading bot designed to interact with Kraken's API. 
This bot leverages a consolidated Python script to execute buy and sell orders at grid intervals based on predefined conditions.
For more details about Kraken's API, visit: https://docs.kraken.com/api/

## Features

- **Grid Trading**: Places buy and sell orders at predefined intervals automatically.
- **Single-Script Architecture**: All functionality is encapsulated in a single Python file (`diogrid.py`) for simplicity.
- **Customizable Parameters**: Grid intervals, trading pairs, order quantities, and more can be easily adjusted.
- **Order Tracking**: Tracks and logs all open and completed orders.
- **Kraken API Integration**: Seamlessly integrates with Kraken's Websocket v2 API.
- **Automated Profit Taking**: Places USDC buy orders when portfolio value increases by configured amount
- **Email Notifications**: Sends alerts for profit-taking events and important status updates
- **Dynamic Grid Adjustment**: Automatically adjusts orders to maintain optimal grid spacing
- **Portfolio Tracking**: Monitors total portfolio value and tracks historical highs
- **Error Recovery**: Automatically handles connection issues and order placement failures
- **Automated Staking**: Automatically stakes eligible assets in real-time as they accumulate
- **Compound Returns**: Combines trading profits with staking rewards for enhanced yield

## Project Structure

```
DioGrid/
├── diogrid.py                     # The main trading bot script
└── README.md                      # Project documentation
```

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/malciller/DioGrid
cd DioGrid
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Set Up Environment Variables

Create a `.env` file in the root directory with your API credentials and email settings:

```
KRAKEN_API_KEY=your_api_key
KRAKEN_API_SECRET=your_api_secret
SENDER_EMAIL=your_sender_email@example.com
RECEIVER_EMAIL=your_receiver_email@example.com
EMAIL_APP_PASSWORD=your_email_app_password
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
```

Note: For Gmail, you'll need to create an App Password. Visit your Google Account settings > Security > 2-Step Verification > App passwords to generate one.

### 4. Configure Trading Parameters

The bot's behavior is controlled by global variables at the top of `diogrid.py`. Please note that the Kraken minimum order volume threshold must be respected. For minimum order volumes, please visit: https://support.kraken.com/hc/en-us/articles/205893708-Minimum-order-size-volume-for-trading

Key settings include:

```python
# TRADING CONFIGURATION
PASSIVE_INCOME = 0 # 1 for True, 0 for False
KRAKEN_FEE = 0.002 # Your Kraken Maker Fee
STARTING_PORTFOLIO_INVESTMENT = 2700.0 # Starting USD portfolio balance
PROFIT_INCREMENT = 10 # Profit taking increment in USDC, ignored if PASSIVE_INCOME = 0
SELL_AMOUNT_MULTIPLIER = 0.999 # Multiplier for sell order size
TRADING_PAIRS = {
    pair: {
        'size': size, # Buy order size
        'grid_interval': grid, # Grid interval
        'grid_spacing': spacing, # Grid spacing
        'trail_interval': spacing, # Trail interval, secondary buffer for grid spacing to avoid over trading
        'precision': precision # Asset decimal price precision
    }
    for pair, (size, grid, spacing, precision) in {
        "BTC/USD": (0.00085, 0.75, 0.75, 1), 
        "SOL/USD": (0.06, 1.5, 1.5, 2),   
        "XRP/USD": (5.0, 2.5, 2.5, 5),        
        "ADA/USD": (18.0, 3.5, 3.5, 6), 
        "ETH/USD": (0.0045, 3.5, 3.5, 2),  
        "TRX/USD": (55.0, 2.5, 2.5, 6),     
        "DOT/USD": (2.5, 2.5, 2.5, 4), 
        "KSM/USD": (0.6, 2.5, 2.5, 2), 
    }.items()
}
```

### Parameter Details

#### Trading Mode Parameters
- **PASSIVE_INCOME**: Controls the bot's trading strategy
  - `0`: Asset Accumulation Mode
    - Sells 99.9% (with SELL_AMOUNT_MULTIPLIER = 0.999) of bought volume
    - Accumulates 0.1% of each trade in the traded asset
    - Example: Buying 1 BTC will sell 0.999 BTC, keeping 0.001 BTC
  - `1`: USDC Profit Mode
    - Sells 100% of bought volume (with SELL_AMOUNT_MULTIPLIER = 1)
    - Takes profits in USDC when portfolio reaches new highs
    - Example: When portfolio increases by PROFIT_INCREMENT ($10), buys $10 USDC

#### Fee and Profit Parameters
- **KRAKEN_FEE**: Your Kraken maker fee rate
  - Default: 0.002 (0.2%)
  - Affects minimum profitable grid spacing
  - Example: With 0.2% fee, minimum grid spacing should be >0.4% for profit

#### Grid Trading Parameters
- **GRID_INTERVAL**: Percentage between orders
  - Example: 0.75% for BTC means orders every $750 at $100,000 BTC price
  - Lower intervals = more frequent trades but smaller profits
  - Higher intervals = larger profits but fewer trades


- **GRID_SPACING**: Minimum price movement required for new orders
  - Prevents excessive trading in volatile conditions
  - Example: 0.75% spacing means price must move 0.75% from last trade
  - Usually set equal to GRID_INTERVAL for optimal performance

- **TRAIL_INTERVAL**: Secondary buffer for grid spacing
  - Provides additional protection against over-trading
  - Example: 0.75% means new orders wait for 0.75% price movement
  - Typically set equal to GRID_SPACING

#### Portfolio Management
- **STARTING_PORTFOLIO_INVESTMENT**: Initial portfolio value
  - Used as baseline for profit tracking
  - Example: $2700 starting value, tracks profits from this point

- **PROFIT_INCREMENT**: USDC profit-taking amount
  - Only active when PASSIVE_INCOME = 1
  - Example: $10 means buy $10 USDC when portfolio increases by $10
  - Smaller increments = more frequent USDC purchases
  - Larger increments = bigger but less frequent purchases

#### Trading Pair Configuration
Example for BTC/USD: (0.00085, 0.75, 0.75, 1)
- Size (0.00085): Buy order volume in base currency
- Grid (0.75): 0.75% spacing between orders
- Spacing (0.75): 0.75% minimum price movement needed
- Precision (1): Decimal places for price ($30,500.1)

Update these parameters to match your desired trading setup.

## Usage

Run the bot by executing the script:

```bash
python diogrid.py
```


## Running Persistently in Background via Docker

For instructions on setting up docker please visit: https://www.docker.com/get-started/

After setting up your `.env` file:

1. **Build the Docker Image**  
   Run the following command in the terminal to build the Docker image:

   ```bash
   docker build -t diogrid_container .
   ```

2. **Run the Docker Container in the Background**
   
   To run the container persistently in the background, use the `-d` flag:

   ```bash
   docker run -d --name diogrid_container --env-file .env diogrid_container
   ```

   - The `--env-file .env` option ensures that environment variables from your `.env` file are loaded into the container.

4. **Verify the Container is Running**  
   You can check the status of your container by running:

   ```bash
   docker ps
   ```

   This will list all running containers. Ensure your container is listed with its name and status as `Up`.

5. **View Logs**  
   If you need to monitor logs, you can view them in real time by running:

   ```bash
   docker logs -f diogrid_container
   ```

6. **Stop the Container**  
   To stop the container when needed, use:

   ```bash
   docker stop diogrid_container
   ```

7. **Restart the Container**  
   If you want to restart the container later without rebuilding, run:

   ```bash
   docker start diogrid_container
   ```

8. **Remove the Container**  
   If you want to delete the container, first stop it (if running), then remove it:

   ```bash
   docker rm diogrid_container
   ```

Now your Docker container should run persistently in the background, ensuring the bot is always running.


### Key Features and Parameters:

- `MIN_PROFIT_USD`: Minimum USD profit target per trade. The bot will sell enough of the purchased asset to secure this profit, while keeping any remaining amount as accumulated holdings. Lower values allow more frequent trading opportunities while building asset positions.
- `KRAKEN_FEE`: Trading fee percentage applied to each transaction.
- `STARTING_PORTFOLIO_INVESTMENT`: Initial investment amount for profit taking tracking.
- `PROFIT_INCREMENT`: USD amount threshold for triggering profit-taking orders.
- `GRID_INTERVAL`: Percentage spacing between orders for order placement.
- `GRID_INTERVAL_GRACE`: Additional buffer percentage added to grid spacing to prevent order overlap.
- `TRADING_PAIRS`: Dictionary of cryptocurrency pairs and their minimum trade amounts (e.g., `{"BTC/USD": 0.0001}`).


## Automated USDC Profit Taking

The bot includes an automated USDC profit-taking mechanism that converts gains into stablecoin holdings:

### How It Works

1. **Portfolio High Water Mark**
   - Bot tracks highest portfolio value (`highest_portfolio_value`)
   - Initial value set by `STARTING_PORTFOLIO_INVESTMENT` (e.g., $500)

2. **Profit Taking Trigger**
   - When portfolio value exceeds previous high by `PROFIT_INCREMENT` (e.g., $5)
   - Places market buy order for USDC equal to the increment amount
   - Example: At $505, bot buys $5 USDC
   - New high water mark is set to current value minus increment

3. **Cooldown Period**
   - 5-minute cooldown between profit-taking events
   - Prevents excessive trading during volatile periods

### Configuration Parameters

```python
STARTING_PORTFOLIO_INVESTMENT = 500.0  # Initial portfolio value for tracking
PROFIT_INCREMENT = 5                   # USD amount to convert to USDC
```

### Example Scenario

Starting with $500 portfolio:
1. Portfolio reaches $505
2. Bot places market order: Buy $5 USDC
3. New high water mark set to $500
4. 5-minute cooldown begins
5. Process repeats when portfolio reaches $505 again

### Email Notifications

Profit-taking events trigger email notifications including:
- Profit amount being taken
- Current portfolio value
- Previous portfolio high
- 15-minute cooldown between notifications

### Tips

- Adjust `PROFIT_INCREMENT` based on your trading volume
- Smaller increments = more frequent USDC purchases
- Larger increments = less frequent but bigger purchases
- Consider exchange fees when setting increment size

## Automated Staking

DioGrid now includes automatic staking functionality for US customers, following Kraken's reintroduction of staking services:

### How It Works

1. **Stakeable Asset Detection**
   - Bot continuously monitors for assets eligible for staking
   - Automatically identifies which holdings can be staked on Kraken

2. **Real-time Staking**
   - Checks for residual balances after grid orders are placed
   - Automatically sweeps stakeable assets to the earn staking wallet
   - Occurs in real-time as trades execute

3. **Accumulation Mode Integration**
   - When running in accumulation mode (PASSIVE_INCOME = 0)
   - Newly accumulated assets are automatically staked
   - Provides additional passive income through staking rewards

4. **Compound Returns**
   - Combines trading profits with staking rewards
   - Staking rewards automatically compound as they're earned
   - Maximizes overall portfolio yield through dual revenue streams

### Benefits

- Passive income from both trading and staking
- No manual staking management required
- Real-time asset optimization
- Automated compound returns
- Enhanced portfolio yield potential

## License

This project is licensed under the MIT License.

## Disclaimer

This software is provided for personal use and educational purposes only. It is not intended to provide financial advice, nor is it an invitation to engage in cryptocurrency trading. The authors of this software are not responsible for any financial losses or damages incurred while using the software. Users should consult with a licensed financial advisor before engaging in cryptocurrency trading. Cryptocurrency trading carries a high level of risk, and users should only trade with funds they can afford to lose.

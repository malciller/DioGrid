
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
MIN_PROFIT_USD = 0.01              # Minimum profit target per trade in USD, bot will subtract the difference from the sell quantities, and you'll begin accumulating holdings of the assets
KRAKEN_FEE = 0.002                 # Trading fee percentage
STARTING_PORTFOLIO_INVESTMENT = 500.0  # Initial investment for profit tracking
PROFIT_INCREMENT = 5               # USD amount for profit-taking orders
GRID_INTERVAL = 0.6               # Percentage between grid lines
GRID_INTERVAL_GRACE = 0.05        # Additional grace percentage for grid
TRADING_PAIRS = {
    "BTC/USD": 0.0001,            # Trading pairs and amounts
    #"SOL/USD": 0.04              # Commented example
}
```

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

## License

This project is licensed under the MIT License.

## Disclaimer

This software is provided for personal use and educational purposes only. It is not intended to provide financial advice, nor is it an invitation to engage in cryptocurrency trading. The authors of this software are not responsible for any financial losses or damages incurred while using the software. Users should consult with a licensed financial advisor before engaging in cryptocurrency trading. Cryptocurrency trading carries a high level of risk, and users should only trade with funds they can afford to lose.

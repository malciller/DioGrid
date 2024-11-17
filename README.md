
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


## Running Persistently in Background via Docker

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

3. **Verify the Container is Running**  
   You can check the status of your container by running:

   ```bash
   docker ps
   ```

   This will list all running containers. Ensure your container is listed with its name and status as `Up`.

4. **View Logs**  
   If you need to monitor logs, you can view them in real time by running:

   ```bash
   docker logs -f diogrid_container
   ```

5. **Stop the Container**  
   To stop the container when needed, use:

   ```bash
   docker stop diogrid_container
   ```

6. **Restart the Container**  
   If you want to restart the container later without rebuilding, run:

   ```bash
   docker start diogrid_container
   ```

7. **Remove the Container**  
   If you want to delete the container, first stop it (if running), then remove it:

   ```bash
   docker rm diogrid_container
   ```

Now your Docker container should run persistently in the background, ensuring the bot is always running.


### Key Features and Parameters:

- `trading_pairs`: Dictionary of cryptocurrency pairs and amount to trade.
- `grid_interval`: Percentage interval for placing buy/sell orders around the current price.


## License

This project is licensed under the MIT License.

## Disclaimer

This software is provided for personal use and educational purposes only. It is not intended to provide financial advice, nor is it an invitation to engage in cryptocurrency trading. The authors of this software are not responsible for any financial losses or damages incurred while using the software. Users should consult with a licensed financial advisor before engaging in cryptocurrency trading. Cryptocurrency trading carries a high level of risk, and users should only trade with funds they can afford to lose.

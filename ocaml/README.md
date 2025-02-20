# Diogrid Trading Bot

A market making bot for Kraken cryptocurrency exchange that automatically places and manages limit orders.

## Features

- Automated limit order placement and management
- Real-time price monitoring via WebSocket
- Configurable trading pairs and parameters
- Order amending to maintain optimal pricing
- Comprehensive logging and error handling

## Configuration

### 1. API Keys

Create a `.env` file in the root directory with your Kraken API credentials:

```env
KRAKEN_API_KEY=your_api_key_here
KRAKEN_API_SECRET=your_api_secret_here
```

Make sure your API key has the following permissions:
- Query Funds
- Query Open Orders & Trades
- Create & Modify Orders

### 2. Trading Pairs Configuration

Edit the `tracked_pairs` list in `diogrid/bin/main.ml` to configure your trading pairs:

```ocaml
let tracked_pairs = [
  ("BTC/USD", 0.75, 0.000875, 0.99);  (* Symbol, Grid Interval %, Order Quantity, Sell Multiplier *)
  ("ETH/USD", 3.0, 0.0045, 0.99);
  // Add more pairs as needed...
]
```

Each tuple contains:
- Trading pair symbol (e.g., "BTC/USD")
- Grid interval percentage (determines price spread)
- Order quantity (base currency amount)
- Sell multiplier (typically 0.99 for 1% less than buy quantity)

### 3. Build and Run

```bash
# Install dependencies
opam install . --deps-only

# Build the project
dune build

# Run the bot
dune exec diogrid
```

## Order Management

The bot will:
1. Place buy and sell limit orders for each configured pair
2. Monitor price movements in real-time
3. Amend orders when prices move significantly
4. Automatically replace filled orders

## Logging

The bot provides detailed logging:
- Order placements and amendments
- Price updates
- WebSocket connection status
- Errors and warnings

## Safety Features

- Post-only orders to ensure maker fees
- Configurable price deviation limits
- Automatic order price adjustment
- Connection retry mechanisms

## Error Handling

The bot includes robust error handling for:
- Network disconnections
- API errors
- Invalid configurations
- Order placement failures

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Disclaimer

Trading cryptocurrencies carries significant risk. This bot is provided as-is with no guarantees. Use at your own risk and always test with small amounts first.
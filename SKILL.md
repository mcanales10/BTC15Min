# BTC 15-Minute Momentum Trader

Automated trading bot for Polymarket's 15-minute "BTC Up or Down" markets.

## Key Features
* **Binance Spot Oracle:** Pulls 1-minute klines directly from Binance for real-time momentum calculation.
* **Dynamic Heartbeat:** Scans the market every 30 seconds. Once a trade is entered, it accelerates to a 1-second loop to actively monitor for Take Profit or Stop Loss conditions.
* **Capital Protection:** Strict spread thresholds, max exposure limits, and a daily hard-stop to prevent catastrophic losses.

**Note:** Deploys in Paper Trading mode by default. To trade live, add the `--live` flag to the Procfile.

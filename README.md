# Lighter.xyz Heikin Ashi Bot

Zero-fee perpetual futures trading bot on [Lighter Exchange](https://lighter.xyz) using the Heikin Ashi candle strategy.

## Strategy

- **Timeframe**: 1-minute candles
- **Entry**: Heikin Ashi candle closes **green** → open **long** | closes **red** → open **short**
- **Reversal**: Opposite signal closes current position and opens the opposite direction
- **Max Loss**: Position auto-closes if unrealized loss exceeds threshold
- **Leverage**: 30x
- **Margin**: $0.5–$1 per trade
- **Fees**: Zero (Lighter Exchange has no trading fees)

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Get your Lighter API keys

1. Go to [lighter.xyz](https://lighter.xyz)
2. Settings → API Keys → Create new key
3. Copy the **private key** and **API key index**
4. Find your **account index** (visible in dashboard or via SDK)

### 3. Configure environment variables

```bash
export LIGHTER_PRIVATE_KEY="your_api_private_key"
export LIGHTER_API_KEY_INDEX=2
export LIGHTER_ACCOUNT_INDEX=0
```

### 4. Run the bot

```bash
python bot.py
```

## Configuration

All settings are in `config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `BASE_URL` | testnet | Lighter endpoint (testnet/mainnet) |
| `MARKET_INDEX` | 0 | 0 = ETH perps |
| `TIMEFRAME` | 1m | Candle timeframe |
| `LEVERAGE` | 30 | Position leverage |
| `INITIAL_MARGIN_USD` | 0.5 | Margin per trade |
| `MAX_MARGIN_USD` | 1.0 | Max margin per trade |
| `MAX_LOSS_USD` | 0.5 | Close position at this loss |
| `HEARTBEAT_INTERVAL` | 30 | Stats print interval (seconds) |

## Deploy on Railway

1. Create a new Railway project from this repo
2. Set environment variables:
   - `LIGHTER_PRIVATE_KEY`
   - `LIGHTER_API_KEY_INDEX`
   - `LIGHTER_ACCOUNT_INDEX`
3. Deploy — bot runs 24/7

## Logs

The bot logs:
- Each HA candle close with signal (green/red)
- Entry and exit with position details
- Realized PnL per trade
- Running win rate and cumulative PnL every 30 seconds
- Reversal events
- Max loss triggers

## Files

- `bot.py` — Main bot loop and strategy logic
- `heikin_ashi.py` — Heikin Ashi candle calculation
- `lighter_client.py` — Lighter SDK wrapper (orders, positions, candles)
- `config.py` — All configuration

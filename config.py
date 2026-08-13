"""
config.py — Lighter.xyz Heikin Ashi Bot Configuration
"""

# ── Lighter Exchange ──────────────────────────────────────────
BASE_URL = "https://testnet.zklighter.elliot.ai"  # testnet
# BASE_URL = "https://mainnet.zklighter.elliot.ai"  # mainnet

# Your Lighter API credentials (from lighter.xyz dashboard)
API_KEY_INDEX = int(__import__('os').environ.get('LIGHTER_API_KEY_INDEX', '2'))
ACCOUNT_INDEX = int(__import__('os').environ.get('LIGHTER_ACCOUNT_INDEX', '0'))
PRIVATE_KEY = __import__('os').environ.get('LIGHTER_PRIVATE_KEY', '')

# ── Trading Config ────────────────────────────────────────────
MARKET_INDEX = 0          # 0 = ETH perps (check orderBookDetails for others)
TIMEFRAME = "1m"          # 1-minute candles
LEVERAGE = 30             # 30x leverage
INITIAL_MARGIN_USD = 0.5  # $0.5 margin per trade (position size = margin * leverage * price)
MAX_MARGIN_USD = 1.0      # max margin per trade ($1)
MAX_LOSS_USD = 0.5        # close position if unrealized loss exceeds this
MIN_TRADE_INTERVAL = 60  # minimum seconds between trades (1 candle)

# ── Heikin Ashi Strategy ──────────────────────────────────────
# Entry: HA candle closes green → long, red → short
# Exit: opposite signal closes current position and reverses

# ── Heartbeat ─────────────────────────────────────────────────
HEARTBEAT_INTERVAL = 30  # print stats every 30 seconds

"""
config.py — OKX demo configuration (replaces Lighter settings)
"""
import os

# OKX API credentials (set as environment variables)
OKX_API_KEY = os.environ.get('OKX_API_KEY', '')
OKX_SECRET = os.environ.get('OKX_SECRET', '')
OKX_PASSPHRASE = os.environ.get('OKX_PASSPHRASE', '')
OKX_TESTNET = os.environ.get('OKX_TESTNET', 'true').lower() in ('1', 'true', 'yes')

# Market symbol used by ccxt/OKX (change if you prefer another instrument)
# Examples: 'ETH/USDT', 'BTC/USDT'
MARKET = os.environ.get('MARKET', 'ETH/USDT')

# ── Trading Config (unchanged strategy parameters) ────────────
TIMEFRAME = "1m"          # 1-minute candles
LEVERAGE = 30               # 30x leverage (bot will request it; set in exchange if needed)
INITIAL_MARGIN_USD = 1.26   # initial margin per trade
MAX_MARGIN_USD = 2.0
MAX_LOSS_USD = 1.0
MIN_TRADE_INTERVAL = 60

# ── Heikin Ashi Strategy ──────────────────────────────────────
# Entry: HA candle closes green → long, red → short
# Exit: opposite signal closes current position and reverses

# ── Heartbeat ─────────────────────────────────────────────────
HEARTBEAT_INTERVAL = 30

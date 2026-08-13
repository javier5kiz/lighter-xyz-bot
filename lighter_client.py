"""
lighter_client.py — Wrapper around the Lighter SDK

Handles:
- Connection to Lighter testnet/mainnet
- Fetching candle data (OHLCV)
- Account info (balance, positions)
- Placing market orders (long/short)
- Closing positions
- Leverage setting
"""

import asyncio
import logging
from typing import Optional

try:
    import lighter
except ImportError:
    lighter = None

logger = logging.getLogger(__name__)


class LighterClient:
    """Thin wrapper around lighter SDK for trading."""

    def __init__(self, base_url: str, api_key_index: int, account_index: int, private_key: str):
        self.base_url = base_url
        self.api_key_index = api_key_index
        self.account_index = account_index
        self.private_key = private_key
        self._signer = None
        self._api = None

    async def connect(self):
        """Initialize signer and API clients."""
        if lighter is None:
            raise RuntimeError("lighter-sdk not installed. Run: pip install lighter-sdk")

        config = lighter.Configuration(host=self.base_url)
        self._api = lighter.ApiClient(config)

        # Initialize signer for placing orders
        self._signer = lighter.SignerClient(
            url=self.base_url,
            api_private_keys={self.api_key_index: self.private_key},
            account_index=self.account_index,
        )

        logger.info(f"Connected to {self.base_url}")
        return self

    async def close(self):
        """Cleanup connections."""
        if self._api:
            await self._api.close()
        if self._signer:
            await self._signer.close()

    # ── Market Data ───────────────────────────────────────────

    async def get_orderbook_details(self, market_index: int):
        """Get market metadata (tick size, lot size, decimals)."""
        api = lighter.OrderApi(self._api)
        resp = await api.order_book_details(market_id=market_index)
        return resp

    async def get_orderbook(self, market_index: int):
        """Get current orderbook (best bid/ask)."""
        api = lighter.OrderApi(self._api)
        resp = await api.order_books(market_ids=[market_index])
        return resp[0] if resp else None

    async def get_best_price(self, market_index: int) -> tuple[float, float]:
        """Returns (best_bid, best_ask) for the market."""
        book = await self.get_orderbook(market_index)
        if not book:
            return 0, 0
        # Parse orderbook — structure varies, extract best bid/ask
        bids = getattr(book, 'bids', [])
        asks = getattr(book, 'asks', [])
        best_bid = float(bids[0].price) if bids else 0
        best_ask = float(asks[0].price) if asks else 0
        return best_bid, best_ask

    async def get_candles(self, market_index: int, interval: str = "1m", limit: int = 100) -> list[dict]:
        """
        Fetch recent candles for Heikin Ashi calculation.
        Uses Lighter's marketPriceCharts or recentTrades endpoint.
        Falls back to constructing candles from trades if needed.
        """
        try:
            api = lighter.OrderApi(self._api)
            # Try market price charts endpoint
            resp = await api.market_price_charts(
                market_id=market_index,
                interval=interval,
                limit=limit,
            )
            if resp and hasattr(resp, 'candles') and resp.candles:
                candles = []
                for c in resp.candles:
                    candles.append({
                        'timestamp': int(getattr(c, 'time', 0)),
                        'open': float(getattr(c, 'open', 0)),
                        'high': float(getattr(c, 'high', 0)),
                        'low': float(getattr(c, 'low', 0)),
                        'close': float(getattr(c, 'close', 0)),
                        'volume': float(getattr(c, 'volume', 0)),
                    })
                return candles
        except Exception as e:
            logger.debug(f"marketPriceCharts failed: {e}")

        # Fallback: fetch recent trades and aggregate
        return await self._candles_from_trades(market_index, limit)

    async def _candles_from_trades(self, market_index: int, limit: int) -> list[dict]:
        """Build candles from recent trades as fallback."""
        try:
            api = lighter.OrderApi(self._api)
            resp = await api.recent_trades(market_id=market_index, limit=500)
            trades = getattr(resp, 'trades', []) or []

            if not trades:
                return []

            # Aggregate into 1-minute candles
            candles = {}
            for t in trades:
                ts = int(getattr(t, 'time', 0))
                minute_ts = (ts // 60) * 60
                price = float(getattr(t, 'price', 0))
                size = float(getattr(t, 'size', 0))

                if minute_ts not in candles:
                    candles[minute_ts] = {
                        'timestamp': minute_ts,
                        'open': price,
                        'high': price,
                        'low': price,
                        'close': price,
                        'volume': size,
                    }
                else:
                    c = candles[minute_ts]
                    c['high'] = max(c['high'], price)
                    c['low'] = min(c['low'], price)
                    c['close'] = price
                    c['volume'] += size

            return sorted(candles.values(), key=lambda x: x['timestamp'])[-limit:]
        except Exception as e:
            logger.error(f"Failed to build candles from trades: {e}")
            return []

    # ── Account ───────────────────────────────────────────────

    async def get_account(self):
        """Get account info (balance, positions, margin)."""
        api = lighter.AccountApi(self._api)
        resp = await api.account(index=self.account_index)
        return resp

    async def get_balance(self) -> float:
        """Get available USDC balance."""
        account = await self.get_account()
        # Balance structure varies — try common fields
        for field in ['collateral', 'balance', 'available_collateral', 'free_collateral']:
            val = getattr(account, field, None)
            if val is not None:
                return float(val)
        # Try nested
        if hasattr(account, 'margin_info'):
            return float(getattr(account.margin_info, 'free_collateral', 0))
        return 0.0

    async def get_position(self, market_index: int) -> Optional[dict]:
        """Get current position for a market. Returns None if no position."""
        try:
            account = await self.get_account()
            positions = getattr(account, 'positions', []) or []

            for pos in positions:
                if int(getattr(pos, 'market_id', -1)) == market_index:
                    size = float(getattr(pos, 'base_amount', 0))
                    if abs(size) < 1e-10:
                        continue
                    return {
                        'market_index': market_index,
                        'size': size,  # positive = long, negative = short
                        'entry_price': float(getattr(pos, 'entry_price', 0)),
                        'side': 'long' if size > 0 else 'short',
                    }
        except Exception as e:
            logger.debug(f"get_position error: {e}")
        return None

    # ── Leverage ──────────────────────────────────────────────

    async def set_leverage(self, market_index: int, leverage: int):
        """Set leverage for a market."""
        try:
            tx, tx_hash, err = await self._signer.update_leverage(
                market_index=market_index,
                leverage=leverage,
            )
            if err:
                logger.error(f"Set leverage failed: {err}")
            else:
                logger.info(f"Leverage set to {leverage}x for market {market_index}")
            return err is None
        except Exception as e:
            logger.error(f"Set leverage exception: {e}")
            return False

    # ── Orders ─────────────────────────────────────────────────

    async def market_order(self, market_index: int, is_buy: bool, base_amount: int,
                           reduce_only: bool = False, client_order_index: int = 0):
        """
        Place a market order.
        
        Args:
            market_index: market to trade
            is_buy: True for buy/long, False for sell/short
            base_amount: size in base units (int, scaled by market decimals)
            reduce_only: True to only reduce existing position
            client_order_index: unique order ID for tracking
        """
        try:
            tx, tx_hash, err = await self._signer.create_order(
                market_index=market_index,
                client_order_index=client_order_index,
                base_amount=base_amount,
                price=0,  # market order — worst acceptable price = 0 means accept any
                is_ask=not is_buy,  # ask = sell, bid = buy
                order_type=self._signer.ORDER_TYPE_MARKET,
                time_in_force=self._signer.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
                reduce_only=reduce_only,
                order_expiry=self._signer.DEFAULT_IOC_EXPIRY,
            )

            if err:
                logger.error(f"Order failed: {err}")
                return {'success': False, 'error': str(err)}

            logger.info(f"Order placed: tx_hash={tx_hash}")
            return {'success': True, 'tx_hash': tx_hash}
        except Exception as e:
            logger.error(f"Order exception: {e}")
            return {'success': False, 'error': str(e)}

    async def close_position(self, market_index: int, position: dict, client_order_index: int = 0):
        """Close an existing position with a reduce-only market order."""
        is_buy = position['size'] < 0  # close short = buy, close long = sell
        base_amount = abs(int(position['size']))

        return await self.market_order(
            market_index=market_index,
            is_buy=is_buy,
            base_amount=base_amount,
            reduce_only=True,
            client_order_index=client_order_index,
        )

    async def open_position(self, market_index: int, side: str, base_amount: int,
                            client_order_index: int = 0):
        """Open a new position (long or short)."""
        is_buy = side == 'long'
        return await self.market_order(
            market_index=market_index,
            is_buy=is_buy,
            base_amount=base_amount,
            reduce_only=False,
            client_order_index=client_order_index,
        )

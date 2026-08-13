"""
lighter_client.py — Wrapper around the Lighter SDK

Handles:
- Connection to Lighter testnet/mainnet
- Fetching candle data (OHLCV) via CandlestickApi
- Account info (balance, positions) via AccountApi
- Placing market orders via SignerClient
- Closing positions
- Setting leverage
"""

import asyncio
import logging
import time
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
        """Get current orderbook metadata (fees, min sizes)."""
        api = lighter.OrderApi(self._api)
        resp = await api.order_books(market_id=market_index)
        if resp and resp.order_books:
            return resp.order_books[0]
        return None

    async def get_best_price(self, market_index: int, is_ask: bool) -> int:
        """
        Get best bid or ask price from orderbook.
        Uses SignerClient.get_best_price which queries the orderbook.
        Returns price as integer (scaled by market's price decimals).
        """
        try:
            price = await self._signer.get_best_price(market_index, is_ask)
            return price
        except Exception as e:
            logger.error(f"get_best_price error: {e}")
            return 0

    async def get_candles(self, market_index: int, resolution: str = "1m", limit: int = 100) -> list[dict]:
        """
        Fetch candles via CandlestickApi.
        
        Args:
            market_index: market ID
            resolution: candle resolution ("1m", "5m", "1h", etc.)
            limit: max candles to fetch
            
        Returns:
            list of dicts: {timestamp, open, high, low, close, volume}
        """
        try:
            api = lighter.CandlestickApi(self._api)
            now_ms = int(time.time() * 1000)
            # Start timestamp: go back enough to get `limit` candles
            # For 1m candles, each candle = 60000ms, so go back limit*60000 + buffer
            start_ms = now_ms - (limit + 5) * 60_000
            
            resp = await api.candles(
                market_id=market_index,
                resolution=resolution,
                start_timestamp=start_ms,
                end_timestamp=now_ms,
                count_back=limit,
                set_timestamp_to_end=True,
            )
            
            if resp and resp.candles:
                candles = []
                for c in resp.candles:
                    candles.append({
                        'timestamp': int(c.t) // 1000,  # ms to seconds
                        'open': float(c.o),
                        'high': float(c.h),
                        'low': float(c.l),
                        'close': float(c.c),
                        'volume': float(c.v),
                    })
                return candles
        except Exception as e:
            logger.error(f"get_candles error: {e}")
        
        return []

    # ── Account ───────────────────────────────────────────────

    async def get_account(self):
        """Get account info by index."""
        api = lighter.AccountApi(self._api)
        resp = await api.account(by="index", value=str(self.account_index))
        if resp and resp.accounts and len(resp.accounts) > 0:
            return resp.accounts[0]
        return None

    async def get_balance(self) -> float:
        """Get available USDC balance."""
        account = await self.get_account()
        if not account:
            return 0.0
        # available_balance is a string like "100.5"
        try:
            return float(account.available_balance)
        except (ValueError, TypeError):
            return 0.0

    async def get_position(self, market_index: int) -> Optional[dict]:
        """Get current position for a market. Returns None if no position."""
        try:
            account = await self.get_account()
            if not account or not account.positions:
                return None

            for pos in account.positions:
                if int(pos.market_id) == market_index:
                    size = float(pos.position)
                    if abs(size) < 1e-10:
                        continue
                    return {
                        'market_index': market_index,
                        'size': size,
                        'entry_price': float(pos.avg_entry_price),
                        'side': 'long' if pos.sign > 0 else 'short',
                        'unrealized_pnl': float(pos.unrealized_pnl),
                        'allocated_margin': float(pos.allocated_margin),
                    }
        except Exception as e:
            logger.debug(f"get_position error: {e}")
        return None

    async def get_unrealized_pnl(self, market_index: int) -> float:
        """Get unrealized PnL for a market position."""
        pos = await self.get_position(market_index)
        if pos:
            return pos['unrealized_pnl']
        return 0.0

    # ── Leverage ──────────────────────────────────────────────

    async def set_leverage(self, market_index: int, leverage: int,
                           margin_mode: int = None):
        """Set leverage for a market. margin_mode: 1=ISOLATED, 0=CROSS."""
        if margin_mode is None:
            margin_mode = lighter.SignerClient.ISOLATED_MARGIN_MODE
        try:
            tx, tx_hash, err = await self._signer.update_leverage(
                market_index=market_index,
                margin_mode=margin_mode,
                leverage=leverage,
            )
            if err:
                logger.error(f"Set leverage failed: {err}")
            else:
                logger.info(f"Leverage set to {leverage}x (margin_mode={margin_mode}) for market {market_index}")
            return err is None
        except Exception as e:
            logger.error(f"Set leverage exception: {e}")
            return False

    # ── Orders ─────────────────────────────────────────────────

    async def market_order(self, market_index: int, is_buy: bool, base_amount: int,
                           avg_execution_price: int = 0,
                           reduce_only: bool = False, client_order_index: int = 0):
        """
        Place a market order via create_market_order.
        
        Args:
            market_index: market to trade
            is_buy: True for buy/long, False for sell/short
            base_amount: size in base units (int, scaled by market decimals)
            avg_execution_price: worst acceptable price (0 = accept any)
            reduce_only: True to only reduce existing position
            client_order_index: unique order ID for tracking
        """
        try:
            tx, tx_hash, err = await self._signer.create_market_order(
                market_index=market_index,
                client_order_index=client_order_index,
                base_amount=base_amount,
                avg_execution_price=avg_execution_price,
                is_ask=not is_buy,
                reduce_only=reduce_only,
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

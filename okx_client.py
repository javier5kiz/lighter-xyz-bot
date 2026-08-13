"""
okx_client.py — Minimal async wrapper around ccxt.okx for demo/testnet trading

This adapts the methods used by the bot (connect, get_candles, get_orderbook, get_best_price,
get_balance, get_position, set_leverage, market_order, close_position, open_position) to an
OKX (ccxt) backend. The goal is to be a drop-in replacement for the previous lighter client
interface used by bot.py.

Notes:
- This is intended for demo/testnet use. Do NOT commit real API keys.
- Error handling is defensive; inspect logs when something fails.
"""

import logging
import asyncio
from types import SimpleNamespace

try:
    import ccxt.async_support as ccxt
except Exception:
    ccxt = None

logger = logging.getLogger(__name__)


class OkxClient:
    def __init__(self, api_key: str, secret: str, passphrase: str, testnet: bool, market: str):
        self.api_key = api_key
        self.secret = secret
        self.passphrase = passphrase
        self.testnet = testnet
        self._exchange = None
        self._market = market  # symbol string like "ETH/USDT"
        self._market_info = None

    async def connect(self):
        if ccxt is None:
            raise RuntimeError("ccxt.async_support is not installed. Run: pip install ccxt")

        self._exchange = ccxt.okx({
            'apiKey': self.api_key,
            'secret': self.secret,
            'password': self.passphrase,
            'enableRateLimit': True,
        })

        if self.testnet:
            try:
                # Use sandbox mode for OKX (ccxt helper)
                self._exchange.set_sandbox_mode(True)
            except Exception:
                logger.warning("Could not set sandbox mode on exchange client; continue anyway")

        # load markets to populate precisions/limits
        try:
            await self._exchange.load_markets()
            self._market_info = self._exchange.markets.get(self._market)
            logger.info(f"Connected to OKX (testnet={self.testnet}) for market {self._market}")
        except Exception as e:
            logger.error(f"Failed to load markets: {e}")
            raise

        return self

    async def close(self):
        if self._exchange:
            try:
                await self._exchange.close()
            except Exception:
                pass

    # ── Market Data ───────────────────────────────────────────
    async def _get_market(self):
        if not self._market_info:
            try:
                await self._exchange.load_markets()
                self._market_info = self._exchange.markets.get(self._market)
            except Exception:
                self._market_info = None
        return self._market_info

    async def get_orderbook_details(self, market_index: str):
        """Return a SimpleNamespace with attributes used by the bot:
           supported_price_decimals, supported_size_decimals, min_base_amount
        """
        market = await self._get_market()
        if not market:
            return None

        price_dec = market.get('precision', {}).get('price', 8)
        size_dec = market.get('precision', {}).get('amount', 8)
        min_base = market.get('limits', {}).get('amount', {}).get('min', 0.0)
        return SimpleNamespace(
            supported_price_decimals=price_dec,
            supported_size_decimals=size_dec,
            min_base_amount=str(min_base),
        )

    async def get_orderbook(self, market_index: str):
        # For compatibility, return an object similar to the previous client
        return await self.get_orderbook_details(market_index)

    async def get_best_price(self, market_index: str, is_ask: bool) -> int:
        """Return price as integer scaled by market price decimals (same convention as lighter client)."""
        try:
            ob = await self._exchange.fetch_order_book(market_index, limit=5)
            market = await self._get_market()
            price_dec = market.get('precision', {}).get('price', 8)

            if is_ask:
                asks = ob.get('asks') or []
                price = asks[0][0] if asks else 0
            else:
                bids = ob.get('bids') or []
                price = bids[0][0] if bids else 0

            return int(price * (10 ** price_dec)) if price else 0
        except Exception as e:
            logger.error(f"get_best_price error: {e}")
            return 0

    async def get_candles(self, market_index: str, resolution: str = "1m", limit: int = 100) -> list:
        try:
            ohlcv = await self._exchange.fetch_ohlcv(market_index, timeframe=resolution, limit=limit)
            candles = []
            for o in ohlcv:
                # o = [timestamp_ms, open, high, low, close, volume]
                candles.append({
                    'timestamp': int(o[0] // 1000),
                    'open': float(o[1]),
                    'high': float(o[2]),
                    'low': float(o[3]),
                    'close': float(o[4]),
                    'volume': float(o[5]),
                })
            return candles
        except Exception as e:
            logger.error(f"get_candles error: {e}")
            return []

    # ── Account ───────────────────────────────────────────────
    async def get_balance(self) -> float:
        try:
            bal = await self._exchange.fetch_balance()
            # prefer USDT
            if 'USDT' in bal.get('free', {}):
                return float(bal['free']['USDT'] or 0.0)
            # fallback: sum all fiat/quote currencies
            totals = 0.0
            for k, v in bal.get('free', {}).items():
                try:
                    totals += float(v or 0.0)
                except Exception:
                    pass
            return totals
        except Exception as e:
            logger.error(f"get_balance error: {e}")
            return 0.0

    async def get_position(self, market_index: str):
        """Attempt to find an open position for the given market. Returns a dict similar to the lighter client."""
        try:
            # ccxt may implement fetch_positions for derivatives
            try:
                positions = await self._exchange.fetch_positions([market_index])
            except Exception:
                # fallback to fetching all positions and filtering
                positions = await self._exchange.fetch_positions()

            if not positions:
                return None

            for p in positions:
                # p may be a dict with 'symbol'
                sym = p.get('symbol') or p.get('info', {}).get('instId')
                if sym == market_index or str(sym).startswith(str(market_index)):
                    size = float(p.get('contracts') or p.get('positionAmt') or 0)
                    entry = float(p.get('entryPrice') or p.get('avgEntryPrice') or p.get('price') or 0)
                    unreal = float(p.get('unrealizedPnl') or p.get('unrealized_pnl') or 0)
                    side = 'long' if size > 0 else 'short'
                    return {
                        'market_index': market_index,
                        'size': size,
                        'entry_price': entry,
                        'side': side,
                        'unrealized_pnl': unreal,
                        'allocated_margin': 0.0,
                    }
        except Exception as e:
            logger.debug(f"get_position error: {e}")
        return None

    async def set_leverage(self, market_index: str, leverage: int, margin_mode: int = None):
        # Implementing leverage via CCXT/OKX private endpoints varies by account type.
        # For now, log and continue — user can set leverage in OKX UI or we can extend this later.
        logger.info(f"(okx-client) set_leverage: requested {leverage}x for {market_index} — no-op in this adapter")
        return True

    # ── Orders ─────────────────────────────────────────────────
    async def market_order(self, market_index: str, is_buy: bool, base_amount: int,
                           avg_execution_price: int = 0, reduce_only: bool = False, client_order_index: int = 0):
        try:
            market = await self._get_market()
            size_dec = market.get('precision', {}).get('amount', 8)
            amount = float(base_amount) / (10 ** size_dec)
            side = 'buy' if is_buy else 'sell'
            params = {}
            if reduce_only:
                params['reduce_only'] = True

            order = await self._exchange.create_order(symbol=market_index, type='market', side=side, amount=amount, params=params)
            logger.info(f"Order placed: {order}")
            return {'success': True, 'order': order}
        except Exception as e:
            logger.error(f"Order exception: {e}")
            return {'success': False, 'error': str(e)}

    async def close_position(self, market_index: str, position: dict, client_order_index: int = 0, size_decimals: int = 8):
        is_buy = position.get('size', 0) < 0
        base_amount = int(abs(position.get('size', 0)) * (10 ** size_decimals))
        # try to pick a reasonable price (not used for market order here)
        try:
            return await self.market_order(market_index, is_buy=is_buy, base_amount=base_amount, avg_execution_price=0, reduce_only=True, client_order_index=client_order_index)
        except Exception as e:
            logger.error(f"close_position error: {e}")
            return {'success': False, 'error': str(e)}

    async def open_position(self, market_index: str, side: str, base_amount: int, client_order_index: int = 0, avg_execution_price: int = 0):
        is_buy = side == 'long'
        return await self.market_order(market_index, is_buy=is_buy, base_amount=base_amount, avg_execution_price=avg_execution_price, reduce_only=False, client_order_index=client_order_index)

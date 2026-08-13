"""
bot.py — Lighter.xyz Heikin Ashi Bot

Strategy:
  - 1-minute Heikin Ashi candles
  - HA candle closes GREEN → close any short, open LONG
  - HA candle closes RED → close any long, open SHORT
  - 30x leverage, $0.5-$1 margin per trade
  - Position reverses on opposite signal
  - Max loss closes position

Lighter Exchange = zero fees, so this works clean.
"""

import asyncio
import logging
import time
import os
from typing import Optional

import config
from heikin_ashi import HeikinAshi
from lighter_client import LighterClient

# ── Logging Setup ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

# ── Stats ──────────────────────────────────────────────────────
class Stats:
    def __init__(self):
        self.trades = 0          # total completed trades
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0    # realized PnL in USD
        self.long_trades = 0
        self.short_trades = 0
        self.reversals = 0      # trades closed by reversal signal
        self.max_loss_closes = 0
        self.errors = 0
        self.candles_checked = 0
        self.last_heartbeat = 0
        self.last_signal = None  # 'long' or 'short'

    def win_rate(self) -> float:
        filled = self.wins + self.losses
        return (self.wins / filled * 100) if filled > 0 else 0.0

    def summary(self) -> str:
        filled = self.wins + self.losses
        wr = self.win_rate()
        sign = '+' if self.total_pnl >= 0 else ''
        return (
            f"Trades: {self.trades} | W:{self.wins} L:{self.losses} | "
            f"WR: {wr:.1f}% | PnL: {sign}${self.total_pnl:.4f} | "
            f"Reversals: {self.reversals} | MaxLoss: {self.max_loss_closes} | "
            f"Errors: {self.errors}"
        )


# ── Bot ────────────────────────────────────────────────────────
class HeikinAshiBot:
    def __init__(self):
        self.ha = HeikinAshi()
        self.stats = Stats()
        self.client: Optional[LighterClient] = None
        self.current_position: Optional[dict] = None
        self.order_counter = 0
        self.last_candle_time = 0
        self.last_trade_time = 0

    def next_order_index(self) -> int:
        self.order_counter += 1
        return self.order_counter

    async def run(self):
        """Main bot loop."""
        self._print_banner()

        # Validate config
        if not config.PRIVATE_KEY:
            logger.error("LIGHTER_PRIVATE_KEY not set. Add your Lighter API private key.")
            logger.error("Get it from: https://lighter.xyz → Settings → API Keys")
            return

        # Connect
        self.client = LighterClient(
            base_url=config.BASE_URL,
            api_key_index=config.API_KEY_INDEX,
            account_index=config.ACCOUNT_INDEX,
            private_key=config.PRIVATE_KEY,
        )
        await self.client.connect()

        # Set leverage
        await self.client.set_leverage(config.MARKET_INDEX, config.LEVERAGE)

        # Check balance
        balance = await self.client.get_balance()
        logger.info(f"💰 Account balance: ${balance:.2f}")

        if balance < config.INITIAL_MARGIN_USD:
            logger.error(f"Balance ${balance:.2f} < min margin ${config.INITIAL_MARGIN_USD}")
            return

        # Get market details
        book_details = await self.client.get_orderbook_details(config.MARKET_INDEX)
        logger.info(f"📈 Market {config.MARKET_INDEX} ready")

        logger.info("🚀 Bot started. Waiting for 1-min HA candle closes...\n")

        # Main loop
        try:
            while True:
                await self._tick()
                await asyncio.sleep(2)  # poll every 2s
        except KeyboardInterrupt:
            logger.info("Stopping bot...")
        finally:
            await self.client.close()
            self._print_final_summary()

    async def _tick(self):
        """One polling cycle."""
        try:
            now = time.time()

            # 1. Fetch candles
            candles = await self.client.get_candles(config.MARKET_INDEX, "1m", limit=20)
            if not candles or len(candles) < 2:
                return

            self.stats.candles_checked += 1

            # 2. Get latest CLOSED candle (skip the still-forming one)
            # A candle is "closed" if its timestamp + 60s < now
            closed_candles = [c for c in candles if c['timestamp'] + 60 <= now]
            if not closed_candles:
                return

            latest_closed = closed_candles[-1]

            # Only act on new candle closes
            if latest_closed['timestamp'] <= self.last_candle_time:
                return

            self.last_candle_time = latest_closed['timestamp']

            # 3. Calculate Heikin Ashi signal from closed candles
            signal = self.ha.latest_signal(closed_candles[-10:])
            if not signal:
                return

            # Log the new candle
            ha_candles = self.ha.calculate(closed_candles[-5:])
            last_ha = ha_candles[-1] if ha_candles else None
            if last_ha:
                candle_type = "🟢 GREEN" if last_ha['is_green'] else "🔴 RED"
                logger.info(
                    f"📊 Candle closed | {candle_type} | "
                    f"HA O:{last_ha['ha_open']:.2f} C:{last_ha['ha_close']:.2f} | "
                    f"Signal: {signal.upper()}"
                )

            # 4. Check for reversal or new entry
            await self._handle_signal(signal)

            # 5. Check max loss on open position
            if self.current_position:
                await self._check_max_loss()

            # 6. Heartbeat
            if now - self.stats.last_heartbeat >= config.HEARTBEAT_INTERVAL:
                self.stats.last_heartbeat = now
                await self._heartbeat()

        except Exception as e:
            logger.error(f"Tick error: {e}")
            self.stats.errors += 1

    async def _handle_signal(self, signal: str):
        """Handle a new HA signal — reverse or open position."""
        # Refresh position from API
        self.current_position = await self.client.get_position(config.MARKET_INDEX)

        if self.current_position:
            pos_side = self.current_position['side']

            # Same direction → do nothing
            if (signal == 'long' and pos_side == 'long') or \
               (signal == 'short' and pos_side == 'short'):
                return

            # Opposite signal → close current, open new
            logger.info(f"🔄 REVERSAL: {pos_side} → {signal.upper()}")
            self.stats.reversals += 1

            # Close current position
            close_result = await self.client.close_position(
                config.MARKET_INDEX,
                self.current_position,
                self.next_order_index(),
            )

            if close_result['success']:
                # Calculate realized PnL
                pnl = await self._calculate_realized_pnl(self.current_position)
                self._record_trade(pnl)
                logger.info(f"   Closed {pos_side} | PnL: {'+' if pnl >= 0 else ''}${pnl:.4f}")
            else:
                logger.error(f"   Close failed: {close_result.get('error', 'unknown')}")
                return

            # Brief delay for order to settle
            await asyncio.sleep(1)

        # Open new position
        await self._open_position(signal)

    async def _open_position(self, side: str):
        """Open a new position in the given direction."""
        # Get orderbook for sizing info
        book = await self.client.get_orderbook(config.MARKET_INDEX)
        if not book:
            logger.error("No orderbook data — cannot size order")
            return

        # Get best price from signer (returns int scaled by price decimals)
        is_ask = side == 'short'  # short = sell = ask side
        best_price = await self.client.get_best_price(config.MARKET_INDEX, is_ask=(not (side == 'long')))
        
        # Price decimals from orderbook
        price_decimals = book.supported_price_decimals
        size_decimals = book.supported_size_decimals
        min_base = int(book.min_base_amount)
        
        # Convert price to human readable
        price = best_price / (10 ** price_decimals) if best_price > 0 else 0
        if price <= 0:
            logger.error("No price data — cannot size order")
            return

        # Position sizing: margin * leverage / price
        margin = config.INITIAL_MARGIN_USD
        notional = margin * config.LEVERAGE  # $0.5 * 30x = $15 notional
        base_amount_raw = notional / price

        # Convert to integer base units (scaled by size decimals)
        base_amount = int(base_amount_raw * (10 ** size_decimals))
        if base_amount < min_base:
            base_amount = min_base

        cost = base_amount / (10 ** size_decimals) * price
        logger.info(
            f"🎯 OPEN {side.upper()} | Size: {base_amount / (10 ** size_decimals):.6f} ({base_amount} units) | "
            f"~${cost:.2f} notional | {config.LEVERAGE}x | Margin: ${margin:.2f}"
        )

        result = await self.client.open_position(
            config.MARKET_INDEX, side, base_amount, self.next_order_index(),
        )

        if result['success']:
            logger.info(f"   ✅ Opened {side} at market")
            if side == 'long':
                self.stats.long_trades += 1
            else:
                self.stats.short_trades += 1
            self.stats.last_signal = side
            self.last_trade_time = time.time()
        else:
            logger.error(f"   ❌ Open failed: {result.get('error', 'unknown')}")
            self.stats.errors += 1

    async def _check_max_loss(self):
        """Close position if unrealized loss exceeds max loss."""
        if not self.current_position:
            return

        # Refresh position to get latest unrealized PnL
        self.current_position = await self.client.get_position(config.MARKET_INDEX)
        if not self.current_position:
            return

        unrealized = self.current_position.get('unrealized_pnl', 0.0)
        if unrealized < -config.MAX_LOSS_USD:
            logger.warning(
                f"🛑 MAX LOSS triggered: unrealized ${unrealized:.4f} < "
                f"-${config.MAX_LOSS_USD}"
            )
            result = await self.client.close_position(
                config.MARKET_INDEX,
                self.current_position,
                self.next_order_index(),
            )
            if result['success']:
                pnl = await self._calculate_realized_pnl(self.current_position)
                self._record_trade(pnl)
                self.stats.max_loss_closes += 1
                logger.info(f"   Closed at max loss | PnL: ${pnl:.4f}")
            self.current_position = None

    async def _calculate_unrealized_pnl(self, position: dict) -> float:
        """Get unrealized PnL from API (already calculated by Lighter)."""
        return position.get('unrealized_pnl', 0.0)

    async def _calculate_realized_pnl(self, position: dict) -> float:
        """Calculate realized PnL from the position's unrealized PnL at close time."""
        return position.get('unrealized_pnl', 0.0)

    def _record_trade(self, pnl: float):
        """Record a completed trade."""
        self.stats.trades += 1
        self.stats.total_pnl += pnl
        if pnl >= 0:
            self.stats.wins += 1
        else:
            self.stats.losses += 1

    async def _heartbeat(self):
        """Print running stats."""
        pos_str = "FLAT"
        unrealized = 0.0

        if self.current_position:
            unrealized = self.current_position.get('unrealized_pnl', 0.0)
            pos_str = f"{self.current_position['side'].upper()} @ {self.current_position['entry_price']:.2f} (uPnL: ${unrealized:.4f})"

        balance = await self.client.get_balance()
        sign = '+' if self.stats.total_pnl >= 0 else ''
        logger.info(f"💓 {self.stats.summary()} | Balance: ${balance:.2f}")
        logger.info(
            f"   Position: {pos_str} | "
            f"Candles: {self.stats.candles_checked} | "
            f"Long: {self.stats.long_trades} Short: {self.stats.short_trades}"
        )

    def _print_banner(self):
        print("\n" + "═" * 60)
        print("  LIGHTER.XYZ HEIKIN ASHI BOT")
        print("═" * 60)
        print(f"  Network: {'TESTNET' if 'testnet' in config.BASE_URL else 'MAINNET'}")
        print(f"  Market:  {config.MARKET_INDEX} (0=ETH perps)")
        print(f"  Strategy: HA 1m — green→long, red→short")
        print(f"  Leverage: {config.LEVERAGE}x")
        print(f"  Margin: ${config.INITIAL_MARGIN_USD}-{config.MAX_MARGIN_USD} per trade")
        print(f"  Max Loss: ${config.MAX_LOSS_USD}")
        print(f"  Reversal: opposite signal closes & reverses")
        print("─" * 60)

    def _print_final_summary(self):
        print("\n" + "═" * 60)
        print("  FINAL SUMMARY")
        print("═" * 60)
        print(f"  {self.stats.summary()}")
        if self.stats.trades > 0:
            print(f"  Long trades:  {self.stats.long_trades}")
            print(f"  Short trades: {self.stats.short_trades}")
            print(f"  Reversals:    {self.stats.reversals}")
            print(f"  MaxLoss closes: {self.stats.max_loss_closes}")
            avg_pnl = self.stats.total_pnl / self.stats.trades
            print(f"  Avg PnL/trade: ${avg_pnl:.4f}")
        print("═" * 60 + "\n")


# ── Entry Point ────────────────────────────────────────────────
if __name__ == "__main__":
    bot = HeikinAshiBot()
    asyncio.run(bot.run())

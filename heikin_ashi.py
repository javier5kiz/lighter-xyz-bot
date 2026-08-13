"""
heikin_ashi.py — Heikin Ashi candle calculation

Converts standard OHLC candles to Heikin Ashi candles.
HA candle is green (bullish) if close > open, red (bearish) if close < open.
"""

class HeikinAshi:
    """Maintains running Heikin Ashi candle state."""

    def __init__(self):
        self.prev_ha_open = None
        self.prev_ha_close = None

    def calculate(self, candles):
        """
        Convert list of standard OHLC candles to Heikin Ashi.
        
        Args:
            candles: list of dicts with keys: open, high, low, close, volume, timestamp
            
        Returns:
            list of HA candles with same keys + ha_open, ha_close, ha_high, ha_low, is_green
        """
        ha_candles = []
        prev_open = self.prev_ha_open
        prev_close = self.prev_ha_close

        for c in candles:
            o, h, l, cl = c['open'], c['high'], c['low'], c['close']

            if prev_open is None:
                # First candle — seed with (o+cl)/2
                ha_open = (o + cl) / 2
            else:
                ha_open = (prev_open + prev_close) / 2

            ha_close = (o + h + l + cl) / 4
            ha_high = max(h, ha_open, ha_close)
            ha_low = min(l, ha_open, ha_close)

            is_green = ha_close >= ha_open

            ha_candles.append({
                **c,
                'ha_open': ha_open,
                'ha_close': ha_close,
                'ha_high': ha_high,
                'ha_low': ha_low,
                'is_green': is_green,
            })

            prev_open = ha_open
            prev_close = ha_close

        # Save state for next batch
        if ha_candles:
            self.prev_ha_open = prev_open
            self.prev_ha_close = prev_close

        return ha_candles

    def latest_signal(self, candles):
        """
        Get the signal from the latest closed HA candle.
        
        Returns:
            'long' if green candle, 'short' if red candle, None if no candles
        """
        ha = self.calculate(candles)
        if not ha:
            return None
        
        last = ha[-1]
        if last['is_green']:
            return 'long'
        else:
            return 'short'

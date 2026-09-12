"""Real external-source adapters."""

from stock_data_center.ingestion.adapters.daily_market import (
    DailyMarketAdapter,
    TPExDailyMarketAdapter,
    TWSEDailyMarketAdapter,
)

__all__ = [
    "DailyMarketAdapter",
    "TPExDailyMarketAdapter",
    "TWSEDailyMarketAdapter",
]

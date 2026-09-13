"""Real external-source adapters."""

from stock_data_center.ingestion.adapters.daily_market import (
    DailyMarketAdapter,
    TPExDailyMarketAdapter,
    TWSEDailyMarketAdapter,
)
from stock_data_center.ingestion.adapters.security_metadata import (
    SecurityMetadataAdapter,
    TPExSecurityMetadataAdapter,
    TWSESecurityMetadataAdapter,
)

__all__ = [
    "DailyMarketAdapter",
    "SecurityMetadataAdapter",
    "TPExDailyMarketAdapter",
    "TPExSecurityMetadataAdapter",
    "TWSEDailyMarketAdapter",
    "TWSESecurityMetadataAdapter",
]

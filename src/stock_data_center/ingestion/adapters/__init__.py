"""Real external-source adapters."""

from stock_data_center.ingestion.adapters.daily_market import (
    DailyMarketAdapter,
    TPExDailyMarketAdapter,
    TWSEDailyMarketAdapter,
)
from stock_data_center.ingestion.adapters.security_lifecycle import (
    SecurityLifecycleAdapter,
    TPExDelistingHistoryAdapter,
    TPExListingHistoryAdapter,
    TWSEDelistingHistoryAdapter,
    TWSEListingHistoryAdapter,
)
from stock_data_center.ingestion.adapters.security_metadata import (
    SecurityMetadataAdapter,
    TPExSecurityMetadataAdapter,
    TWSESecurityMetadataAdapter,
)
from stock_data_center.ingestion.adapters.trading_calendar import (
    TradingCalendarAdapter,
    TWSETradingCalendarAdapter,
)
from stock_data_center.ingestion.adapters.whole_market_daily import (
    TPExWholeMarketDailyAdapter,
    TWSEWholeMarketDailyAdapter,
    WholeMarketDailyAdapter,
)

__all__ = [
    "DailyMarketAdapter",
    "SecurityLifecycleAdapter",
    "SecurityMetadataAdapter",
    "TradingCalendarAdapter",
    "TPExDailyMarketAdapter",
    "TPExDelistingHistoryAdapter",
    "TPExListingHistoryAdapter",
    "TPExSecurityMetadataAdapter",
    "TWSEDailyMarketAdapter",
    "TWSEDelistingHistoryAdapter",
    "TWSEListingHistoryAdapter",
    "TWSESecurityMetadataAdapter",
    "TPExWholeMarketDailyAdapter",
    "TWSETradingCalendarAdapter",
    "TWSEWholeMarketDailyAdapter",
    "WholeMarketDailyAdapter",
]

"""Real external-source adapters."""

from stock_data_center.ingestion.adapters.corporate_action import (
    CorporateActionListAdapter,
    TPExETFReverseSplitAdapter,
    TPExETFSplitAdapter,
    TPExExRightDailyAdapter,
    TPExParValueChangeAdapter,
    TPExReductionAdapter,
    TWSEDividendDetailAdapter,
    TWSEETFSplitAdapter,
    TWSEExRightAdapter,
    TWSEParValueChangeAdapter,
    TWSEReductionAdapter,
    TWSEReductionDetailAdapter,
)
from stock_data_center.ingestion.adapters.daily_market import (
    DailyMarketAdapter,
    TPExDailyMarketAdapter,
    TWSEDailyMarketAdapter,
)
from stock_data_center.ingestion.adapters.foreign_holding import (
    ForeignHoldingAdapter,
    MOPSForeignHoldingAdapter,
    TWSEForeignHoldingAdapter,
)
from stock_data_center.ingestion.adapters.institutional_investor import (
    InstitutionalInvestorAdapter,
    TPExInstitutionalInvestorAdapter,
    TWSEInstitutionalInvestorAdapter,
)
from stock_data_center.ingestion.adapters.institutional_summary import (
    InstitutionalMarketSummaryAdapter,
    TPExInstitutionalMarketSummaryAdapter,
    TWSEInstitutionalMarketSummaryAdapter,
)
from stock_data_center.ingestion.adapters.market_index import (
    MarketIndexAdapter,
    TPExMarketIndexAdapter,
    TWSEMarketIndexAdapter,
    TWSETaiexHistoryAdapter,
)
from stock_data_center.ingestion.adapters.official_valuation import (
    OfficialValuationAdapter,
    TPExOfficialValuationAdapter,
    TWSEOfficialValuationAdapter,
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
    "CorporateActionListAdapter",
    "DailyMarketAdapter",
    "ForeignHoldingAdapter",
    "InstitutionalInvestorAdapter",
    "MOPSForeignHoldingAdapter",
    "InstitutionalMarketSummaryAdapter",
    "MarketIndexAdapter",
    "OfficialValuationAdapter",
    "SecurityLifecycleAdapter",
    "SecurityMetadataAdapter",
    "TradingCalendarAdapter",
    "TPExDailyMarketAdapter",
    "TPExDelistingHistoryAdapter",
    "TPExETFReverseSplitAdapter",
    "TPExETFSplitAdapter",
    "TPExExRightDailyAdapter",
    "TPExInstitutionalInvestorAdapter",
    "TWSEForeignHoldingAdapter",
    "TPExInstitutionalMarketSummaryAdapter",
    "TPExListingHistoryAdapter",
    "TPExSecurityMetadataAdapter",
    "TWSEDailyMarketAdapter",
    "TWSEDelistingHistoryAdapter",
    "TWSEDividendDetailAdapter",
    "TWSEETFSplitAdapter",
    "TWSEExRightAdapter",
    "TWSEInstitutionalInvestorAdapter",
    "TWSEInstitutionalMarketSummaryAdapter",
    "TWSEListingHistoryAdapter",
    "TWSESecurityMetadataAdapter",
    "TPExMarketIndexAdapter",
    "TPExOfficialValuationAdapter",
    "TPExParValueChangeAdapter",
    "TPExReductionAdapter",
    "TPExWholeMarketDailyAdapter",
    "TWSETradingCalendarAdapter",
    "TWSEMarketIndexAdapter",
    "TWSEOfficialValuationAdapter",
    "TWSEParValueChangeAdapter",
    "TWSEReductionAdapter",
    "TWSEReductionDetailAdapter",
    "TWSETaiexHistoryAdapter",
    "TWSEWholeMarketDailyAdapter",
    "WholeMarketDailyAdapter",
]

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
from stock_data_center.ingestion.adapters.foreign_holding import (
    ForeignHoldingAdapter,
    MOPSForeignHoldingAdapter,
    TPExInstiQfiiForeignHoldingAdapter,
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
from stock_data_center.ingestion.adapters.margin_trading import (
    TPExMarginTradingAdapter,
    TWSEMarginTradingAdapter,
)
from stock_data_center.ingestion.adapters.market_index import (
    MarketIndexAdapter,
    TPExMarketIndexAdapter,
    TWSEMarketIndexAdapter,
    TWSETaiexHistoryAdapter,
)
from stock_data_center.ingestion.adapters.monthly_revenue import (
    MOPSOtcMonthlyRevenueAdapter,
    MOPSSiiMonthlyRevenueAdapter,
)
from stock_data_center.ingestion.adapters.official_valuation import (
    OfficialValuationAdapter,
    TPExOfficialValuationAdapter,
    TWSEOfficialValuationAdapter,
)
from stock_data_center.ingestion.adapters.securities_lending import (
    TPExSecuritiesLendingAdapter,
    TWSESecuritiesLendingAdapter,
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
    "ForeignHoldingAdapter",
    "InstitutionalInvestorAdapter",
    "InstitutionalMarketSummaryAdapter",
    "MOPSForeignHoldingAdapter",
    "MOPSOtcMonthlyRevenueAdapter",
    "MOPSSiiMonthlyRevenueAdapter",
    "MarketIndexAdapter",
    "OfficialValuationAdapter",
    "TPExETFReverseSplitAdapter",
    "TPExETFSplitAdapter",
    "TPExExRightDailyAdapter",
    "TPExInstiQfiiForeignHoldingAdapter",
    "TPExInstitutionalInvestorAdapter",
    "TPExInstitutionalMarketSummaryAdapter",
    "TPExMarginTradingAdapter",
    "TPExMarketIndexAdapter",
    "TPExOfficialValuationAdapter",
    "TPExParValueChangeAdapter",
    "TPExReductionAdapter",
    "TPExSecuritiesLendingAdapter",
    "TPExWholeMarketDailyAdapter",
    "TWSEDividendDetailAdapter",
    "TWSEETFSplitAdapter",
    "TWSEExRightAdapter",
    "TWSEForeignHoldingAdapter",
    "TWSEInstitutionalInvestorAdapter",
    "TWSEInstitutionalMarketSummaryAdapter",
    "TWSEMarginTradingAdapter",
    "TWSEMarketIndexAdapter",
    "TWSEOfficialValuationAdapter",
    "TWSEParValueChangeAdapter",
    "TWSEReductionAdapter",
    "TWSEReductionDetailAdapter",
    "TWSESecuritiesLendingAdapter",
    "TWSETaiexHistoryAdapter",
    "TWSETradingCalendarAdapter",
    "TWSEWholeMarketDailyAdapter",
    "TradingCalendarAdapter",
    "WholeMarketDailyAdapter",
]

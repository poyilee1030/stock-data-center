// Which exchange a daily source publishes for. A stock that moved market has a
// history on each, shown apart and never merged (CLAUDE.md §30); the page's
// source choice picks one exchange for every dataset. A source not listed here
// is refused, not guessed: its rows would otherwise vanish or mix.
export type Exchange = "twse" | "tpex";

const EXCHANGE: Record<string, Exchange> = {
  twse_mi_index: "twse", tpex_otc_quotes: "tpex",
  twse_t86: "twse", tpex_insti_daily_trade: "tpex",
  twse_mi_qfiis: "twse", mops_t13sa150_otc: "tpex",
  twse_mi_margn: "twse", tpex_margin_balance: "tpex",
  twse_twt93u: "twse", tpex_margin_sbl: "tpex",
};

export function exchangeOf(source: string): Exchange {
  const exchange = EXCHANGE[source];
  if (!exchange) throw new Error(`no exchange known for source ${source}`);
  return exchange;
}

export function rowsOfExchange<Row extends { source: string }>(rows: Row[], exchange: Exchange): Row[] {
  return rows.filter((r) => exchangeOf(r.source) === exchange);
}

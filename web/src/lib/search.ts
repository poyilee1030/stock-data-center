import type { Stock } from "../api/types";

/** Exact code first, then code prefixes, then names containing the query; listed before delisted. */
export function searchStocks(stocks: Stock[], query: string, limit = 12): Stock[] {
  const q = query.trim();
  if (!q) return [];
  const rank = (s: Stock): number => {
    if (s.stock_id === q) return 0;
    if (s.stock_id.startsWith(q)) return 1;
    if (s.name.includes(q)) return 2;
    return -1;
  };
  return stocks
    .map((s) => ({ s, r: rank(s) }))
    .filter(({ r }) => r >= 0)
    .sort((a, b) => a.r - b.r || Number(a.s.market === null) - Number(b.s.market === null)
                    || a.s.stock_id.localeCompare(b.s.stock_id))
    .slice(0, limit)
    .map(({ s }) => s);
}

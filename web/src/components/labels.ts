import type { Stock } from "../api/types";

export function marketLabel(market: Stock["market"] | "sii" | "otc"): string {
  if (market === "sii") return "上市";
  if (market === "otc") return "上櫃";
  return "已下市";
}

// Source codes are the API's; the label says which exchange published them.
export const SOURCE_LABELS: Record<string, string> = {
  twse_mi_index: "證交所",
  tpex_otc_quotes: "櫃買中心",
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

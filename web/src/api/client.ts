// The only way the dashboard reads data: the public API, same origin, with the
// key the viewer typed (ADR-0029 §4). No PIT parameter is ever sent, so every
// answer is "latest" and says which instant that resolved to.
import type { AdjustedAnswer, IndicatorRow, PriceRow, RowsAnswer, StocksAnswer, TradingDaysAnswer } from "./types";

const KEY = "stockdc.apiKey";

export class KeyRequired extends Error {
  constructor() {
    super("an API key is required");
  }
}

export class ApiError extends Error {}

export function storedKey(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function storeKey(key: string | null): void {
  try {
    if (key === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, key);
  } catch {
    // A browser that refuses storage asks again next visit.
  }
}

export function query(params: Record<string, string | string[] | undefined>): string {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value === undefined) continue;
    for (const one of Array.isArray(value) ? value : [value]) search.append(name, one);
  }
  return search.toString();
}

const TAIPEI_DATE = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
});

export function todayInTaipei(now = new Date()): string {
  return TAIPEI_DATE.format(now);
}

export async function get<T>(path: string, params: Record<string, string | string[] | undefined> = {},
                             signal?: AbortSignal): Promise<T> {
  const key = storedKey();
  if (!key) throw new KeyRequired();
  const qs = query(params);
  const response = await fetch(`/v1/${path}${qs ? `?${qs}` : ""}`, { headers: { "X-API-Key": key }, signal });
  if (response.status === 401) {
    storeKey(null);
    throw new KeyRequired();
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(body?.detail ?? `HTTP ${response.status}`);
  return body as T;
}

// Data starts on 2020-01-02 (v1 scope); asking from 2000 keeps a later
// backfill of older years visible without a code change.
const FIRST = "2000-01-01";

export const api = {
  stocks: (signal?: AbortSignal) => get<StocksAnswer>("stocks", {}, signal),
  tradingDays: (signal?: AbortSignal) =>
    get<TradingDaysAnswer>("trading-days", { start: FIRST, end: todayInTaipei() }, signal),
  prices: (stockId: string, signal?: AbortSignal) =>
    get<RowsAnswer<PriceRow>>("datasets/daily-prices",
                              { start: FIRST, end: todayInTaipei(), stock_id: stockId }, signal),
  indicators: (stockId: string, signal?: AbortSignal) =>
    get<RowsAnswer<IndicatorRow>>("datasets/technical-indicators",
                                  { start: FIRST, end: todayInTaipei(), stock_id: stockId }, signal),
  adjusted: (stockId: string, source: string, signal?: AbortSignal) =>
    get<AdjustedAnswer>("datasets/adjusted-prices-pit",
                        { start: FIRST, end: todayInTaipei(), stock_id: stockId, source }, signal),
};

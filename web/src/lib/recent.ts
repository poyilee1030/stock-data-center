const KEY = "stockdc.recent";

export function recentStocks(): string[] {
  try {
    const list = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(list) ? list.filter((s) => typeof s === "string").slice(0, 8) : [];
  } catch {
    return [];
  }
}

export function rememberStock(stockId: string): void {
  try {
    localStorage.setItem(KEY, JSON.stringify([stockId, ...recentStocks().filter((s) => s !== stockId)].slice(0, 8)));
  } catch {
    // A convenience only.
  }
}

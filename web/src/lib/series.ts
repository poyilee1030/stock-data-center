// Placing API rows on the trading-day axis. Nothing here computes a value:
// every number a chart draws is a field of an API row, or missing.

export interface Axis {
  dates: string[];
  // Dates a row has that the trading calendar does not: kept on the axis, and
  // shown as a warning, never dropped.
  offCalendar: string[];
}

/** The trading days from the first row to the last, plus any date only a row has. */
export function tradingAxis(calendar: string[], rows: { trade_date: string }[]): Axis {
  if (rows.length === 0) return { dates: [], offCalendar: [] };
  const own = rows.map((r) => r.trade_date).sort();
  const first = own[0], last = own[own.length - 1];
  const known = new Set(calendar);
  const offCalendar = [...new Set(own.filter((d) => !known.has(d)))];
  const dates = [...new Set([...calendar.filter((d) => d >= first && d <= last), ...offCalendar])].sort();
  return { dates, offCalendar };
}

/** Each row at its date's slot; a date without a row is undefined. */
export function align<Row extends { trade_date: string }>(dates: string[], rows: Row[]): (Row | undefined)[] {
  const at = new Map<string, Row>();
  for (const row of rows) {
    if (at.has(row.trade_date)) {
      throw new Error(`two rows for ${row.trade_date}: rows of different sources are never merged`);
    }
    at.set(row.trade_date, row);
  }
  return dates.map((d) => at.get(d));
}

/** One field along the axis: a missing row and a null value are both null. */
export function column<Row, K extends keyof Row>(aligned: (Row | undefined)[], field: K): (Row[K] | null)[] {
  return aligned.map((row) => (row == null || row[field] === undefined ? null : row[field]));
}

export function bySource<Row extends { source: string }>(rows: Row[]): Map<string, Row[]> {
  const groups = new Map<string, Row[]>();
  for (const row of rows) {
    const group = groups.get(row.source);
    if (group) group.push(row);
    else groups.set(row.source, [row]);
  }
  return groups;
}

/** The source of the latest row: what a stock that changed market trades on now. */
export function defaultSource(rows: { source: string; trade_date: string }[]): string | null {
  let latest: { source: string; trade_date: string } | null = null;
  for (const row of rows) if (latest === null || row.trade_date > latest.trade_date) latest = row;
  return latest?.source ?? null;
}

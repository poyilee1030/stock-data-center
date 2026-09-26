// Display formatting only (ADR-0029 §6): grouping, 萬/億 abbreviations,
// two-place prices and Taipei time. A missing value is a dash, never zero.

export const MISSING = "—";

const TAIPEI = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function taipei(instant: string): string {
  const parts = Object.fromEntries(TAIPEI.formatToParts(new Date(instant)).map((p) => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

function fixed(value: number, digits: number): string {
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function grouped(value: number | null): string {
  return value === null ? MISSING : value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function priceText(value: number | null): string {
  return value === null ? MISSING : fixed(value, 2);
}

export function compact(value: number | null): string {
  if (value === null) return MISSING;
  const size = Math.abs(value);
  if (size >= 1e8) return `${fixed(value / 1e8, 2)} 億`;
  if (size >= 1e4) return `${fixed(value / 1e4, 1)} 萬`;
  return grouped(value);
}

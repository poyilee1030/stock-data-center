// Which way a day moved, read from what the source published. Both feeds sign
// price_change; TWSE also has a +/-/X column, TPEx only its 不比價 marker
// (stored as X). Under 不比價 the change is against a reference price, not the
// previous close, so it is not coloured as a rise or a fall.
import { MISSING, priceText } from "./format";

export type Movement = "up" | "down" | "flat" | "unmatched";

type Changed = { price_change: number | null; price_direction: string | null };

export function movement(row: Changed | undefined): Movement {
  if (row?.price_direction === "X") return "unmatched";
  const change = row?.price_change ?? null;
  if (change === null || change === 0) return "flat";
  return change > 0 ? "up" : "down";
}

export function changeText(row: Changed | undefined): string {
  if (!row || row.price_change === null) return MISSING;
  const text = priceText(Math.abs(row.price_change));
  if (row.price_direction === "X") return `${priceText(row.price_change)}（不比價）`;
  if (row.price_change > 0) return `+${text}`;
  if (row.price_change < 0) return `−${text}`;
  return text;
}

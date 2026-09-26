import type { Pit } from "../api/types";
import { MISSING, taipei } from "../lib/format";

/** The instant an answer resolved "latest" to, as every panel states it. */
export function PitNote({ pit, what }: { pit: Pit | undefined; what: string }) {
  if (!pit) return null;
  const instant = pit.information_as_of ?? pit.system_as_of;
  const latest = (pit.defaulted?.length ?? 0) > 0;
  return (
    <span className="pit" title={`information_as_of = knowledge_as_of = ${instant}`}>
      {what} · 資料時點 {instant ? taipei(instant) : MISSING}{latest ? "（latest）" : ""}
    </span>
  );
}

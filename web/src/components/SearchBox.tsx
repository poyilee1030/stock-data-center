import { useEffect, useMemo, useRef, useState } from "react";
import type { Stock } from "../api/types";
import { stockHref } from "../lib/router";
import { searchStocks } from "../lib/search";
import { Search } from "./Icons";
import { marketLabel } from "./labels";

export function SearchBox({ stocks, large = false, autoFocus = false }: {
  stocks: Stock[] | null;
  large?: boolean;
  autoFocus?: boolean;
}) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const hits = useMemo(() => (stocks ? searchStocks(stocks, text) : []), [stocks, text]);

  useEffect(() => {
    if (large) return;
    // "/" jumps to the search box from anywhere but another field.
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (e.key === "/" && !/INPUT|TEXTAREA/.test(target.tagName)) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [large]);

  const go = (stock: Stock) => {
    location.hash = stockHref(stock.stock_id);
    setText("");
    setOpen(false);
    input.current?.blur();
  };

  return (
    <div className={`search ${large ? "search-large" : ""}`}>
      <Search />
      <input ref={input} type="search" value={text} autoFocus={autoFocus} data-testid={large ? "home-search" : "search"}
             placeholder={stocks ? "股票代號或名稱" : "載入股票清單…"} disabled={!stocks}
             aria-label="搜尋股票" aria-expanded={open && hits.length > 0} role="combobox" aria-controls="search-hits"
             onChange={(e) => { setText(e.target.value); setOpen(true); setActive(0); }}
             onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 120)}
             onKeyDown={(e) => {
               if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
               else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
               else if (e.key === "Enter" && hits[active]) go(hits[active]);
               else if (e.key === "Escape") setOpen(false);
             }} />
      {!large && <kbd className="hint">/</kbd>}
      {open && hits.length > 0 && (
        <ul className="hits" id="search-hits" role="listbox">
          {hits.map((s, i) => (
            <li key={s.stock_id} role="option" aria-selected={i === active} className={i === active ? "on" : ""}
                onMouseDown={(e) => { e.preventDefault(); go(s); }} onMouseEnter={() => setActive(i)}>
              <span className="code">{s.stock_id}</span>
              <span className="name">{s.name}</span>
              <span className="industry">{s.industry ?? ""}</span>
              <span className={`badge ${s.market ?? "gone"}`}>{marketLabel(s.market)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

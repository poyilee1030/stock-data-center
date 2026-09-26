import { useEffect, useState } from "react";

// Hash routes, so the API serves one page for every route (ADR-0029 §1).
export type StockTab = "price" | "chips";
export type Route = { page: "home" } | { page: "market" } | { page: "stock"; stockId: string; tab: StockTab };

export function parse(hash: string): Route {
  if (hash === "#/market") return { page: "market" };
  const stock = /^#\/stock\/([0-9A-Za-z]+)(?:\/(chips))?$/.exec(hash);
  return stock ? { page: "stock", stockId: stock[1], tab: stock[2] === "chips" ? "chips" : "price" } : { page: "home" };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parse(location.hash));
    addEventListener("hashchange", onChange);
    return () => removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function stockHref(stockId: string, tab: StockTab = "price"): string {
  return tab === "price" ? `#/stock/${stockId}` : `#/stock/${stockId}/${tab}`;
}

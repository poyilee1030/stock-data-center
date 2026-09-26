import { useEffect, useState } from "react";

// Hash routes, so the API serves one page for every route (ADR-0029 §1).
export type Route = { page: "home" } | { page: "stock"; stockId: string };

export function parse(hash: string): Route {
  const stock = /^#\/stock\/([0-9A-Za-z]+)$/.exec(hash);
  return stock ? { page: "stock", stockId: stock[1] } : { page: "home" };
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

export function stockHref(stockId: string): string {
  return `#/stock/${stockId}`;
}

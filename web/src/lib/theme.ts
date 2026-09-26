import { useCallback, useEffect, useState } from "react";
import type { Palette } from "./chart";

export type Theme = "dark" | "light";
const KEY = "stockdc.theme";

function initial(): Theme {
  // Dark unless the viewer chose light (owner, 2026-09-26).
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initial);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      // Kept for this visit only.
    }
  }, [theme]);
  // <html> changes before React re-renders: the chart reads its palette from
  // the CSS custom properties while rendering (code review of #69).
  const toggle = useCallback(() => {
    const next: Theme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    setTheme(next);
  }, []);
  return [theme, toggle];
}

/** The chart colours of the current theme, from the CSS custom properties. */
export function readPalette(): Palette {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    up: v("--up"), down: v("--down"), flat: v("--flat"), text: v("--text"), muted: v("--muted"),
    grid: v("--grid"), surface: v("--raised"), accent: v("--accent"),
    ma: { 5: v("--s1"), 10: v("--s2"), 20: v("--s3"), 60: v("--s4"), 120: v("--s5"), 240: v("--s6") },
    bollinger: v("--muted"), k: v("--s1"), d: v("--s2"), rsi6: v("--s1"), rsi12: v("--s2"),
    dif: v("--s1"), dea: v("--s2"),
  };
}

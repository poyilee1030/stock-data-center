// Inline icons: the page loads nothing from outside (ADR-0029 §4).
const base = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
               strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
               "aria-hidden": true };

export const Sun = () => (
  <svg {...base}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
);
export const Moon = () => (
  <svg {...base}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>
);
export const Key = () => (
  <svg {...base}><circle cx="7.5" cy="15.5" r="4.5" /><path d="M10.7 12.3 21 2M16 7l3 3M14 9l2 2" /></svg>
);
export const Search = () => (
  <svg {...base}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
);
export const Logo = () => (
  <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
    <rect width="32" height="32" rx="8" fill="var(--raised)" stroke="var(--border)" />
    <path d="M7 22l6-7 5 4 7-9" stroke="var(--accent)" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

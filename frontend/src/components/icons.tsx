// Thin-stroke icons used by the page.
type P = { className?: string };
const base = (className = "h-5 w-5") => ({
  className,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  viewBox: "0 0 24 24",
  "aria-hidden": true,
});

export const Check = ({ className }: P) => (
  <svg {...base(className)}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </svg>
);
export const XIcon = ({ className }: P) => (
  <svg {...base(className)}>
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
);
export const Spinner = ({ className = "h-5 w-5" }: P) => (
  <svg className={`${className} animate-spin`} viewBox="0 0 24 24" aria-hidden>
    <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity=".2" strokeWidth="3" fill="none" />
    <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
  </svg>
);

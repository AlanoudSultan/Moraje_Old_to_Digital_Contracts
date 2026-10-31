import { createContext, useCallback, useContext, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { toAr } from "../api";
import { Check, Spinner, XIcon } from "./icons";

/** The four "+" registration marks every blueprint frame wears. */
export const Corners = () => (
  <>
    <i className="corner tl" />
    <i className="corner tr" />
    <i className="corner bl" />
    <i className="corner br" />
  </>
);

/** A hairline, square-cornered frame with registration marks. */
export function Frame({ as: Tag = "div", className = "", style, children, ...rest }: {
  as?: "div" | "figure" | "article" | "form" | "section";
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
} & Record<string, unknown>) {
  const T = Tag as "div";
  return (
    <T className={`blueprint ${className}`} style={style} {...rest}>
      <Corners />
      {children}
    </T>
  );
}

/** Segmented control. */
export function Seg<T extends string>({ value, onChange, options, small }: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode }[];
  small?: boolean;
}) {
  return (
    <div className="seg" role="group">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className="seg-opt"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          style={small ? { padding: "5px 10px", fontSize: 12 } : undefined}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** A row of label/number cells inside one frame (intake stats, review summary). */
export function StatGrid({ items, min = 160, size = 28 }: { items: { label: string; value: ReactNode }[]; min?: number; size?: number }) {
  return (
    <Frame style={{ display: "grid", gridTemplateColumns: `repeat(auto-fit,minmax(${min}px,1fr))` }}>
      {items.map((k) => (
        <div key={k.label} style={{ padding: "16px 18px", borderInlineStart: "1px solid var(--color-divider)", marginInlineStart: -1, display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 12, color: "var(--color-neutral-700)" }}>{k.label}</span>
          <span style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: size }}>{k.value}</span>
        </div>
      ))}
    </Frame>
  );
}

/** "active": not finished, but its content is ready to work on (e.g. fields awaiting review). */
export type StepState = "waiting" | "running" | "active" | "done" | "error" | "skipped";

function Dot({ state, n }: { state: StepState; n: number }) {
  const done = state === "done";
  const err = state === "error";
  return (
    <span
      style={{
        width: 34, height: 34, borderRadius: "50%", display: "grid", placeItems: "center", fontWeight: 600, fontSize: 13, flex: "none",
        background: done ? "var(--color-accent)" : err ? "var(--color-danger)" : "transparent",
        color: done || err ? "var(--color-bg)" : state === "skipped" ? "var(--color-neutral-500)" : "var(--color-text)",
        border: `1px solid ${done ? "var(--color-accent)" : err ? "var(--color-danger)" : "var(--color-divider)"}`,
      }}
    >
      {done ? <Check className="h-4 w-4" /> : err ? <XIcon className="h-4 w-4" /> : state === "running" ? <Spinner className="h-4 w-4 text-[var(--color-accent)]" /> : toAr(n)}
    </span>
  );
}

/** One stage of the request timeline: a numbered dot on a rail, a hide/show toggle, and either the
 *  full body (title, description, content) or a one-line summary when hidden or not ready yet. */
export function TimelineStep({
  n, title, sub, desc, aside, state, last, children, id, open: openProp, onToggle,
}: {
  n: number;
  title: string;
  sub?: ReactNode;
  desc?: ReactNode;
  aside?: ReactNode;
  state: StepState;
  last?: boolean;
  children?: ReactNode;
  id?: string;
  open?: boolean;
  onToggle?: (open: boolean) => void;
}) {
  const [ownOpen, setOwnOpen] = useState(true);
  const open = openProp ?? ownOpen;
  const setOpen = (v: boolean) => (onToggle ? onToggle(v) : setOwnOpen(v));
  const hasBody = !!children && state !== "waiting" && state !== "skipped";
  return (
    <div id={id} style={{ display: "grid", gridTemplateColumns: "36px minmax(0,1fr)", gap: 18, scrollMarginTop: 16 }}>
      <div className="no-print" style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
        <Dot state={state} n={n} />
        {!last && <span style={{ flex: 1, width: 1, background: "var(--color-divider)", marginTop: 6 }} />}
      </div>
      <div style={{ minWidth: 0, paddingBottom: 12, display: "flex", flexDirection: "column", gap: 6 }}>
        {hasBody && (
          <button className="btn btn-ghost no-print" onClick={() => setOpen(!open)} aria-expanded={open} style={{ alignSelf: "flex-end", fontSize: 12, padding: "2px 8px" }}>
            {open ? "إخفاء" : "إظهار"}
          </button>
        )}
        {hasBody && open ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
              <div>
                <h2 style={{ fontSize: 30, margin: "0 0 4px" }}>{title}</h2>
                {desc && <p style={{ margin: 0, color: state === "error" ? "var(--color-danger)" : "var(--color-neutral-700)" }}>{desc}</p>}
              </div>
              {aside}
            </div>
            {children}
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, paddingTop: hasBody ? 4 : 6, flexWrap: "wrap" }}>
            <h2 style={{ fontSize: 24, margin: 0, color: state === "skipped" || state === "waiting" ? "var(--color-neutral-600)" : undefined }}>{title}</h2>
            {sub && <span aria-live="polite" style={{ fontSize: 13, color: state === "error" ? "var(--color-danger)" : "var(--color-neutral-700)" }}>{sub}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

/** A modal on the backdrop; Esc and a click outside close it. */
export function Dialog({ label, onClose, children, style }: { label: string; onClose: () => void; children: ReactNode; style?: CSSProperties }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.querySelector<HTMLElement>("button")?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="dialog-backdrop no-print" onClick={onClose}>
      <div ref={box} className="dialog" role="dialog" aria-modal="true" aria-label={label} onClick={(e) => e.stopPropagation()} style={style}>
        {children}
      </div>
    </div>
  );
}

// ---------- toast ----------

const ToastCtx = createContext<(t: string) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState("");
  const timer = useRef<number>();
  const flash = useCallback((t: string) => {
    setToast(t);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(""), 2200);
  }, []);
  return (
    <ToastCtx.Provider value={flash}>
      {children}
      {toast && (
        <div role="status" className="no-print" style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 60, background: "var(--color-accent-900)", color: "var(--color-bg)", padding: "10px 18px", fontSize: 14, boxShadow: "var(--shadow-lg)" }}>
          {toast}
        </div>
      )}
    </ToastCtx.Provider>
  );
}

/** Download link drawn as a button; greyed out when the file isn't there yet. */
export function DownloadBtn({ href, ok, label, done, primary }: { href: string; ok: boolean; label: string; done: string; primary?: boolean }) {
  const flash = useToast();
  return (
    <a
      className={`btn ${primary ? "btn-primary" : "btn-secondary"}`}
      href={ok ? href : undefined}
      aria-disabled={!ok}
      onClick={() => ok && flash(done)}
    >
      {label}
    </a>
  );
}

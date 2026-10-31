import type { OcrPage } from "../api";
import { pct, toAr } from "../api";

export const LOW = 0.85;

/** Box style by reading confidence, as in the design's legend. */
export const lineBorder = (conf: number, on: boolean) =>
  on ? "2px solid var(--color-accent)" : conf < 0.5 ? "1.5px dashed var(--color-text)" : conf < LOW ? "1.5px solid var(--color-accent-2-600)" : "1px solid var(--color-accent-400)";
const numBg = (conf: number, on: boolean) => (on ? "var(--color-accent)" : conf < LOW ? "var(--color-accent-2-700)" : "var(--color-accent-500)");
const stroke = (conf: number, on: boolean) =>
  on ? { color: "var(--color-accent)", width: 2, dash: undefined }
    : conf < 0.5 ? { color: "var(--color-text)", width: 1.5, dash: "4 3" }
    : conf < LOW ? { color: "var(--color-accent-2-600)", width: 1.5, dash: undefined }
    : { color: "var(--color-accent-400)", width: 1, dash: undefined };

/** Height of a line's right end (where an Arabic line starts), for its number. */
const rightEndY = (poly: number[][]) => {
  const right = Math.max(...poly.map((p) => p[0]));
  const near = poly.filter((p) => p[0] >= right - 0.03);
  return (near.length ? near : poly).reduce((a, p) => a + p[1], 0) / (near.length || poly.length);
};

/** Page image with the segmentation drawn on top: numbered line boxes (by confidence) and stamps.
 *  Coordinates are normalized 0..1, so the same boxes fit the original and the enhanced image. */
export function PageViewer({
  contractId,
  page,
  selected,
  onSelect,
  variant = "original",
}: {
  contractId: string;
  page: OcrPage;
  selected?: string | null;
  onSelect?: (lineId: string) => void;
  variant?: "original" | "enhanced";
}) {
  // When OCR lines exist, draw those (they carry IDs and confidence); before OCR, the raw boxes.
  // Lines with a polygon (Kraken) are drawn as tilted outlines; the rest as boxes.
  const items = page.lines.length
    ? page.lines.map((l, i) => ({ id: l.id, bbox: l.bbox, poly: l.polygon, n: i + 1, conf: l.confidence }))
    : page.boxes.map((b, i) => ({ id: `box${i}`, bbox: b, poly: page.polygons?.[i], n: i + 1, conf: 1 }));
  const shaped = items.filter((it) => it.poly && it.poly.length > 2);

  return (
    <div style={{ position: "relative", userSelect: "none" }}>
      <img
        src={`/api/contracts/${contractId}/pages/${page.page}?variant=${variant}`}
        alt={`صورة الصك ${variant === "enhanced" ? "بعد التحسين" : "الأصلية"}، صفحة ${toAr(page.page)}`}
        style={{ width: "100%", display: "block" }}
      />
      {shaped.length > 0 && (
        <svg viewBox="0 0 1 1" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", overflow: "visible" }}>
          {shaped.map((it) => {
            const on = selected === it.id;
            const st = stroke(it.conf, on);
            return (
              <polygon
                key={it.id}
                className="linepoly"
                points={it.poly!.map((p) => p.join(",")).join(" ")}
                onClick={() => onSelect?.(it.id)}
                fill={on ? "color-mix(in srgb, var(--color-accent) 28%, transparent)" : "transparent"}
                stroke={st.color}
                strokeWidth={st.width}
                strokeDasharray={st.dash}
                vectorEffect="non-scaling-stroke"
                style={{ cursor: onSelect ? "pointer" : "default", pointerEvents: "all" }}
              >
                <title>{page.lines.length ? `سطر ${toAr(it.n)} · ${pct(it.conf)}` : `سطر ${toAr(it.n)}`}</title>
              </polygon>
            );
          })}
        </svg>
      )}
      {shaped.map((it) => {
        const on = selected === it.id;
        const right = Math.max(...it.poly!.map((p) => p[0]));
        return (
          <span key={`n${it.id}`} style={{ position: "absolute", pointerEvents: "none", left: `${right * 100}%`, top: `${rightEndY(it.poly!) * 100}%`, transform: "translate(-100%,-50%)", fontSize: 8, lineHeight: 1, padding: 2, minWidth: 14, textAlign: "center", background: numBg(it.conf, on), color: "var(--color-bg)" }}>
            {toAr(it.n)}
          </span>
        );
      })}
      {items.filter((it) => !(it.poly && it.poly.length > 2)).map((it) => {
        const [x0, y0, x1, y1] = it.bbox;
        const on = selected === it.id;
        return (
          <button
            key={it.id}
            type="button"
            className="linebox"
            onClick={() => onSelect?.(it.id)}
            title={page.lines.length ? `سطر ${toAr(it.n)} · ${pct(it.conf)}` : `سطر ${toAr(it.n)}`}
            style={{
              position: "absolute",
              left: `${x0 * 100}%`,
              top: `${y0 * 100}%`,
              width: `${(x1 - x0) * 100}%`,
              height: `${(y1 - y0) * 100}%`,
              border: lineBorder(it.conf, on),
              background: on ? "color-mix(in srgb, var(--color-accent) 28%, transparent)" : "transparent",
              cursor: onSelect ? "pointer" : "default",
              padding: 0,
            }}
          >
            <span style={{ position: "absolute", right: 0, top: "50%", transform: "translateY(-50%)", fontSize: 8, lineHeight: 1, padding: 2, minWidth: 14, textAlign: "center", background: numBg(it.conf, on), color: "var(--color-bg)" }}>
              {toAr(it.n)}
            </span>
          </button>
        );
      })}
      {page.stamps.map((s, i) => {
        const [x0, y0, x1, y1] = s.bbox;
        return (
          <div
            key={`s${i}`}
            style={{
              position: "absolute", pointerEvents: "none", borderRadius: "50%", border: "2px dashed var(--color-accent-2-600)",
              left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`,
            }}
          >
            <span className="tag" style={{ position: "absolute", top: -18, right: 0, padding: "1px 6px", fontSize: 10, background: "var(--color-accent-2-700)", color: "#fff" }}>
              ختم {toAr(i + 1)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** The design's legend under a page image. */
export function LineLegend() {
  const sw = (border: string) => <i style={{ width: 12, height: 7, border, display: "inline-block" }} />;
  return (
    <figcaption style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
      <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>{sw("1px solid var(--color-accent-400)")}٨٥٪ فأكثر</span>
      <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>{sw("1.5px solid var(--color-accent-2-600)")}٥٠–٨٥٪</span>
      <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>{sw("1.5px dashed var(--color-text)")}أقل من ٥٠٪</span>
    </figcaption>
  );
}

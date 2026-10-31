import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { api, countAr, pct, toAr, type ContractDetail, type DeedData, type DeedField, type OcrPage } from "../api";
import { LineLegend, LOW, PageViewer } from "./PageViewer";
import { Dialog, DownloadBtn, Frame, Seg, StatGrid } from "./ui";
import { Spinner } from "./icons";

// ---------- data ----------

/** The deed template fields with the reviewer's edits applied, shared by the review, copy and decision steps. */
export function useDeed(id: string | null, ready: boolean) {
  const [data, setData] = useState<DeedData | null>(null);
  const [version, setVersion] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    if (id) api.deed(id).then(setData).catch((e) => setErr((e as Error).message));
  }, [id]);
  useEffect(() => {
    setData(null);
    setErr(null);
    if (ready) load();
  }, [ready, load]);
  const save = async (key: string, status: DeedField["status"], value?: string) => {
    await api.deedEdit(id!, { key, status, value });
    await load();
    setVersion((v) => v + 1);
  };
  return { data, err, save, version };
}

/** Fields whose reading accuracy is under the threshold (they stay listed after a decision so it can be undone). */
export const isLowConf = (f: DeedField) => f.low || (f.confidence !== null && f.confidence < LOW);
export const pendingOf = (data: DeedData | null) => (data?.fields ?? []).filter((f) => f.low && f.status === "auto");
export const pendingLabel = (n: number) => countAr(n, "field");

export function deedSummary(data: DeedData | null) {
  const fields = data?.fields ?? [];
  return [
    { label: "الحقول", value: toAr(fields.length) },
    { label: "روجعت", value: toAr(fields.filter((f) => f.status !== "auto").length) },
    { label: "عُدّلت", value: toAr(fields.filter((f) => f.status === "edited").length) },
    { label: "متبقية", value: toAr(pendingOf(data).length) },
  ];
}

type LineRef = { id: string; page: number; n: number; text: string; conf: number };

function lineIndex(pages: OcrPage[]) {
  const m = new Map<string, LineRef>();
  for (const p of pages) p.lines.forEach((l, i) => m.set(l.id, { id: l.id, page: p.page, n: i + 1, text: l.text, conf: l.confidence }));
  return m;
}

// ---------- step 3: reading and field review ----------

const STATUS: Record<DeedField["status"], [string, string]> = {
  auto: ["تلقائي", "tag-neutral"],
  confirmed: ["مؤكَّد", "tag-accent"],
  edited: ["عُدّل يدوياً", "tag-accent"],
  skipped: ["متخطّى", "tag-outline"],
};

export type Filter = "needs" | "all" | "lines";

function FieldRow({ f, src, multiPage, onSrc, save }: {
  f: DeedField;
  src?: LineRef;
  multiPage: boolean;
  onSrc: (l: LineRef) => void;
  save: (key: string, status: DeedField["status"], value?: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(!f.value && f.status === "auto");
  const [draft, setDraft] = useState(f.value);
  const [busy, setBusy] = useState(false);
  const act = async (status: DeedField["status"], value?: string) => {
    setBusy(true);
    try {
      await save(f.key, status, value);
      setEditing(false);
    } finally {
      setBusy(false);
    }
  };
  const [statusLabel, tagClass] = f.low && f.status === "auto" ? ["يحتاج مراجعة", "tag-outline"] : STATUS[f.status];
  const conf = f.confidence;
  const skipped = f.status === "skipped";
  return (
    <div style={{ padding: "14px 0", borderBottom: "1px solid var(--color-divider)", display: "grid", gridTemplateColumns: "minmax(120px,180px) minmax(0,1fr) auto", gap: 16, alignItems: "start" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>{f.label}</span>
        {conf !== null ? (
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{ width: 48, height: 3, background: "var(--color-neutral-200)", position: "relative" }}>
              <div style={{ position: "absolute", insetBlock: 0, right: 0, width: `${conf * 100}%`, background: conf < LOW ? "var(--color-accent-2-600)" : "var(--color-accent-400)" }} />
            </div>
            <span style={{ fontSize: 11, color: "var(--color-neutral-700)" }}>دقة {pct(conf)}</span>
          </div>
        ) : (
          <span style={{ fontSize: 11, color: "var(--color-neutral-700)" }}>أضافه المراجع</span>
        )}
        {src && (
          <button className="btn btn-ghost" onClick={() => onSrc(src)} style={{ fontSize: 12, padding: "1px 4px", alignSelf: "flex-start", fontWeight: 400 }}>
            المصدر: {multiPage ? `ص${toAr(src.page)} ` : ""}سطر {toAr(src.n)}
          </button>
        )}
      </div>
      <div style={{ minWidth: 0 }}>
        {editing ? (
          <input className="input" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="اكتب القيمة كما في الأصل" style={{ fontSize: 16 }} autoFocus
            onKeyDown={(e) => e.key === "Enter" && draft.trim() && act(draft.trim() === f.value.trim() ? "confirmed" : "edited", draft.trim())} />
        ) : (
          <div style={{ fontSize: 17, lineHeight: 1.5, padding: "4px 0", textDecoration: skipped ? "line-through" : "none", color: skipped ? "var(--color-neutral-600)" : "var(--color-text)" }}>{f.value}</div>
        )}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
          <span className={`tag ${tagClass}`}>{statusLabel}</span>
          {f.status === "auto" && !f.quoted && f.value && <span className="tag tag-outline">ليس اقتباساً حرفياً من السطر</span>}
        </div>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
        {busy && <Spinner className="h-4 w-4 self-center text-[var(--color-accent)]" />}
        {editing ? (
          <>
            <button className="btn btn-primary" disabled={busy || !draft.trim()} onClick={() => act(draft.trim() === f.value.trim() ? "confirmed" : "edited", draft.trim())}>حفظ</button>
            {f.value && <button className="btn btn-secondary" disabled={busy} onClick={() => { setEditing(false); setDraft(f.value); }}>إلغاء</button>}
          </>
        ) : f.status === "auto" ? (
          <>
            <button className="btn btn-secondary" disabled={busy} onClick={() => act("confirmed")} title="تأكيد">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>تأكيد
            </button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => { setDraft(f.value); setEditing(true); }}>تعديل</button>
            <button className="btn btn-ghost" disabled={busy} onClick={() => act("skipped")}>تخطٍّ</button>
          </>
        ) : (
          <button className="btn btn-ghost" disabled={busy} onClick={() => act("auto")}>تراجع</button>
        )}
      </div>
    </div>
  );
}

function GroupTitle({ children }: { children: ReactNode }) {
  return (
    <div style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 18, paddingBottom: 8, borderBottom: "1px solid var(--color-text)", color: "var(--color-accent-800)" }}>
      {children}
    </div>
  );
}

/** Filter control shown in the step header. */
export function ReviewFilter({ value, onChange, pending, lines, hasFields }: { value: Filter; onChange: (f: Filter) => void; pending: number; lines: number; hasFields: boolean }) {
  const opts: { value: Filter; label: ReactNode }[] = [
    ...(hasFields
      ? [
          { value: "needs" as const, label: `تحتاج مراجعة · ${pendingLabel(pending)}` },
          { value: "all" as const, label: "كل الحقول" },
        ]
      : []),
    { value: "lines", label: `الأسطر المقروءة · ${toAr(lines)}` },
  ];
  return <Seg value={value} onChange={onChange} options={opts} />;
}

export function DeedReview({ id, pages, deed, filter, enhanced }: {
  id: string;
  pages: OcrPage[];
  deed: ReturnType<typeof useDeed> | null;
  filter: Filter;
  enhanced: boolean;
}) {
  const [page, setPage] = useState(pages[0]?.page ?? 1);
  const [img, setImg] = useState<"original" | "enhanced">("original");
  const [sel, setSel] = useState<string | null>(null);
  const [adding, setAdding] = useState("");
  const [extra, setExtra] = useState<string[]>([]);
  const lines = useMemo(() => lineIndex(pages), [pages]);
  const cur = pages.find((p) => p.page === page) ?? pages[0];
  const multiPage = pages.length > 1;
  const data = deed?.data ?? null;

  const fields = useMemo(() => {
    if (!data) return [];
    const have = new Set(data.fields.map((f) => f.key));
    const added: DeedField[] = extra.filter((k) => !have.has(k)).map((k) => {
      const meta = data.keys.find((x) => x.key === k)!;
      return { key: k, section: meta.section, label: meta.label, value: "", confidence: null, line_ids: [], quoted: false, status: "auto", low: false };
    });
    return [...data.fields, ...added];
  }, [data, extra]);
  const lowList = fields.filter(isLowConf);
  const pending = pendingOf(data);
  const progress = lowList.length ? ((lowList.length - pending.length) / lowList.length) * 100 : 100;

  const selectLine = (l: LineRef) => {
    setSel(l.id);
    setPage(l.page);
  };
  const selL = sel ? lines.get(sel) : undefined;
  const sectionLabel = Object.fromEntries((data?.sections ?? []).map((s) => [s.id, s.label]));
  const row = (f: DeedField) => (
    <FieldRow key={f.key} f={f} src={f.line_ids.map((x) => lines.get(x)).find(Boolean)} multiPage={multiPage} onSrc={selectLine} save={deed!.save} />
  );
  const missingKeys = (data?.keys ?? []).filter((k) => !fields.some((f) => f.key === k.key));

  return (
    <>
      {data?.extracted && (
        <div style={{ height: 4, background: "var(--color-neutral-200)", position: "relative" }}>
          <div style={{ position: "absolute", insetBlock: 0, right: 0, width: `${progress}%`, background: "var(--color-accent)", transition: "width .3s" }} />
        </div>
      )}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 28, alignItems: "flex-start" }}>
        {cur && (
          <Frame as="figure" style={{ flex: "0 1 320px", minWidth: 260, padding: 10, margin: 0, position: "sticky", top: 20, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              {enhanced ? (
                <Seg small value={img} onChange={setImg} options={[{ value: "original", label: "الأصل" }, { value: "enhanced", label: "بعد التحسين" }]} />
              ) : (
                <span />
              )}
              <span style={{ fontSize: 11, color: "var(--color-neutral-700)" }}>{img === "original" ? "المرجع" : "ما يقرؤه النموذج"}</span>
            </div>
            {multiPage && (
              <Seg small value={String(cur.page)} onChange={(v) => setPage(Number(v))} options={pages.map((p) => ({ value: String(p.page), label: `صفحة ${toAr(p.page)}` }))} />
            )}
            <PageViewer contractId={id} page={cur} selected={sel} onSelect={setSel} variant={enhanced ? img : "original"} />
            {selL && (
              <div style={{ borderTop: "1px solid var(--color-divider)", paddingTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{ fontSize: 11, color: "var(--color-accent-800)" }}>سطر {toAr(selL.n)} · دقة {pct(selL.conf)}</span>
                <span style={{ fontSize: 14, lineHeight: 1.7 }}>{selL.text || "لم يُستخدم هذا السطر في أي حقل."}</span>
              </div>
            )}
            <LineLegend />
          </Frame>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 22, flex: "1 1 420px", minWidth: 0 }}>
          {filter === "lines" && (
            <div style={{ display: "flex", flexDirection: "column", borderTop: "1px solid var(--color-text)" }}>
              {pages.flatMap((p) =>
                p.lines.map((l, i) => (
                  <button key={l.id} type="button" className="rowbtn" onClick={() => selectLine({ id: l.id, page: p.page, n: i + 1, text: l.text, conf: l.confidence })}
                    style={{ display: "grid", gridTemplateColumns: "32px minmax(0,1fr) 56px", gap: 14, alignItems: "start", padding: "12px 8px", border: 0, borderBottom: "1px solid var(--color-divider)", cursor: "pointer", textAlign: "right", color: "inherit", font: "inherit", background: sel === l.id ? "var(--color-accent-100)" : "transparent" }}>
                    <span style={{ fontFamily: "var(--font-heading)", fontSize: 18, color: "var(--color-accent-700)" }}>{toAr(i + 1)}</span>
                    <span style={{ fontSize: 16, lineHeight: 1.75 }}>{l.text}</span>
                    <span className={`tag ${l.confidence < LOW ? "tag-outline" : "tag-accent"}`} style={{ justifyContent: "center" }}>{pct(l.confidence)}</span>
                  </button>
                )),
              )}
              {!pages.some((p) => p.lines.length) && <p style={{ padding: "12px 0", color: "var(--color-neutral-700)" }}>لم تصل الأسطر المقروءة بعد.</p>}
            </div>
          )}

          {filter !== "lines" && !data && (
            <p style={{ margin: 0, color: "var(--color-neutral-700)", display: "flex", gap: 8, alignItems: "center" }}>
              {deed?.err ?? <><Spinner className="h-4 w-4 text-[var(--color-accent)]" /> جارٍ تعبئة نموذج الصك…</>}
            </p>
          )}
          {filter !== "lines" && data && !data.extracted && (
            <p style={{ margin: 0, color: "var(--color-neutral-700)" }}>لم تُعبَّأ حقول نموذج الصك لهذا الطلب. استخدم «إعادة المعالجة».</p>
          )}

          {filter === "needs" && data?.extracted && lowList.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column" }}>
              <GroupTitle>الأقل دقة أولاً</GroupTitle>
              {[...lowList].sort((a, b) => (a.confidence ?? 0) - (b.confidence ?? 0)).map(row)}
            </div>
          )}

          {filter === "all" && data?.extracted &&
            (data.sections ?? [])
              .map((s) => ({ ...s, items: fields.filter((f) => f.section === s.id) }))
              .filter((s) => s.items.length)
              .map((s) => (
                <div key={s.id} style={{ display: "flex", flexDirection: "column" }}>
                  <GroupTitle>{sectionLabel[s.id]}</GroupTitle>
                  {s.items.map(row)}
                </div>
              ))}

          {filter === "all" && data?.extracted && missingKeys.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>إضافة حقل لم يُستخرج:</span>
              <select className="input" value={adding} onChange={(e) => setAdding(e.target.value)} style={{ flex: "1 1 220px", width: "auto" }}>
                <option value="">اختر حقلاً</option>
                {data.sections.map((s) => (
                  <optgroup key={s.id} label={s.label}>
                    {missingKeys.filter((k) => k.section === s.id).map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
                  </optgroup>
                ))}
              </select>
              <button className="btn btn-secondary" disabled={!adding} onClick={() => { setExtra((x) => [...x, adding]); setAdding(""); }}>إضافة</button>
            </div>
          )}

          {filter !== "lines" && data?.extracted && pending.length === 0 && (
            <Frame style={{ padding: "18px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <span style={{ fontWeight: 600 }}>راجعت كل الحقول منخفضة الدقة.</span>
              <span style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>النسخة الرقمية أدناه محدّثة.</span>
            </Frame>
          )}
        </div>
      </div>
    </>
  );
}

// ---------- step 4: digital copy (the user's deed template) ----------

export function CopyActions({ d, onPrint }: { d: ContractDetail; onPrint: () => void }) {
  const base = `/api/contracts/${d.contract.id}/download`;
  return (
    <div className="no-print" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      <DownloadBtn href={`${base}/docx`} ok={d.downloads.docx} label="Word" done="تم تنزيل النسخة الرقمية (Word)" />
      <DownloadBtn href={`${base}/pdf`} ok={d.downloads.pdf} label="PDF" done="تم تنزيل النسخة الرقمية (PDF)" />
      <DownloadBtn href={`${base}/embeddings`} ok={d.downloads.embeddings} label="البيانات (JSONL)" done="تم تنزيل البيانات المضمّنة (JSONL)" />
      <button className="btn btn-primary" onClick={onPrint}>طباعة</button>
    </div>
  );
}

/** The user's Electronic Deed Template filled with the current values, refreshed after every edit. */
export function DeedPreview({ id, version, frameRef }: { id: string; version: number; frameRef: RefObject<HTMLIFrameElement> }) {
  const box = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [height, setHeight] = useState(1200);
  useEffect(() => {
    // The template is a fixed A4-width page; scale it (up or down) so it fills the frame.
    const fit = () => box.current && setScale(box.current.clientWidth / 810);
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);
  const onLoad = () => {
    const doc = frameRef.current?.contentDocument;
    // Measure the content, not the document: scrollHeight never drops below the frame's current height.
    if (doc) setHeight(Math.ceil(doc.body.getBoundingClientRect().height) + 4);
  };
  return (
    <Frame as="article" style={{ background: "#fff", padding: 12, boxShadow: "var(--shadow-sm)" }}>
      <div ref={box} style={{ overflow: "hidden", height: height * scale }}>
        <iframe
          ref={frameRef}
          key={version}
          title="معاينة النسخة الرقمية"
          src={`/api/contracts/${id}/deed.html?v=${version}`}
          onLoad={onLoad}
          style={{ width: 810, height, border: 0, transform: `scale(${scale})`, transformOrigin: "top right" }}
        />
      </div>
    </Frame>
  );
}

export function PendingBanner({ n, onReview }: { n: number; onReview: () => void }) {
  if (!n) return null;
  return (
    <div className="no-print" style={{ display: "flex", gap: 10, alignItems: "center", padding: "10px 14px", background: "var(--color-accent-2-100)", color: "var(--color-accent-2-800)", fontSize: 14, borderRadius: 8, flexWrap: "wrap" }}>
      <span>{pendingLabel(n)} لم تُراجع بعد.</span>
      <button className="btn btn-ghost" onClick={onReview}>العودة للمراجعة</button>
    </div>
  );
}

// ---------- step 5: reviewer decision ----------

const hijriNow = (at?: string) =>
  new Date(at ?? Date.now()).toLocaleDateString("ar-SA-u-ca-islamic-umalqura-nu-arab", { day: "numeric", month: "long", year: "numeric" });

type Decided = { decision: "approve" | "reject" | "request_changes"; notes: string; at: string };

/** Summary row, optional note and the two decisions. Approve stays locked while low-accuracy fields
 *  are unreviewed; approving opens a confirmation pop-up with the files; the outcome then replaces the buttons. */
export function ReviewDecision({
  d, summary, pending, onReview, onReviewed, onNew, labels, modalLines, downloads,
}: {
  d: ContractDetail;
  summary: { label: string; value: string }[];
  pending: number;
  onReview?: () => void;
  onReviewed: () => void;
  onNew: () => void;
  labels: { approve: string; back: string; approvedTitle: string; approvedBody: string; backTitle: string; backBody: string; modalTitle: string };
  modalLines: ReactNode;
  downloads: ReactNode;
}) {
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [mine, setMine] = useState<Decided | null>(null);
  const [modal, setModal] = useState(false);

  const hr = d.state.human_review;
  const auto = d.contract.status === "approved";
  const decided: Decided | null = mine ?? (hr ? { decision: hr.decision, notes: hr.notes, at: hr.timestamp } : null);
  const canDecide = d.contract.status === "awaiting_review" && !decided;

  const send = async (decision: "approve" | "request_changes") => {
    setBusy(decision);
    setErr(null);
    try {
      await api.review(d.contract.id, { decision, notes, reviewer: "" });
      setMine({ decision, notes, at: new Date().toISOString() });
      if (decision === "approve") setModal(true);
      onReviewed();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const title = auto ? "اعتُمد تلقائياً" : decided?.decision === "approve" ? labels.approvedTitle : decided?.decision === "reject" ? "رُفض الطلب" : labels.backTitle;
  const body = auto
    ? "المخاطر منخفضة ودقة القراءة عالية، فاعتُمد دون مراجعة بشرية."
    : decided?.decision === "approve" ? labels.approvedBody : decided?.decision === "reject" ? "لن تصدر نسخة رقمية لهذا الطلب." : labels.backBody;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22, maxWidth: 760 }}>
      <StatGrid items={summary} min={140} size={30} />

      {canDecide && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="field">
            <label htmlFor="review-notes">ملاحظة المراجع (اختياري)</label>
            <textarea id="review-notes" className="input" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="مثال: تم التحقق من اسم القاضي من سجل المحكمة" />
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <button className="btn btn-primary" disabled={pending > 0 || !!busy} onClick={() => send("approve")} style={{ fontSize: 16, padding: "11px 22px" }}>
              {busy === "approve" && <Spinner className="h-4 w-4" />} {labels.approve}
            </button>
            <button className="btn btn-secondary" disabled={!!busy} onClick={() => send("request_changes")} style={{ fontSize: 16, padding: "11px 22px" }}>
              {busy === "request_changes" && <Spinner className="h-4 w-4" />} {labels.back}
            </button>
            {pending > 0 && (
              <span style={{ fontSize: 13, color: "var(--color-accent-800)" }}>
                راجع {pendingLabel(pending)} قبل الاعتماد ·{" "}
                <a href="#review" onClick={(e) => { e.preventDefault(); onReview?.(); }}>انتقل للمراجعة</a>
              </span>
            )}
          </div>
          {err && <span role="alert" style={{ fontSize: 13, color: "var(--color-danger)" }}>{err}</span>}
        </div>
      )}

      {(decided || auto) && (
        <Frame style={{ padding: 22, display: "flex", flexDirection: "column", gap: 12 }}>
          <span style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 24, color: decided?.decision === "reject" ? "var(--color-danger)" : "var(--color-accent-800)" }}>{title}</span>
          <span style={{ fontSize: 14, color: "var(--color-neutral-800)" }}>{body}</span>
          {decided?.notes && <span style={{ fontSize: 14, whiteSpace: "pre-line", borderInlineStart: "2px solid var(--color-accent-300)", paddingInlineStart: 10 }}>{decided.notes}</span>}
          {decided && <span style={{ fontSize: 12, color: "var(--color-neutral-700)" }}>{hijriNow(decided.at)}</span>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn btn-secondary" onClick={onNew}>صك جديد</button>
          </div>
        </Frame>
      )}

      {modal && (
        <Dialog label={labels.modalTitle} onClose={() => setModal(false)} style={{ width: "min(520px,100%)", background: "#fff", border: 0, borderRadius: 16, padding: 32, gap: 18, textAlign: "center", alignItems: "center" }}>
          <span style={{ width: 64, height: 64, borderRadius: "50%", background: "var(--color-accent-100)", color: "var(--color-accent)", display: "grid", placeItems: "center" }}>
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
          </span>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontFamily: "var(--font-arabic)", fontWeight: 700, fontSize: 24 }}>{labels.modalTitle}</span>
            <span style={{ fontSize: 14, color: "var(--color-neutral-700)", lineHeight: 1.7 }}>
              {modalLines}
              رقم الطلب <span dir="ltr">{d.contract.id}</span>
              <br />
              <span style={{ whiteSpace: "nowrap" }}>{hijriNow(mine?.at)}</span>
            </span>
          </div>
          <div style={{ width: "100%", display: "grid", gridTemplateColumns: `repeat(${summary.length},1fr)`, border: "1px solid var(--color-divider)", borderRadius: 10, overflow: "hidden" }}>
            {summary.map((sm) => (
              <div key={sm.label} style={{ padding: "12px 8px", borderInlineStart: "1px solid var(--color-divider)", marginInlineStart: -1, display: "flex", flexDirection: "column", gap: 2 }}>
                <span style={{ fontFamily: "var(--font-arabic)", fontWeight: 700, fontSize: 22, color: "var(--color-accent)" }}>{sm.value}</span>
                <span style={{ fontSize: 12, color: "var(--color-neutral-700)" }}>{sm.label}</span>
              </div>
            ))}
          </div>
          <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, alignSelf: "flex-start" }}>تنزيل الملفات</span>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>{downloads}</div>
          </div>
          <div style={{ width: "100%", display: "flex", gap: 8, marginTop: 4 }}>
            <button className="btn btn-primary" onClick={() => { setModal(false); onNew(); }} style={{ flex: 1, padding: 12 }}>مراجعة صك جديد</button>
            <button className="btn btn-ghost" onClick={() => setModal(false)} style={{ padding: "12px 18px" }}>إغلاق</button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  api,
  CLAUSE_TYPE_AR,
  DOC_TYPE_AR,
  pct,
  RESULT_AR,
  SEVERITY_AR,
  STATUS_AR,
  toAr,
  countAr,
  type AgentEvent,
  type ContractDetail,
  type ContractRow,
  type EnhanceInfo,
  type Health,
  type SearchHit,
  type OcrPage,
  type Rule,
  type Severity,
} from "./api";
import { Spinner } from "./components/icons";
import { LineLegend, PageViewer } from "./components/PageViewer";
import {
  CopyActions, DeedPreview, DeedReview, deedSummary, isLowConf, PendingBanner, pendingLabel, pendingOf, ReviewDecision, ReviewFilter, useDeed, type Filter,
} from "./components/Deed";
import { Header, Hero } from "./components/Landing";
import { DownloadBtn, Frame, Seg, StatGrid, TimelineStep, ToastProvider, useToast, type StepState } from "./components/ui";

// ---------- small UI pieces ----------

type ToneName = "success" | "warning" | "danger" | "info" | "muted";
const TONE: Record<ToneName, string> = {
  success: "tag-accent",
  warning: "tag-outline",
  danger: "tag-danger",
  info: "tag-accent",
  muted: "tag-neutral",
};
const Chip = ({ tone = "muted", children }: { tone?: ToneName; children: ReactNode }) => <span className={`tag ${TONE[tone]}`}>{children}</span>;
const SEV_TONE: Record<Severity, ToneName> = { critical: "danger", high: "warning", medium: "info", low: "muted" };
const confTone = (c: number): ToneName => (c >= 0.85 ? "success" : c >= 0.5 ? "warning" : "danger");
const muted = { color: "var(--color-neutral-700)" };

const scrollToId = (id: string) =>
  setTimeout(() => {
    const el = document.getElementById(id);
    if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 10, behavior: "smooth" });
  }, 60);

function PageTabs({ pages, value, onChange }: { pages: number[]; value: number; onChange: (p: number) => void }) {
  if (pages.length < 2) return null;
  return <Seg value={String(value)} onChange={(v) => onChange(Number(v))} options={pages.map((p) => ({ value: String(p), label: `صفحة ${toAr(p)}` }))} />;
}

// ---------- platform: upload, history, search ----------

function UploadCard({ onStarted, busy, setBusy }: { onStarted: (id: string) => void; busy: string | null; setBusy: (s: string | null) => void }) {
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [samples, setSamples] = useState<{ key: string; label_ar: string; file_name: string }[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const flash = useToast();
  useEffect(() => {
    api.samples().then(setSamples).catch(() => undefined);
  }, []);

  const run = async (label: string, fn: () => Promise<{ id: string }>) => {
    setError(null);
    setBusy(label);
    try {
      onStarted((await fn()).id);
      flash("تم استلام الصك");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const submit = (f: File) => {
    if (f.size > 20 * 1024 * 1024) return setError("حجم الملف يتجاوز ٢٠ ميجابايت");
    run(f.name, () => api.upload(f));
  };

  return (
    <Frame style={{ flex: "2 1 480px", minWidth: 0, padding: 18, display: "flex", flexDirection: "column", gap: 14 }}>
      <div
        className="dropzone"
        data-drag={drag}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          const f = e.dataTransfer.files?.[0];
          if (f) submit(f);
        }}
        onClick={() => !busy && input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        role="button"
        tabIndex={0}
        aria-label="اختر ملف الصك أو اسحبه هنا"
        style={{ border: "1.5px dashed var(--color-accent-300)", borderRadius: 10, padding: 22, display: "flex", alignItems: "center", gap: 16, cursor: "pointer" }}
      >
        <span style={{ width: 48, height: 48, borderRadius: "50%", background: "var(--color-accent-100)", color: "var(--color-accent)", display: "grid", placeItems: "center", flex: "none" }}>
          {busy ? (
            <Spinner className="h-5 w-5" />
          ) : (
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="m17 8-5-5-5 5" /><path d="M12 3v12" /></svg>
          )}
        </span>
        <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontWeight: 600, fontSize: 17 }}>{busy ? `جارٍ إرسال ${busy}` : "اسحب الصك هنا أو اضغط للاختيار"}</span>
          <span style={{ fontSize: 13, ...muted }}>PDF، صور (JPG، PNG، TIFF، HEIC)، Word · بحد أقصى ٢٠ ميجابايت</span>
        </span>
        <input
          ref={input}
          type="file"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) submit(f);
            e.target.value = "";
          }}
        />
      </div>
      {samples.length > 0 && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: 13, ...muted }}>أو جرّب:</span>
          {samples.map((s) => (
            <button key={s.key} className="btn btn-secondary" disabled={!!busy} onClick={() => run(s.label_ar, () => api.runSample(s.key))} style={{ fontSize: 13, padding: "6px 12px" }}>
              {s.label_ar}
            </button>
          ))}
        </div>
      )}
      {error && <div role="alert" style={{ fontSize: 14, color: "var(--color-danger)" }}>{error}</div>}
    </Frame>
  );
}

const rowTag = (h: ContractRow, current: boolean) =>
  current ? ["الحالي", "tag-outline"] : [STATUS_AR[h.status]?.label ?? h.status, ["approved", "approved_by_reviewer"].includes(h.status) ? "tag-accent" : "tag-neutral"];

function History({ rows, current, onOpen }: { rows: ContractRow[]; current: string | null; onOpen: (id: string) => void }) {
  return (
    <Frame style={{ flex: "1 1 260px", minWidth: 0, padding: 18, display: "flex", flexDirection: "column", gap: 8 }}>
      <span style={{ fontWeight: 600 }}>الطلبات السابقة</span>
      <div style={{ maxHeight: 220, overflowY: "auto" }}>
        {rows.length === 0 && <span style={{ fontSize: 13, ...muted }}>لا توجد طلبات بعد.</span>}
        {rows.map((h) => {
          const [label, cls] = rowTag(h, h.id === current);
          return (
            <button key={h.id} type="button" className="rowbtn" onClick={() => onOpen(h.id)}
              style={{ width: "100%", padding: "8px 4px", border: 0, borderBottom: "1px solid var(--color-divider)", display: "flex", justifyContent: "space-between", gap: 10, fontSize: 14, background: "transparent", color: "inherit", cursor: "pointer", textAlign: "right", font: "inherit" }}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{h.title || h.file_name}</span>
              <span className={`tag ${cls}`} style={{ flex: "none" }}>{label}</span>
            </button>
          );
        })}
      </div>
    </Frame>
  );
}

function SearchCard({ onOpen }: { onOpen: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async (e: FormEvent) => {
    e.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      setHits(await api.search(q.trim(), 8));
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Frame as="form" onSubmit={run} style={{ flex: "1 1 100%", padding: "14px 18px", display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
      <label htmlFor="search" style={{ fontWeight: 600 }}>بحث بالمعنى في الصكوك السابقة</label>
      <input id="search" className="input" value={q} onChange={(e) => { setQ(e.target.value); setHits(null); }} placeholder="مثال: شروط الوقف، اسم القاضي، المحكمة الكبرى" style={{ flex: "1 1 280px", width: "auto", borderRadius: 8 }} />
      <button className="btn btn-primary" type="submit" disabled={busy}>{busy && <Spinner className="h-4 w-4" />} بحث</button>
      {err && <div style={{ flexBasis: "100%", fontSize: 13, color: "var(--color-danger)" }}>{err}</div>}
      {hits && (
        <div style={{ flexBasis: "100%", display: "flex", flexDirection: "column" }}>
          {hits.map((h, i) => (
            <button key={i} type="button" className="rowbtn" onClick={() => onOpen(h.contract_id)}
              style={{ padding: "8px 0", border: 0, borderTop: "1px solid var(--color-divider)", display: "flex", gap: 12, fontSize: 14, background: "transparent", color: "inherit", cursor: "pointer", textAlign: "right", font: "inherit", alignItems: "baseline" }}>
              <span className="tag tag-accent" style={{ flex: "none" }}>{h.title || h.file_name}{h.page ? ` · صفحة ${toAr(h.page)}` : ""}</span>
              <span style={{ flex: 1 }}>{h.text}</span>
              <span style={{ fontSize: 11, ...muted, flex: "none" }}>تشابه {pct(h.score)}</span>
            </button>
          ))}
          {hits.length === 0 && <span style={{ fontSize: 13, ...muted, paddingTop: 8 }}>لا نتائج مطابقة.</span>}
        </div>
      )}
    </Frame>
  );
}

// ---------- live data ----------

function useContract(id: string | null) {
  const [detail, setDetail] = useState<ContractDetail | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const load = useCallback(() => {
    if (id) api.get(id).then(setDetail).catch(() => undefined);
  }, [id]);
  useEffect(() => {
    setDetail(null);
    setEvents([]);
    if (!id) return;
    load();
    const es = new EventSource(`/api/contracts/${id}/events`);
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data) as AgentEvent;
      setEvents((prev) => [...prev, ev]);
      if (ev.status === "done" || ev.status === "error" || ev.status === "waiting") load();
    };
    return () => es.close();
  }, [id, load]);
  return { detail, events, reload: load };
}

// ---------- steps ----------

type EnhanceItem = { page: number; info: EnhanceInfo | null; error?: string; skipped?: boolean };

function Intake({ d, enhanced }: { d: ContractDetail; enhanced: boolean }) {
  const t = d.state.intake!;
  return (
    <>
      <StatGrid items={[
        { label: "نوع المستند", value: DOC_TYPE_AR[t.doc_type] ?? t.doc_type },
        { label: "المصدر", value: t.is_scanned ? "ممسوح ضوئياً" : "رقمي" },
        { label: "الصفحات", value: toAr(t.page_count) },
        { label: "الجودة", value: pct(t.quality_score) },
      ]} />
      {(t.issues_ar.length > 0 || enhanced) && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {t.issues_ar.length > 0 && (
            <>
              <span style={{ fontWeight: 600 }}>ملاحظات على الصورة</span>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {t.issues_ar.map((i) => <span key={i} className="tag tag-outline" style={{ fontSize: 13, padding: "5px 12px" }}>{i}</span>)}
              </div>
            </>
          )}
          {enhanced && (
            <p style={{ margin: "6px 0 0", fontSize: 14, color: "var(--color-neutral-800)" }}>
              دقة الصورة منخفضة، لذا حُسّنت قبل القراءة. القراءة تتم على النسخة المحسّنة، ويبقى الأصل هو المرجع.
            </p>
          )}
        </div>
      )}
    </>
  );
}

const enhanceTags = (info: EnhanceInfo) => (
  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", direction: "ltr" }}>
    <span className="tag tag-accent">{info.model}</span>
    <span className="tag tag-neutral">{info.from_size.join("×")} → {info.size.join("×")} px</span>
    <span className="tag tag-neutral">{info.seconds} s</span>
  </div>
);

/** Slider comparison between the original and the enhanced page. */
function Enhancement({ id, items }: { id: string; items: EnhanceItem[] }) {
  const [page, setPage] = useState(items[0]?.page ?? 1);
  const [cmp, setCmp] = useState(50);
  const cur = items.find((p) => p.page === page) ?? items[0];
  if (!cur) return null;
  const [w, h] = cur.info?.from_size ?? [3, 4];
  return (
    <>
      <PageTabs pages={items.map((p) => p.page)} value={cur.page} onChange={setPage} />
      {cur.info ? (
        <Frame as="figure" style={{ padding: 12, margin: 0 }}>
          <div style={{ position: "relative", aspectRatio: `${w}/${h}`, maxHeight: 720, margin: "0 auto", overflow: "hidden", background: "var(--color-surface)" }}>
            <img src={`/api/contracts/${id}/pages/${cur.page}?variant=original`} alt="الأصل" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "contain" }} />
            <img src={`/api/contracts/${id}/pages/${cur.page}?variant=enhanced`} alt="بعد التحسين" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "contain", clipPath: `inset(0 ${100 - cmp}% 0 0)` }} />
            <div style={{ position: "absolute", top: 0, bottom: 0, left: `${cmp}%`, width: 1, background: "var(--color-accent)" }} />
            <span className="tag" style={{ position: "absolute", top: 10, right: 10, background: "var(--color-bg)", border: "1px solid var(--color-divider)" }}>الأصل · المرجع</span>
            <span className="tag" style={{ position: "absolute", top: 10, left: 10, background: "var(--color-accent-900)", color: "var(--color-bg)" }}>بعد التحسين · ما يقرؤه النموذج</span>
          </div>
          <input type="range" min={0} max={100} value={cmp} onChange={(e) => setCmp(+e.target.value)} aria-label="مقارنة" style={{ width: "100%", marginTop: 12, direction: "ltr" }} />
        </Frame>
      ) : cur.skipped ? (
        <p style={{ margin: 0, ...muted }}>دقة هذه الصفحة كافية، فقُرئت كما هي دون تحسين.</p>
      ) : (
        <p style={{ margin: 0, color: "var(--color-accent-2-800)" }}>لم تُحسَّن هذه الصفحة{cur.error ? `: ${cur.error}` : ""}، وقُرئت الصورة الأصلية.</p>
      )}
    </>
  );
}

/** Page image with line boxes and (once read) the lines list, for the contract flow. */
function OcrLines({ id, pages, listLines = true }: { id: string; pages: OcrPage[]; listLines?: boolean }) {
  const [page, setPage] = useState(pages[0]?.page ?? 1);
  const [sel, setSel] = useState<string | null>(null);
  const [variant, setVariant] = useState<"original" | "enhanced">("enhanced");
  const cur = pages.find((p) => p.page === page) ?? pages[0];
  if (!cur) return null;
  const shown = cur.enhanced ? variant : "original";
  return (
    <>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <PageTabs pages={pages.map((p) => p.page)} value={cur.page} onChange={setPage} />
        <Chip>{countAr(listLines ? cur.lines.length || cur.boxes.length : cur.boxes.length, "line")}</Chip>
        <Chip tone={cur.stamps.length ? "warning" : "muted"}>{countAr(cur.stamps.length, "stamp")}</Chip>
        {cur.enhanced && (
          <span style={{ marginInlineStart: "auto" }}>
            <Seg small value={variant} onChange={setVariant} options={[{ value: "original", label: "الأصل" }, { value: "enhanced", label: "بعد التحسين" }]} />
          </span>
        )}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 28, alignItems: "flex-start" }}>
        <Frame as="figure" style={{ flex: listLines ? "0 1 380px" : "1 1 520px", maxWidth: listLines ? undefined : 680, minWidth: 260, padding: 10, margin: 0, display: "flex", flexDirection: "column", gap: 10 }}>
          <PageViewer contractId={id} page={listLines ? cur : { ...cur, lines: [] }} selected={sel} onSelect={setSel} variant={shown} />
          <LineLegend />
        </Frame>
        {listLines && cur.lines.length > 0 && (
          <div style={{ flex: "1 1 420px", minWidth: 0, maxHeight: 720, overflowY: "auto", borderTop: "1px solid var(--color-text)" }}>
            {cur.lines.map((l, i) => (
              <button key={l.id} type="button" className="rowbtn" onClick={() => setSel(l.id)}
                style={{ width: "100%", display: "grid", gridTemplateColumns: "32px minmax(0,1fr) 56px", gap: 14, alignItems: "start", padding: "12px 8px", border: 0, borderBottom: "1px solid var(--color-divider)", cursor: "pointer", textAlign: "right", color: "inherit", font: "inherit", background: sel === l.id ? "var(--color-accent-100)" : "transparent" }}>
                <span style={{ fontFamily: "var(--font-heading)", fontSize: 18, color: "var(--color-accent-700)" }}>{toAr(i + 1)}</span>
                <span style={{ fontSize: 16, lineHeight: 1.75 }}>{l.text}</span>
                <Chip tone={confTone(l.confidence)}>{pct(l.confidence)}</Chip>
              </button>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

function Structure({ d }: { d: ContractDetail }) {
  const dig = d.state.digital!;
  const keyGroups = dig.key_fields.reduce<Record<string, typeof dig.key_fields>>((acc, f) => {
    (acc[f.section || "البيانات الأساسية"] ||= []).push(f);
    return acc;
  }, {});
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div className="flex flex-wrap gap-2">
        <Chip>العنوان: {dig.title || "غير محدد"}</Chip>
        <Chip>{countAr(dig.clauses.length, "clause")}</Chip>
        <Chip>{countAr(dig.parties.length, "party")}</Chip>
        <Chip>{countAr(dig.dates.length, "date")}</Chip>
        <Chip tone="info">القراءة: {dig.ocr_engine}</Chip>
      </div>
      {Object.entries(keyGroups).map(([section, items]) => (
        <div key={section}>
          <div style={{ background: "var(--color-accent-800)", color: "#fff", padding: "10px 16px", fontWeight: 600, fontSize: 14 }}>{section}</div>
          <dl className="grid sm:grid-cols-2" style={{ margin: 0, border: "1px solid var(--color-divider)", borderTop: 0 }}>
            {items.map((f, i) => (
              <div key={i} className="flex" style={{ borderBottom: "1px solid var(--color-divider)" }}>
                <dt style={{ width: "40%", padding: "10px 14px", background: "var(--color-accent-100)", fontWeight: 700, fontSize: 14 }}>{f.label}</dt>
                <dd style={{ flex: 1, margin: 0, padding: "10px 14px", fontSize: 15, background: f.confidence < 0.85 ? "var(--color-accent-2-100)" : undefined }}>{f.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
      {dig.parties.length > 0 && (
        <table className="table">
          <tbody>
            {dig.parties.map((p, i) => (
              <tr key={i}><td style={{ fontWeight: 600 }}>{p.role_ar}</td><td>{p.name}</td><td style={muted}>{p.id_number ?? ""}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      <div style={{ borderTop: "1px solid var(--color-text)" }}>
        {dig.clauses.map((c) => (
          <div key={c.clause_id} style={{ padding: "12px 0", borderBottom: "1px solid var(--color-divider)" }}>
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 18, color: "var(--color-accent-800)" }}>{c.number_raw || c.heading || "-"}</span>
              {c.number_raw && c.heading && <span style={{ fontWeight: 600 }}>{c.heading}</span>}
              <Chip tone={confTone(c.confidence)}>{pct(c.confidence)}</Chip>
              <span style={{ fontSize: 11, ...muted }} dir="ltr">{c.clause_id}</span>
            </div>
            <div style={{ whiteSpace: "pre-line", lineHeight: 1.8 }}>{c.text}</div>
          </div>
        ))}
      </div>
      {dig.dates.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {dig.dates.map((t, i) => <Chip key={i}>{t.raw} {t.iso ? `(${t.iso})` : ""}</Chip>)}
        </div>
      )}
    </div>
  );
}

function Downloads({ d }: { d: ContractDetail }) {
  const base = `/api/contracts/${d.contract.id}/download`;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <DownloadBtn href={`${base}/docx`} ok={d.downloads.docx} label="Word" done="تم تنزيل النسخة الرقمية (Word)" />
        <DownloadBtn href={`${base}/pdf`} ok={d.downloads.pdf} label="PDF" done="تم تنزيل النسخة الرقمية (PDF)" />
        <DownloadBtn href={`${base}/report`} ok={d.downloads.report} label="تقرير المراجعة" done="تم تنزيل تقرير المراجعة" />
        <DownloadBtn href={`${base}/embeddings`} ok={d.downloads.embeddings} label="البيانات (JSONL)" done="تم تنزيل البيانات المضمّنة (JSONL)" />
      </div>
      {(d.state.outputs?.warnings ?? []).map((w) => <span key={w} style={{ fontSize: 12, ...muted }}>{w}</span>)}
    </div>
  );
}

function ClauseTypes({ d }: { d: ContractDetail }) {
  const clauses = Object.fromEntries((d.state.digital?.clauses ?? []).map((c) => [c.clause_id, c]));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, overflowX: "auto" }}>
      <table className="table" style={{ minWidth: 520 }}>
        <thead><tr>{["البند", "النوع", "أنواع ثانوية", "الثقة"].map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>
          {(d.state.clause_types ?? []).map((t) => (
            <tr key={t.clause_id}>
              <td>{clauses[t.clause_id]?.number_raw || clauses[t.clause_id]?.heading || t.clause_id}</td>
              <td style={{ fontWeight: 600 }}>{CLAUSE_TYPE_AR[t.type] ?? t.type}</td>
              <td style={muted}>{t.secondary_types.map((s) => CLAUSE_TYPE_AR[s] ?? s).join("، ") || "-"}</td>
              <td>{pct(t.confidence)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {(d.state.missing_types ?? []).length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span style={{ fontSize: 13, ...muted }}>أنواع مفقودة:</span>
          {d.state.missing_types!.map((m) => <Chip key={m} tone="warning">{CLAUSE_TYPE_AR[m] ?? m}</Chip>)}
        </div>
      )}
    </div>
  );
}

function Policy({ d, rules }: { d: ContractDetail; rules: Record<string, Rule> }) {
  const clauses = Object.fromEntries((d.state.digital?.clauses ?? []).map((c) => [c.clause_id, c]));
  const tone = { pass: "success", fail: "danger", missing: "warning", not_applicable: "muted" } as const;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="table" style={{ minWidth: 720 }}>
        <thead><tr>{["القاعدة", "البند", "الحالة", "الخطورة", "الدليل"].map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>
          {(d.state.policy_results ?? []).map((r) => {
            const c = r.clause_id ? clauses[r.clause_id] : undefined;
            return (
              <tr key={r.rule_id}>
                <td>
                  <div style={{ fontWeight: 600 }}>{rules[r.rule_id]?.title_ar}</div>
                  <div style={{ fontSize: 11, ...muted }} dir="ltr">{r.rule_id}</div>
                </td>
                <td>{c ? c.number_raw || c.heading || c.clause_id : "-"}</td>
                <td><Chip tone={tone[r.status]}>{RESULT_AR[r.status]}</Chip></td>
                <td><Chip tone={SEV_TONE[r.severity]}>{SEVERITY_AR[r.severity]}</Chip></td>
                <td style={{ maxWidth: 420, lineHeight: 1.7 }}>
                  {r.evidence_ar && <div style={{ marginBottom: 4, background: "var(--color-neutral-100)", padding: "2px 8px" }}>«{r.evidence_ar}»</div>}
                  <div style={muted}>{r.explanation_ar}</div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Redlines({ d }: { d: ContractDetail }) {
  const clauses = Object.fromEntries((d.state.digital?.clauses ?? []).map((c) => [c.clause_id, c]));
  const s = d.state;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div className="flex flex-wrap items-center gap-2">
        <span style={{ fontWeight: 600 }}>درجة المخاطر {toAr(s.risk_score ?? 0)} من ١٠٠</span>
        {(s.score_breakdown ?? []).map((b) => <Chip key={b.rule_id} tone={SEV_TONE[b.severity]}>{b.rule_id} +{toAr(b.points)}</Chip>)}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,340px),1fr))", gap: 20 }}>
        {(s.redlines ?? []).map((r, i) => {
          const c = r.clause_id ? clauses[r.clause_id] : undefined;
          return (
            <Frame key={i} style={{ padding: 18, display: "flex", flexDirection: "column", gap: 8 }}>
              <div className="flex flex-wrap items-center gap-2">
                <span style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 18 }}>{c ? c.number_raw || c.heading : "بند جديد مقترح"}</span>
                <Chip tone={SEV_TONE[r.severity]}>{SEVERITY_AR[r.severity]}</Chip>
                <span style={{ fontSize: 11, ...muted }} dir="ltr">{r.rule_id}</span>
              </div>
              <div style={{ fontWeight: 600, fontSize: 14 }}>{r.issue_ar}</div>
              {r.original_ar && <div style={{ background: "#fef3f2", color: "var(--color-danger)", padding: 8, lineHeight: 1.8, textDecoration: "line-through" }}>{r.original_ar}</div>}
              <div style={{ background: "var(--color-accent-100)", color: "var(--color-accent-800)", padding: 8, lineHeight: 1.8 }}>{r.suggested_ar}</div>
              <div style={{ fontSize: 12, ...muted }}>{r.rationale_ar}</div>
            </Frame>
          );
        })}
      </div>
    </div>
  );
}

// ---------- page ----------

const AGENTS = ["intake", "digitize", "reconstruct", "clause", "policy", "redline", "decide"] as const;

const DEED_LABELS = {
  approve: "اعتماد النسخة الرقمية",
  back: "طلب نسخة أوضح",
  approvedTitle: "اعتُمدت النسخة الرقمية",
  approvedBody: "حُفظت القيم المراجَعة وفُهرس الصك للبحث. يمكنك تنزيل النسخة من مرحلة «النسخة الرقمية».",
  backTitle: "طُلبت نسخة أوضح من الصك",
  backBody: "سيُعاد الطلب إلى مقدّمه مع ملاحظاتك، ولن تصدر نسخة رقمية حتى تصل صورة أوضح.",
  modalTitle: "تم اعتماد النسخة الرقمية",
};
const CONTRACT_LABELS = {
  approve: "اعتماد العقد",
  back: "طلب تعديلات",
  approvedTitle: "اعتُمد العقد",
  approvedBody: "حُفظ قرارك وفُهرس العقد للبحث. يمكنك تنزيل الملفات من مرحلة «النسخة الرقمية».",
  backTitle: "طُلبت تعديلات على العقد",
  backBody: "سيُعاد العقد إلى مقدّمه مع ملاحظاتك.",
  modalTitle: "تم اعتماد العقد",
};

function Page() {
  const [id, setId] = useState<string | null>(() => new URLSearchParams(location.search).get("id"));
  const [busy, setBusy] = useState<string | null>(null);
  const [history, setHistory] = useState<ContractRow[]>([]);
  const [rules, setRules] = useState<Record<string, Rule>>({});
  const [health, setHealth] = useState<Health | null>(null);
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const [filterPick, setFilterPick] = useState<Filter | null>(null);
  const deedFrame = useRef<HTMLIFrameElement>(null);
  const { detail: d, events, reload } = useContract(id);

  const refreshMeta = useCallback(() => {
    api.list().then(setHistory).catch(() => undefined);
    api.health().then(setHealth).catch(() => undefined);
  }, []);
  useEffect(() => {
    refreshMeta();
    api.rules().then((r) => setRules(Object.fromEntries(r.rules.map((x) => [x.id, x])))).catch(() => undefined);
  }, [refreshMeta]);
  useEffect(() => {
    window.history.replaceState(null, "", id ? `?id=${id}` : location.pathname);
    setClosed({});
    setFilterPick(null);
  }, [id]);
  useEffect(() => {
    if (d?.contract.status && d.contract.status !== "processing") refreshMeta();
  }, [d?.contract.status, refreshMeta]);

  const open = (newId: string) => {
    setId(newId);
    refreshMeta();
    scrollToId("request");
  };
  const newDeed = () => {
    setId(null);
    scrollToId("start");
  };

  // Last event per agent, plus live segmentation/OCR pages from events until the state has them.
  const last = useMemo(() => {
    const m: Record<string, AgentEvent> = {};
    for (const e of events) m[e.agent] = e;
    return m;
  }, [events]);
  const livePages = useMemo(() => {
    const byPage = new Map<number, OcrPage>();
    for (const e of events) {
      const data = e.data as Record<string, unknown>;
      if (data.kind === "layout") byPage.set(data.page as number, { ...(data as unknown as OcrPage), lines: [] });
      if (data.kind === "ocr") {
        const p = byPage.get(data.page as number);
        if (p) byPage.set(p.page, { ...p, lines: data.lines as OcrPage["lines"] });
      }
    }
    return [...byPage.values()].sort((a, b) => a.page - b.page);
  }, [events]);
  const pagesFromState = d?.state.ocr_pages;
  const enhanceItems = useMemo(() => {
    const m = new Map<number, EnhanceItem>();
    for (const e of events) {
      const data = e.data as Record<string, unknown>;
      if (data.kind === "enhance") {
        const pg = data.page as number;
        m.set(pg, data.error ? { page: pg, info: null, error: data.error as string } : data.skipped ? { page: pg, info: null, skipped: true } : { page: pg, info: data as unknown as EnhanceInfo });
      }
    }
    // After a reload the events may be gone; the saved pages still say which were enhanced.
    if (!m.size) for (const p of pagesFromState ?? []) m.set(p.page, p.enhanced ? { page: p.page, info: p.enhanced } : { page: p.page, info: null, skipped: true });
    return [...m.values()].sort((a, b) => a.page - b.page);
  }, [events, pagesFromState]);

  const s = d?.state;
  // With no request open, the deed steps are shown blank, ready for a new deed.
  const isDeed = !id || ["court_record", "property_deed"].includes(s?.intake?.doc_type ?? "");
  const pages: OcrPage[] = s?.ocr_pages?.length ? s.ocr_pages : livePages;
  const finished = !!s?.decision;
  const deed = useDeed(isDeed ? id : null, !!s?.deed_fields);
  const pending = pendingOf(deed.data).length;
  const anyEnhanced = enhanceItems.some((x) => x.info);
  const hasLow = (deed.data?.fields ?? []).some(isLowConf);
  const filter: Filter = filterPick ?? (deed.data?.extracted ? (hasLow ? "needs" : "all") : "lines");

  const stateOf = (agent: (typeof AGENTS)[number], present: boolean): StepState => {
    const e = last[agent];
    if (e?.status === "error") return "error";
    if (present || e?.status === "done") return "done";
    if (e && (e.status === "started" || e.status === "info")) return "running";
    return finished ? "skipped" : "waiting";
  };
  const msg = (agent: string) => last[agent]?.message_ar;
  const step = (key: string) => ({ open: !closed[key], onToggle: (o: boolean) => setClosed((c) => ({ ...c, [key]: !o })) });
  const goReview = () => {
    setFilterPick("needs");
    setClosed((c) => ({ ...c, review: false }));
    scrollToId("review");
  };

  const digitizeState = stateOf("digitize", !!s?.digital);
  const hr = s?.human_review;
  const decisionState: StepState = hr || d?.contract.status === "approved" ? "done" : d?.contract.status === "awaiting_review" ? "active" : d?.contract.status === "error" ? "error" : finished ? "running" : "waiting";
  const decisionSub = hr
    ? hr.decision === "approve" ? "معتمد" : hr.decision === "reject" ? "مرفوض" : isDeed ? "أُعيد لطلب نسخة أوضح" : "أُعيد لطلب تعديلات"
    : d?.contract.status === "approved" ? "معتمد تلقائياً" : d?.contract.status === "reviewing" ? "جارٍ تسجيل القرار" : "بانتظار المراجع";

  const headerTag = (() => {
    if (!d) return null;
    const st = d.contract.status;
    if (hr) return hr.decision === "approve" ? ["معتمد من المراجع", "tag-accent"] : hr.decision === "reject" ? ["مرفوض", "tag-danger"] : [isDeed ? "طُلبت نسخة أوضح" : "مطلوب تعديلات", "tag-outline"];
    if (isDeed && st === "awaiting_review") return pending ? ["قيد المراجعة", "tag-outline"] : ["جاهز للاعتماد", "tag-accent"];
    return [STATUS_AR[st]?.label ?? st, st === "approved" || st === "approved_by_reviewer" ? "tag-accent" : st === "error" ? "tag-danger" : "tag-outline"];
  })();
  const title = (isDeed && deed.data?.fields.find((f) => f.key === "header.title" && f.status !== "skipped")?.value) || s?.digital?.title || d?.contract.file_name || "طلب";
  const fieldVal = (k: string) => deed.data?.fields.find((f) => f.key === k && f.status !== "skipped")?.value;

  const enhanceStep = (n: number) => (
    <TimelineStep n={n} title="تحسين الصورة" {...step("enhance")}
      state={enhanceItems.length ? "done" : digitizeState === "running" ? "running" : finished ? "skipped" : "waiting"}
      desc={anyEnhanced ? "اسحب المؤشر للمقارنة بين الأصل والنسخة التي يقرؤها النموذج." : "دقة الصورة كافية، فقُرئت كما هي."}
      sub={enhanceItems.find((x) => x.info)?.info?.model ?? (enhanceItems.length ? "لم تحتج الصورة إلى تحسين" : finished ? "التحسين متوقف في الإعدادات" : undefined)}
      aside={enhanceItems.find((x) => x.info)?.info && enhanceTags(enhanceItems.find((x) => x.info)!.info!)}>
      {enhanceItems.length > 0 && <Enhancement id={id!} items={enhanceItems} />}
    </TimelineStep>
  );
  const usedKraken = pages.some((p) => p.segmenter === "kraken");
  const krakenFailed = !usedKraken && pages.some((p) => p.segmenter_fallback);
  const segStep = (n: number) => (
    <TimelineStep n={n} title="تقسيم الصفحة: الأسطر والأختام" {...step("seg")}
      state={pages.length ? "done" : digitizeState === "running" ? "running" : digitizeState}
      desc={usedKraken
        ? "نموذج Kraken يحدد كل سطر بإطار يتبع ميله وانحناءه، وتحليل الصورة يحدد الأختام. هذه الإطارات تُرسم على الصورة التي يقرؤها Gemini."
        : krakenFailed
          ? "تعذر تشغيل نموذج Kraken لهذا الطلب، فحُددت الأسطر بتحليل الصورة (إطارات مستقيمة). السبب في سجل الخادم."
          : "تحليل الصورة يحدد الأسطر والأختام. هذه الإطارات تُرسم على الصورة التي يقرؤها Gemini."}
      sub={pages.length
        ? `${countAr(pages.reduce((a, p) => a + p.boxes.length, 0), "line")} و${countAr(pages.reduce((a, p) => a + p.stamps.length, 0), "stamp")}${usedKraken ? " · نموذج Kraken" : ""}`
        : digitizeState === "running"
          ? `جارٍ تقسيم الصفحة إلى أسطر ${health?.segmenter === "classic" ? "بتحليل الصورة" : "بنموذج Kraken"}…`
          : health?.segmenter === "classic" ? "تحليل الصورة" : "نموذج Kraken"}
      aside={pages.length > 0 && (
        <span className={`tag ${usedKraken ? "tag-accent" : krakenFailed ? "tag-danger" : "tag-neutral"}`} dir="ltr">
          {usedKraken ? "Kraken · blla" : krakenFailed ? "Kraken failed · classic layout" : "classic layout"}
        </span>
      )}>
      {pages.length > 0 && <OcrLines id={id!} pages={pages} listLines={false} />}
    </TimelineStep>
  );
  const intakeStep = (
    <TimelineStep n={1} title="الاستلام" {...step("intake")} state={stateOf("intake", !!s?.intake)}
      desc="فحص أولي لنوع المستند وجودته قبل القراءة."
      sub={s?.intake ? `${s.intake.is_scanned ? "ممسوح ضوئياً" : "رقمي"} · جودة ${pct(s.intake.quality_score)}` : msg("intake")}>
      {d && s?.intake && <Intake d={d} enhanced={anyEnhanced} />}
    </TimelineStep>
  );

  const deedDownloads = d && (
    <>
      <DownloadBtn href={`/api/contracts/${d.contract.id}/download/docx`} ok={d.downloads.docx} label="Word" done="تم تنزيل النسخة الرقمية (Word)" />
      <DownloadBtn href={`/api/contracts/${d.contract.id}/download/pdf`} ok={d.downloads.pdf} label="PDF" done="تم تنزيل النسخة الرقمية (PDF)" />
      <DownloadBtn href={`/api/contracts/${d.contract.id}/download/${isDeed ? "embeddings" : "report"}`} ok={isDeed ? d.downloads.embeddings : d.downloads.report} label={isDeed ? "JSONL" : "التقرير"} done={isDeed ? "تم تنزيل البيانات المضمّنة (JSONL)" : "تم تنزيل تقرير المراجعة"} />
    </>
  );

  return (
    <div dir="rtl" style={{ minHeight: "100vh", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)" }}>
      <Header
        onHome={() => window.scrollTo({ top: 0, behavior: "smooth" })}
        warning={health && health.ocr_engine === "gemini" && !health.gemini_key_set && (
          <div role="alert" style={{ marginTop: 12, padding: "10px 14px", background: "var(--color-accent-2-100)", color: "var(--color-accent-2-800)", fontSize: 13, borderRadius: 8 }}>
            لم يُضبط مفتاح GEMINI_API_KEY في ملف ‎.env‎، فلن تُقرأ الصفحات. أضف المفتاح ثم أعد تشغيل الخادم.
          </div>
        )}
      />

      <Hero />

      <div id="start" style={{ maxWidth: 1180, margin: "0 auto", padding: "0 28px", scrollMarginTop: 16 }}>
        <div style={{ borderTop: "1px solid var(--color-divider)", paddingTop: 40, display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
          <h2 style={{ fontSize: 40, margin: 0 }}>المنصة</h2>
          <span style={muted}>ارفع صكاً أو جرّب أحد الأمثلة.</span>
        </div>
      </div>

      <main id="main" style={{ maxWidth: 1180, margin: "0 auto", padding: "28px 28px 64px", display: "flex", flexDirection: "column", gap: 28 }}>
        <section className="no-print" style={{ display: "flex", flexWrap: "wrap", gap: 20 }}>
          <UploadCard onStarted={open} busy={busy} setBusy={setBusy} />
          <History rows={history} current={id} onOpen={open} />
          <SearchCard onOpen={open} />
        </section>

        <section id="request" style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 28, scrollMarginTop: 16 }}>
            <div className="no-print" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 16, flexWrap: "wrap", paddingBottom: 18, borderBottom: "1px solid var(--color-divider)" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={{ fontSize: 12, ...muted }}>
                  {id ? (
                    <>
                      طلب <span dir="ltr">{id}</span>
                      {d && <> · <span dir="ltr">{d.contract.file_name}</span></>}
                    </>
                  ) : (
                    "ارفع صكاً أو جرّب أحد الأمثلة أعلاه لتبدأ."
                  )}
                </span>
                <h1 style={{ fontSize: 36, margin: 0 }}>{id ? title : "مراجعة صك جديد"}</h1>
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                {id && finished && (
                  <button className="btn btn-ghost" style={{ fontSize: 13 }} title="معالجة الملف نفسه من جديد في طلب جديد"
                    onClick={() => api.rerun(id).then((r) => open(r.id)).catch(() => undefined)}>
                    إعادة المعالجة
                  </button>
                )}
                {!id ? (
                  <span className="tag tag-neutral" style={{ fontSize: 13, padding: "5px 12px" }}>بانتظار الصك</span>
                ) : headerTag ? (
                  <span className={`tag ${headerTag[1]}`} style={{ fontSize: 13, padding: "5px 12px" }}>{headerTag[0]}</span>
                ) : (
                  <Spinner className="h-5 w-5 text-[var(--color-accent)]" />
                )}
              </div>
            </div>

            {(s?.errors ?? []).length > 0 && (
              <div role="alert" style={{ padding: "10px 14px", background: "#fef3f2", color: "var(--color-danger)", fontSize: 14, borderRadius: 8 }}>
                {s!.errors!.map((e) => <div key={e}>{e}</div>)}
              </div>
            )}

            {isDeed ? (
              <>
                {intakeStep}
                {enhanceStep(2)}
                {segStep(3)}

                <TimelineStep n={4} id="review" title="قراءة الصك ومراجعة الحقول" {...step("review")}
                  state={last.deed?.status === "error" || digitizeState === "error" ? "error" : deed.data?.extracted ? (pending ? "active" : "done") : pages.length ? "running" : finished ? "skipped" : "waiting"}
                  desc={deed.data?.extracted ? `ملأ النموذج ${countAr(deed.data.fields.length, "field")} من نموذج الصك. الحقول التي دقتها أقل من ٨٥٪ تحتاج قرارك.` : last.deed?.message_ar ?? msg("digitize")}
                  sub={deed.data?.extracted ? (pending ? `${pendingLabel(pending)} بانتظارك` : "تمت المراجعة") : last.deed?.message_ar ?? (pages.length ? msg("digitize") : undefined)}
                  aside={pages.length > 0 && (
                    <ReviewFilter value={filter} onChange={setFilterPick} pending={pending} hasFields={!!deed.data?.extracted}
                      lines={pages.reduce((a, p) => a + p.lines.length, 0)} />
                  )}>
                  {pages.length > 0 && <DeedReview id={id!} pages={pages} deed={s?.deed_fields ? deed : null} filter={filter} enhanced={pages.some((p) => !!p.enhanced)} />}
                </TimelineStep>

                <TimelineStep n={5} title="النسخة الرقمية" {...step("copy")}
                  state={deed.data?.extracted ? (pending ? "active" : "done") : finished ? "skipped" : "waiting"}
                  desc="نموذج الصك الإلكتروني مملوءاً بالقيم المراجَعة. يتحدث مع كل تعديل."
                  sub={id ? "معاينة حية" : undefined}
                  aside={d && <CopyActions d={d} onPrint={() => deedFrame.current?.contentWindow?.print()} />}>
                  {deed.data?.extracted && (
                    <>
                      <PendingBanner n={pending} onReview={goReview} />
                      <DeedPreview id={id!} version={deed.version} frameRef={deedFrame} />
                    </>
                  )}
                </TimelineStep>

                <TimelineStep n={6} last title="القرار والمراجعة البشرية" {...step("decide")} state={decisionState}
                  desc="لا يعتمد النظام أي صك تلقائياً. القرار لك." sub={id ? decisionSub : undefined}>
                  {d && finished && (
                    <ReviewDecision d={d} summary={deedSummary(deed.data)} pending={pending} onReview={goReview} onReviewed={reload} onNew={newDeed}
                      labels={DEED_LABELS} downloads={deedDownloads}
                      modalLines={<>{[fieldVal("basic.deed_type") && `صك ${fieldVal("basic.deed_type")}`, fieldVal("header.court")].filter(Boolean).join(" · ")}{(fieldVal("basic.deed_type") || fieldVal("header.court")) && <br />}</>} />
                  )}
                </TimelineStep>
              </>
            ) : (
              <>
                {intakeStep}
                {enhanceStep(2)}

                {segStep(3)}

                <TimelineStep n={4} title="القراءة الآلية للنص" {...step("ocr")} state={digitizeState}
                  desc={s?.ocr_confidence ? `أدنى ثقة ${pct(s.ocr_confidence.min)}، المتوسط ${pct(s.ocr_confidence.mean)}` : msg("digitize")}
                  sub={s?.ocr_confidence ? `أدنى ثقة ${pct(s.ocr_confidence.min)}` : msg("digitize")}>
                  {pages.some((p) => p.lines.length) && <OcrLines id={id} pages={pages.filter((p) => p.lines.length)} />}
                </TimelineStep>

                <TimelineStep n={5} title="البنية: العنوان والبنود والبيانات" {...step("structure")} state={stateOf("digitize", !!s?.digital)}
                  desc="Claude Haiku يجمّع الأسطر فقط ولا يغيّر النص." sub={s?.digital ? countAr(s.digital.clauses.length, "clause") : undefined}>
                  {s?.digital && d && <Structure d={d} />}
                </TimelineStep>

                <TimelineStep n={6} title="النسخة الرقمية" {...step("copy")} state={stateOf("reconstruct", !!d?.downloads.docx)}
                  desc="قالب Word وPDF في الكود." sub={msg("reconstruct")}>
                  {d && finished && <Downloads d={d} />}
                </TimelineStep>

                <TimelineStep n={7} title="تصنيف البنود" {...step("clause")} state={stateOf("clause", !!s?.clause_types)} desc="Claude Haiku" sub={msg("clause")}>
                  {s?.clause_types && d && <ClauseTypes d={d} />}
                </TimelineStep>

                <TimelineStep n={8} title="فحص السياسات" {...step("policy")} state={stateOf("policy", !!s?.policy_results)} desc="Claude Sonnet مقابل rules.json" sub={msg("policy")}>
                  {s?.policy_results && d && <Policy d={d} rules={rules} />}
                </TimelineStep>

                <TimelineStep n={9} title="المخاطر والتعديلات المقترحة" {...step("redline")} state={stateOf("redline", !!s?.redlines)}
                  desc="الدرجة بالكود، والصياغات من قوالب معتمدة." sub={msg("redline")}>
                  {s?.redlines && d && <Redlines d={d} />}
                </TimelineStep>

                <TimelineStep n={10} last title="القرار والمراجعة البشرية" {...step("decide")} state={decisionState}
                  desc="القرار الأخير للمراجع." sub={decisionSub}>
                  {d && finished && (
                    <ReviewDecision d={d} pending={0} onReviewed={reload} onNew={newDeed} labels={CONTRACT_LABELS} downloads={deedDownloads} modalLines={null}
                      summary={[
                        { label: "درجة المخاطر", value: toAr(s?.risk_score ?? 0) },
                        { label: "أدنى ثقة", value: pct(s?.ocr_confidence?.min) },
                        { label: "مخالفات", value: toAr((s?.policy_results ?? []).filter((r) => r.status === "fail" || r.status === "missing").length) },
                        { label: "تعديلات مقترحة", value: toAr((s?.redlines ?? []).length) },
                      ]} />
                  )}
                </TimelineStep>
              </>
            )}
        </section>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <Page />
    </ToastProvider>
  );
}

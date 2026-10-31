import type * as React from "react";
import { Frame } from "./ui";

/** The "scan" mark from the design: corner brackets, a scan line and the letter م. */
export function LogoMark() {
  const c = "2.5px solid var(--color-accent)";
  const corner = (pos: React.CSSProperties) => <span style={{ position: "absolute", width: 12, height: 12, ...pos }} />;
  return (
    <div aria-hidden="true" style={{ position: "relative", width: 44, height: 44, flex: "none", display: "grid", placeItems: "center" }}>
      {corner({ top: 0, right: 0, borderTop: c, borderRight: c })}
      {corner({ top: 0, left: 0, borderTop: c, borderLeft: c })}
      {corner({ bottom: 0, right: 0, borderBottom: c, borderRight: c })}
      {corner({ bottom: 0, left: 0, borderBottom: c, borderLeft: c })}
      <span style={{ position: "absolute", left: 5, right: 5, top: "50%", height: 2, background: "var(--color-accent-2)" }} />
      <span style={{ fontFamily: "var(--font-arabic)", fontWeight: 700, fontSize: 22, lineHeight: 1, color: "var(--color-accent)", marginTop: -6, position: "relative" }}>م</span>
    </div>
  );
}

export function Header({ onHome, warning }: { onHome: () => void; warning?: React.ReactNode }) {
  return (
    <header className="no-print" style={{ maxWidth: 1280, margin: "0 auto", padding: "18px 28px", borderBottom: "1px solid var(--color-divider)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 28 }}>
        <button type="button" onClick={onHome} style={{ display: "flex", alignItems: "center", gap: 12, marginLeft: "auto", cursor: "pointer", background: "none", border: 0, padding: 0, textAlign: "right", color: "inherit" }}>
          <LogoMark />
          <span style={{ display: "flex", flexDirection: "column", lineHeight: 1.3, gap: 6 }}>
            <span style={{ fontFamily: "var(--font-arabic)", fontWeight: 700, fontSize: 22, color: "var(--color-accent)" }}>مُراجِع</span>
            <span style={{ fontSize: 11, color: "var(--color-neutral-700)" }}>رقمنة الصكوك والوثائق القضائية</span>
          </span>
        </button>
        <span className="tag" style={{ background: "var(--color-accent-2-700)", color: "#fff", fontWeight: 700 }}>نموذج تجريبي</span>
      </div>
      {warning}
    </header>
  );
}


const STAGES = [
  { n: "٠١", title: "الاستلام", body: "يتعرف على نوع المستند وعدد صفحاته وجودته، ويرصد الأختام والحواف المقطوعة والكتابة الباهتة.", meta: "Claude Haiku · vision" },
  { n: "٠٢", title: "تحسين الصورة", body: "يكبّر صور الجوال الصغيرة أربع مرات لتسهل قراءتها. الصورة الأصلية لا تُمس وتبقى المرجع.", meta: "Real-ESRGAN x4plus" },
  { n: "٠٣", title: "تقسيم الصفحة", body: "يحدد كل سطر بإطار يتبع ميله وانحناءه، ويرصد الأختام، ثم تُرسم الإطارات المرقّمة على الصورة التي تُقرأ.", meta: "Kraken · blla" },
  { n: "٠٤", title: "القراءة", body: "يقرأ كل سطر كما هو دون تصحيح أو إكمال، ويضع [؟] مكان الكلمة غير المقروءة.", meta: "Gemini OCR · Arabic" },
  { n: "٠٥", title: "مراجعتك", body: "يملأ نموذج الصك الإلكتروني ويعرض الحقول الأقل دقة أولاً لتؤكدها أو تعدّلها أو تتخطاها.", meta: "threshold 0.85" },
  { n: "٠٦", title: "النسخة الرقمية", body: "نسخة Word وPDF بالقيم المراجَعة، وبيانات قابلة للبحث بالمعنى في كل الصكوك السابقة.", meta: "DOCX · PDF · JSONL" },
];

const scrollToId = (id: string) => {
  const el = document.getElementById(id);
  if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 10, behavior: "smooth" });
};

export function Hero() {
  return (
    <section className="no-print" style={{ maxWidth: 1180, margin: "0 auto", padding: "0 28px" }}>
      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,440px),1fr))", gap: 56, alignItems: "center", padding: "72px 0 80px" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
          <span style={{ fontSize: 13, color: "var(--color-accent-700)", fontWeight: 500 }}>للمحامين وموظفي وزارة العدل وكتابات العدل</span>
          <h1 style={{ fontSize: "clamp(44px,6vw,76px)", lineHeight: 1.5, margin: 0, textWrap: "balance" } as React.CSSProperties}>من صكٍّ ورقي قديم إلى نسخة رقمية تُراجَع وتُعتمد</h1>
          <p style={{ fontSize: 18, lineHeight: 1.75, maxWidth: "52ch", margin: 0, color: "var(--color-neutral-800)", textWrap: "pretty" } as React.CSSProperties}>
            ارفع صورة الصك، ولو كانت ممسوحة بجودة ضعيفة أو مكتوبة بخط اليد. يقرأ مُراجِع النص ويملأ نموذج الصك الإلكتروني، ثم تراجع أنت الحقول غير المؤكدة قبل إصدار النسخة الرقمية.
          </p>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 6 }}>
            <button className="btn btn-primary" onClick={() => scrollToId("start")} style={{ fontSize: 17, padding: "12px 22px", gap: 10 }}>
              جرّب على صك وقف حقيقي
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5" /><path d="m12 19-7-7 7-7" /></svg>
            </button>
            <a className="btn btn-secondary" href="#how" onClick={(e) => { e.preventDefault(); scrollToId("how"); }} style={{ fontSize: 17, padding: "12px 22px" }}>كيف يعمل</a>
          </div>
        </div>
        <Frame as="figure" style={{ padding: 14, margin: "0 auto", width: "100%", maxWidth: 440 }}>
          <div style={{ position: "relative" }}>
            <img src="/assets/deed-hero-boxes.jpg" alt="صك وقف قديم مع إطارات الأسطر المقروءة" style={{ width: "100%", display: "block" }} />
          </div>
          <figcaption style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 12, marginTop: 10, color: "var(--color-neutral-700)" }}>
            <span>صك وقف · ١٣٨٢هـ</span>
            <span style={{ whiteSpace: "nowrap" }}>٣٧٠×٥٤٠ بكسل · جودة ٤٠٪</span>
          </figcaption>
        </Frame>
      </section>

      <section id="how" style={{ padding: "56px 0", borderTop: "1px solid var(--color-divider)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "end", gap: 24, flexWrap: "wrap", marginBottom: 32 }}>
          <h2 style={{ fontSize: 40, margin: 0 }}>ست مراحل، والقرار الأخير لك</h2>
          <p style={{ margin: 0, maxWidth: "44ch", color: "var(--color-neutral-700)" }}>يقرأ الذكاء الاصطناعي ويقترح، أما الحكم على دقة كل حقل فيبقى للمراجع.</p>
        </div>
        <Frame style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))" }}>
          {STAGES.map((s) => (
            <div key={s.n} style={{ padding: "24px 22px 26px", borderInlineStart: "1px solid var(--color-divider)", display: "flex", flexDirection: "column", gap: 10, marginInlineStart: -1 }}>
              <span style={{ fontFamily: "var(--font-heading)", fontSize: 44, lineHeight: 1, color: "var(--color-accent)" }}>{s.n}</span>
              <h3 style={{ fontSize: 22, margin: 0 }}>{s.title}</h3>
              <p style={{ margin: 0, fontSize: 14, lineHeight: 1.7, color: "var(--color-neutral-800)" }}>{s.body}</p>
              <span style={{ marginTop: "auto", paddingTop: 10, fontSize: 11, color: "var(--color-neutral-700)", direction: "ltr", textAlign: "right" }}>{s.meta}</span>
            </div>
          ))}
        </Frame>
      </section>
    </section>
  );
}

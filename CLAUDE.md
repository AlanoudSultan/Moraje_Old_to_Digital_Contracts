# CLAUDE.md: Arabic Contract Review Pipeline (HUMAIN Hackathon, Challenge 3)

You are building a working, demo-ready web app for the HUMAIN Future AI Builders Hackathon, Challenge 3 (Autonomous Enterprise). The agent owns one business process end to end: reviewing Arabic contracts, including old scanned ones, with minimal human intervention.

Read this whole file before writing code. Then read every file in `agent_skills/` and `config/rules.json`.

---

## 1. What we are building

A multi-agent pipeline, orchestrated with LangGraph, where each agent calls Claude through the Anthropic Python SDK. A web front end, styled like Saudi government service portals, lets a user upload a contract, watch the agents work live, and download the results. Escalated contracts go to a reviewer queue.

Pipeline (7 agents, 3 stages):

| # | Agent | Stage | Model | Notes |
|---|-------|-------|-------|-------|
| 1 | Intake | Digitize | claude-haiku-5-5 (vision) | Scanned or digital, quality score, doc type |
| 2 | Digitization | Digitize | claude-opus-5-5 (vision) | Page images to structured JSON with per-block confidence |
| 3 | Reconstruction | Digitize | No LLM by default | Deterministic: JSON to RTL .docx and .pdf, stamps as cropped images, labelled "نسخة رقمية" |
| 4 | Clause | Review | claude-sonnet-5-5 | Maps each clause to a clause type |
| 5 | Policy | Review | claude-sonnet-5-5 | Checks clauses against `config/rules.json` |
| 6 | Redline | Review | claude-sonnet-5-5 | Risk score 0 to 100, Arabic rewording for flagged clauses |
| 7 | Decision | Route | Deterministic rule + claude-haiku-5-5 | Rule decides the route; Haiku writes the Arabic reviewer summary |

Routing: approve automatically only if risk is low AND OCR confidence is high. Otherwise escalate to a human with an Arabic summary of flagged clauses.

Model IDs: confirm the current IDs and SDK syntax with the `claude-api` skill before coding. Keep model names in `backend/app/config.py`, never hard-coded in agents.

Design rule for the judges: an LLM is only used where it adds value. Reconstruction and the routing threshold are plain code. Say this in the README; it also shows cost awareness.

---

## 2. Tech stack

- Backend: Python 3.11+, FastAPI, LangGraph, `anthropic` SDK, Pydantic v2
- Checkpointing: LangGraph `SqliteSaver` (needed for human-in-the-loop `interrupt`)
- PDF and images: PyMuPDF (`fitz`) for rasterizing pages at 200 DPI, Pillow for cropping stamps
- Documents: `python-docx` for RTL Word output; LibreOffice headless (`soffice --headless --convert-to pdf`) for the PDF copy. Follow the `docx` and `pdf` skills.
- Frontend: React + Vite + TypeScript + Tailwind CSS, `dir="rtl"` and `lang="ar"` at the root
- Live progress: Server-Sent Events from FastAPI to the browser
- Storage: local filesystem under `data/contracts/<id>/` plus SQLite. No cloud services.
- Secrets: `ANTHROPIC_API_KEY` in `.env`, with a `.env.example` committed

---

## 3. Repository layout

```
.
├── CLAUDE.md
├── README.md
├── agent_skills/            # runtime instructions for each agent (system prompts)
│   ├── intake/SKILL.md
│   ├── digitization/SKILL.md
│   ├── reconstruction/SKILL.md
│   ├── clause/SKILL.md
│   ├── policy/SKILL.md
│   ├── redline/SKILL.md
│   └── decision/SKILL.md
├── config/rules.json
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI routes
│   │   ├── config.py          # models, thresholds, paths
│   │   ├── state.py           # ContractState TypedDict + Pydantic models
│   │   ├── graph.py           # LangGraph StateGraph
│   │   ├── llm.py             # Claude client, skill loader, structured output helper
│   │   ├── agents/            # one module per agent
│   │   ├── tools/             # pdf_render.py, docx_builder.py, arabic_text.py
│   │   └── events.py          # SSE event bus
│   └── tests/
├── frontend/
├── samples/                  # 3 synthetic demo contracts
└── data/                     # gitignored
```

`agent_skills/` is NOT `.claude/skills/`. Those are runtime prompts for the product agents, loaded by `llm.py`. Do not move them.

---

## 4. Shared state (backend/app/state.py)

```python
class ContractState(TypedDict, total=False):
    contract_id: str
    file_path: str
    page_images: list[str]            # paths to rendered PNGs
    intake: IntakeResult              # is_scanned, quality_score, doc_type, page_count, readable
    digital: DigitalContract          # title, parties, clauses, tables, dates, stamps, signatures
    ocr_confidence: OcrConfidence     # min, mean, low_conf_block_ids
    outputs: Outputs                  # docx_path, pdf_path
    clause_types: list[ClauseTyping]  # clause_id, type, secondary_types, confidence
    policy_results: list[PolicyResult]# rule_id, clause_id | None, status, severity, evidence_ar
    redlines: list[Redline]           # clause_id, severity, issue_ar, original_ar, suggested_ar, rationale_ar
    risk_score: int                   # 0 to 100
    decision: Literal["approve", "escalate"]
    decision_reasons: list[str]
    reviewer_summary_ar: str
    human_review: HumanReview | None  # decision, notes, reviewer, timestamp
    errors: list[str]
```

Define every nested type as a Pydantic model and reuse the same models as the JSON schemas for Claude's structured outputs, so agent output is validated before it enters state.

---

## 5. Graph (backend/app/graph.py)

```
START -> intake
intake -> digitize            if intake.readable
intake -> decide              if not readable (forces escalate, reason "unreadable")
digitize -> reconstruct -> clause -> policy -> redline -> decide
decide -> END                 if decision == "approve"
decide -> human_review        if decision == "escalate"
human_review -> finalize -> END
```

- `human_review` calls LangGraph `interrupt()` with the reviewer summary. The reviewer page resumes it with `Command(resume={...})`.
- Use `thread_id = contract_id` for the checkpointer.
- Every node emits SSE events: `{"agent": "...", "status": "started|done|error", "message_ar": "...", "data": {...}}`.
- On any agent exception: log to `errors`, emit an error event, and route to `decide`, which must escalate. The pipeline never crashes silently and never auto-approves after an error.
- Optional speed-up once it works: run `reconstruct` in parallel with `clause` (both only need `digital`).

Follow the `langgraph-fundamentals` and `langgraph-persistence` skills for StateGraph, conditional edges, checkpointer, and interrupt syntax.

---

## 6. LLM helper (backend/app/llm.py)

- `load_skill(name)`: read `agent_skills/<name>/SKILL.md`, strip YAML frontmatter, return the body as the system prompt.
- `call_structured(skill, model, content_blocks, output_model)`: send system prompt + user content, force JSON matching `output_model.model_json_schema()` (use the SDK's structured output or tool-use pattern per the `claude-api` skill), validate with Pydantic, retry once with the validation error appended, then raise.
- Use prompt caching on the system prompt and on `rules.json` (they repeat on every call).
- Vision: send page PNGs as base64 image blocks. Digitization sends one page per call, run concurrently with a semaphore of 4, then merges.
- Log token usage per agent per contract to SQLite and show the total cost on the result page.

---

## 7. Agent implementation notes

Each agent's behavior, inputs, output schema, and rules are in its SKILL.md. Implementation specifics:

1. **Intake**: if the upload is a PDF with an extractable text layer of real Arabic text, `is_scanned = False`. Still render page images for every input; the Digitization agent always gets images, and also the text layer when it exists.
2. **Digitization**: per-page calls, then merge: join clauses that continue across pages, renumber, dedupe headers and footers. Bounding boxes are normalized 0 to 1 relative to the page image.
3. **Reconstruction**: pure Python in `tools/docx_builder.py`.
   - RTL: set `w:bidi` on every paragraph and `w:rtl` on every run; section and table direction RTL.
   - Font: "IBM Plex Sans Arabic" if installed, else "Arial". Embed nothing else.
   - Header on every page: "نسخة رقمية" plus the source file name and the processing date (Gregorian and Hijri).
   - Stamps and signatures: crop from the page PNG using the bbox, insert as images at their position in the clause flow, captioned "ختم (صورة من الأصل)".
   - Blocks below the confidence threshold get a yellow highlight and a footnote "يحتاج تحقق".
   - Convert to PDF with LibreOffice headless. If LibreOffice is missing, skip the PDF and add a warning, do not fail.
4. **Clause / Policy / Redline**: text only, no images. Pass clause IDs everywhere so the UI can link findings to clauses.
5. **Decision**: the route is computed in code from `config/rules.json -> decision_thresholds`. Claude (Haiku) only writes `reviewer_summary_ar` and only when escalating.

---

## 8. API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/contracts` | Upload (pdf, png, jpg, docx). Returns `{id}` and starts the graph in a background task |
| GET | `/api/contracts/{id}/events` | SSE stream of agent events |
| GET | `/api/contracts/{id}` | Full state for the result page |
| GET | `/api/contracts/{id}/download/{docx\|pdf\|report}` | Files |
| GET | `/api/contracts/{id}/pages/{n}` | Original page image |
| GET | `/api/review-queue` | Escalated contracts waiting for a human |
| POST | `/api/contracts/{id}/review` | `{decision: approve\|reject\|request_changes, notes}` resumes the interrupt |
| GET | `/api/rules` | Current rules for display |

The `report` download is a short Arabic review report (.docx) listing findings, redlines, risk score, decision, and reasons.

---

## 9. Frontend design: Saudi government portal style

The look should feel like a Saudi ministry e-service (najiz.sa, moj.gov.sa), which follow the Digital Government Authority's unified design system, "Platforms Code" (platformscode.sa). Check platformscode.sa for exact tokens if reachable and prefer them over the approximations below. Use the `frontend-design` skill for quality, but these constraints win.

### Tokens (approximate, verify against Platforms Code)
```
--primary:        #1B8354   /* government green */
--primary-hover:  #166A45
--primary-soft:   #E7F4EC   /* breadcrumb bar, chips, active tab underline bg */
--gold:           #B7975B   /* small accents only */
--bg:             #F9FAFB
--surface:        #FFFFFF
--border:         #E5E7EB
--text:           #1F2A37
--text-muted:     #6C737F
--success: #067647  --warning: #B54708  --danger: #B42318  --info: #175CD3
radius: 8px cards, 6px buttons; shadows very soft; generous whitespace
font: "IBM Plex Sans Arabic" (Google Fonts), weights 400/500/600/700
```

### Layout patterns to reproduce
- Top utility strip (light gray, thin) with accessibility controls on the left: font size up/down, high contrast toggle, read aloud icon. Make them actually work (CSS variables).
- Main header: white, logo on the right, horizontal nav with items: الرئيسية، الخدمات الإلكترونية، دليل الاستخدام، تواصل معنا. Active item is a filled green block. Login-style button on the left reading "دخول المراجع".
- Breadcrumb bar in `--primary-soft`: الرئيسية > الخدمات الإلكترونية > مراجعة عقد. Share, print, bookmark icons on the left.
- Service page pattern (like the MoJ service page): big title, one-line description, green "طلب الخدمة" button, tabs (الخطوات، شروط الخدمة، المستندات المطلوبة، يجب أن تعرف), numbered steps list, and a side info card with icon tiles: لغة الخدمة، قنوات تقديم الخدمة، الجهة المستفيدة (chips)، مدة تنفيذ الخدمة.
- Home hero: large bold title, subtitle, subtle dotted circle pattern background in light green/gray, then 3 to 4 white cards with an icon, title, short text, and a green button.
- Cookie/privacy notice card at the bottom ("خصوصيتك تهمنا") with a green "متابعة" button, dismissible, stored in localStorage.
- Dark footer with links.
- Everything RTL. Numbers can show Arabic-Indic digits in Arabic text. Mobile responsive.

### Do NOT copy (important)
- No "موقع حكومي مسجل لدى هيئة الحكومة الرقمية" banner, no Saudi emblem, no ministry or Najiz logos, no gov.sa wording or domain. That would impersonate a government site.
- Use our own product name and logo (a simple green check mark plus wordmark, e.g. "مُراجِع" or another name the user picks).
- Show a small badge in the header: "نموذج تجريبي - هاكاثون".

### Pages
1. `/` Home: hero "المنصة الذكية لمراجعة العقود", cards: مراجعة عقد جديد، تتبع طلب، قائمة المراجع، القواعد والسياسات.
2. `/services/contract-review`: the service page pattern above, with the upload dropzone (drag and drop, pdf/jpg/png/docx, max 20 MB).
3. `/requests/:id`: live tracker. A horizontal 3-stage stepper (رقمنة، مراجعة، توجيه) with the 7 agents as sub-steps, each showing spinner/check/error and its Arabic status message from SSE. When done:
   - Decision banner (green "معتمد تلقائياً" or orange "محال للمراجع") with risk score gauge and OCR confidence.
   - Side-by-side view: original page image on one side, digital clauses on the other; clicking a clause highlights its bbox on the image.
   - Findings table: clause, type, rule, status chip (مطابق / مخالف / مفقود), severity.
   - Redline cards: original text struck through in red, suggested text in green, rationale.
   - Downloads: النسخة الرقمية (Word)، النسخة الرقمية (PDF)، تقرير المراجعة.
   - Small "تكلفة المعالجة" line with tokens and estimated cost.
4. `/reviewer`: queue table of escalated contracts; detail view shows the Arabic summary and flagged clauses with approve / reject / request changes plus notes. Submitting resumes the graph and the request page updates.
5. `/rules`: read-only view of rules.json grouped by clause type.

---

## 10. Arabic handling (tools/arabic_text.py)

- Never "fix" or paraphrase contract wording during digitization. Transcribe exactly; unclear words become `[؟]` and lower that block's confidence.
- Normalize only for matching and search (a separate normalized field): strip tatweel, unify alef forms, convert Arabic-Indic digits to Western digits. Keep original text untouched for output.
- Recognize clause numbering forms: "البند الأول"، "المادة (١)"، "أولاً:"، "١-".
- Dates: capture both Hijri and Gregorian when present; store raw text plus parsed ISO date when possible (`hijri-converter` package).
- All user-facing strings in the UI and agent messages are Arabic.

---

## 11. Demo data (samples/)

Create three synthetic Arabic contracts (do not use real contracts or real people):
1. `clean_service_agreement.pdf`: digital, compliant, should auto-approve.
2. `risky_supply_contract.pdf`: digital, unlimited liability, foreign governing law, no data protection clause, 120-day payment. Should escalate on policy.
3. `old_scanned_lease.jpg`: a photographed or scanned-looking page (render text, add a stamp graphic, blur and skew it). Should escalate on low OCR confidence.

Add a "جرّب بعقد تجريبي" option on the upload page that loads these.

---

## 12. Build order (the deadline is tight, stop at a working cut line)

Work in this order and get each step running before the next. Commit after each.

1. Scaffold backend and frontend, `.env.example`, `make dev` (or two npm/uvicorn commands) to run both.
2. `llm.py` + state models + Intake + Digitization on a single image. Print JSON. **Test with sample 3.**
3. Reconstruction to RTL .docx. Open the file and check direction, font, stamp image, header.
4. Clause, Policy, Redline, Decision. Run end to end from CLI on all 3 samples.
5. LangGraph wiring with checkpointer, SSE, and the API.
6. Frontend: upload, live tracker, result page. **MVP cut line: if time runs out, ship here.**
7. Reviewer queue with interrupt and resume.
8. Polish: side-by-side bbox highlight, rules page, accessibility controls, cost line, README with architecture diagram and screenshots.

---

## 13. Quality bar and tests

- `backend/tests/`: schema validation for each agent output (mock Claude), graph routing tests (approve path, policy escalate, OCR escalate, error escalate, unreadable escalate), docx RTL test (open the file and assert `w:bidi`).
- Run the 3 samples end to end and record: decision, risk score, min OCR confidence, total tokens. Put this table in the README.
- No secrets in git. No full contract text in logs.
- Disclaimer in UI footer and README: decision support only, not legal advice; rules are illustrative and must be set by the organization's legal team.

---

## 14. How to work with me

- Ask before adding a dependency not listed here.
- Prefer small, verifiable steps. After each build step, tell me what to run to see it working.
- No em dashes in UI copy or docs.

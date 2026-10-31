export type AgentEvent = {
  agent: string;
  status: "started" | "done" | "error" | "waiting" | "info";
  message_ar: string;
  data: Record<string, unknown>;
};

export type ContractRow = {
  id: string;
  file_name: string;
  created_at: string;
  updated_at: string;
  status: string;
  decision: "approve" | "escalate" | null;
  risk_score: number | null;
  ocr_min: number | null;
  doc_type: string | null;
  title: string | null;
  mock: number;
};

export type Region = { page: number; bbox: number[] };
export type Clause = {
  clause_id: string;
  number_raw: string | null;
  heading: string | null;
  text: string;
  block_ids: string[];
  pages: number[];
  regions: Region[];
  confidence: number;
};
export type Stamp = { id: string; page: number; bbox: number[]; kind: string; near_block_id: string | null };
export type KeyField = { section: string | null; label: string; value: string; confidence: number };
export type Party = { role_ar: string; name: string; id_number: string | null };
export type PolicyResult = {
  rule_id: string;
  clause_id: string | null;
  status: "pass" | "fail" | "missing" | "not_applicable";
  severity: Severity;
  evidence_ar: string;
  explanation_ar: string;
  confidence: number;
};
export type Severity = "critical" | "high" | "medium" | "low";
export type Redline = {
  clause_id: string | null;
  rule_id: string;
  severity: Severity;
  issue_ar: string;
  original_ar: string;
  suggested_ar: string;
  rationale_ar: string;
};

export type OcrLine = { id: string; text: string; confidence: number; bbox: number[]; polygon?: number[][] };
export type OcrPage = {
  page: number;
  skew: number;
  boxes: number[][];
  polygons?: number[][][]; // Kraken's line outlines (normalized points), parallel to boxes
  segmenter?: "kraken" | "classic";
  segmenter_fallback?: boolean; // Kraken was meant to run but was missing or failed
  stamps: { bbox: number[]; overlaps_text: boolean }[];
  lines: OcrLine[];
  enhanced?: EnhanceInfo | null;
  read_from?: "enhanced" | "original";
  sent_to_model?: { file: string; size: number[]; kb: number; enhanced: boolean } | null;
};
export type EnhanceInfo = { model: string; seconds: number; from_size: number[]; size: number[] };

export type ContractState = {
  contract_id?: string;
  file_name?: string;
  intake?: {
    is_scanned: boolean;
    quality_score: number;
    doc_type: string;
    page_count: number;
    readable: boolean;
    issues_ar: string[];
  };
  digital?: {
    title: string | null;
    parties: Party[];
    key_fields: KeyField[];
    clauses: Clause[];
    stamps: Stamp[];
    dates: { raw: string; calendar: string; iso: string | null }[];
    ocr_engine: string;
  };
  ocr_confidence?: { min: number; mean: number; low_conf_block_ids: string[] };
  ocr_pages?: OcrPage[];
  deed_fields?: DeedField[];
  score_breakdown?: { rule_id: string; severity: Severity; points: number }[];
  outputs?: { warnings: string[] };
  clause_types?: { clause_id: string; type: string; secondary_types: string[]; confidence: number }[];
  missing_types?: string[];
  policy_results?: PolicyResult[];
  redlines?: Redline[];
  risk_score?: number;
  decision?: "approve" | "escalate";
  decision_reasons?: string[];
  reviewer_summary_ar?: string;
  human_review?: { decision: "approve" | "reject" | "request_changes"; notes: string; reviewer: string; timestamp: string };
  final_status?: string;
  errors?: string[];
};

export type Usage = {
  per_agent: { agent: string; model: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number }[];
  total_tokens: number;
  total_cost_usd: number;
};

export type ContractDetail = {
  contract: ContractRow;
  state: ContractState;
  page_count: number;
  downloads: { docx: boolean; pdf: boolean; report: boolean; embeddings: boolean };
  usage: Usage;
  events: AgentEvent[];
};

export type Rule = {
  id: string;
  clause_type: string;
  title_ar: string;
  check: string;
  severity: Severity;
  required: boolean;
};
export type RulesDoc = {
  _note: string;
  version: string;
  decision_thresholds: Record<string, number | boolean>;
  scoring: { weights: Record<Severity, number>; low_confidence_multiplier: number; low_confidence_below: number };
  rules: Rule[];
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `خطأ ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : msg;
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export type DeedField = {
  key: string;
  section: string;
  label: string;
  value: string;
  confidence: number | null;
  line_ids: string[];
  quoted: boolean;
  status: "auto" | "edited" | "confirmed" | "skipped";
  low: boolean;
};
export type DeedData = {
  fields: DeedField[];
  keys: { key: string; section: string; label: string }[];
  sections: { id: string; label: string }[];
  extracted: boolean;
};

export type SearchHit = {
  contract_id: string;
  kind: "clause" | "key_field";
  ref_id: string;
  page: number | null;
  text: string;
  score: number;
  file_name: string;
  title: string | null;
};

export type Health = {
  ok: boolean;
  mock_mode: boolean;
  economy_mode: boolean;
  spent_usd: number;
  budget_usd: number;
  ocr_engine: string;
  gemini_key_set: boolean;
  gemini_spent_usd: number;
  gemini_budget_usd: number;
  gemini_model: string;
  gemini_free_tier: boolean;
  segmenter?: "kraken" | "classic";
};

export const api = {
  health: () =>
    fetch("/api/health").then((r) =>
      json<Health>(r),
    ),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch("/api/contracts", { method: "POST", body: fd }).then((r) => json<{ id: string }>(r));
  },
  samples: () => fetch("/api/samples").then((r) => json<{ key: string; file_name: string; label_ar: string }[]>(r)),
  runSample: (key: string) => fetch(`/api/samples/${key}`, { method: "POST" }).then((r) => json<{ id: string }>(r)),
  list: () => fetch("/api/contracts").then((r) => json<ContractRow[]>(r)),
  rerun: (id: string) => fetch(`/api/contracts/${id}/rerun`, { method: "POST" }).then((r) => json<{ id: string }>(r)),
  get: (id: string) => fetch(`/api/contracts/${id}`).then((r) => json<ContractDetail>(r)),
  queue: () =>
    fetch("/api/review-queue").then((r) =>
      json<(ContractRow & { decision_reasons: string[]; reviewer_summary_ar: string })[]>(r),
    ),
  review: (id: string, body: { decision: string; notes: string; reviewer: string }) =>
    fetch(`/api/contracts/${id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<{ ok: boolean }>(r)),
  rules: () => fetch("/api/rules").then((r) => json<RulesDoc>(r)),
  deed: (id: string) => fetch(`/api/contracts/${id}/deed`).then((r) => json<DeedData>(r)),
  deedEdit: (id: string, body: { key: string; status: DeedField["status"]; value?: string }) =>
    fetch(`/api/contracts/${id}/deed`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<{ ok: boolean }>(r)),
  search: (q: string, k = 10) =>
    fetch(`/api/search?${new URLSearchParams({ q, k: String(k) })}`).then((r) => json<SearchHit[]>(r)),
};

const AR_DIGITS = "٠١٢٣٤٥٦٧٨٩";
export const toAr = (v: string | number | null | undefined): string =>
  v === null || v === undefined ? "-" : String(v).replace(/[0-9]/g, (d) => AR_DIGITS[Number(d)]);
// Counted nouns (العدد والمعدود): one, two, plural (3-10), accusative singular (11-99), genitive (100s).
const COUNTED = {
  page: ["صفحة واحدة", "صفحتان", "صفحات", "صفحة", "صفحة"],
  enhanced_page: ["صفحة واحدة محسّنة", "صفحتان محسّنتان", "صفحات محسّنة", "صفحة محسّنة", "صفحة محسّنة"],
  line: ["سطر واحد", "سطران", "أسطر", "سطراً", "سطر"],
  clause: ["بند واحد", "بندان", "بنود", "بنداً", "بند"],
  stamp: ["ختم واحد", "ختمان", "أختام", "ختماً", "ختم"],
  segment: ["مقطع واحد", "مقطعان", "مقاطع", "مقطعاً", "مقطع"],
  field: ["حقل واحد", "حقلان", "حقول", "حقلاً", "حقل"],
  filled_field: ["حقل واحد معبّأ", "حقلان معبّآن", "حقول معبّأة", "حقلاً معبّأً", "حقل معبّأ"],
  low_field: ["حقل واحد منخفض الدقة", "حقلان منخفضا الدقة", "حقول منخفضة الدقة", "حقلاً منخفض الدقة", "حقل منخفض الدقة"],
  skipped_field: ["حقل واحد مُتخطّى", "حقلان مُتخطّيان", "حقول مُتخطّاة", "حقلاً مُتخطّى", "حقل مُتخطّى"],
  party: ["طرف واحد", "طرفان", "أطراف", "طرفاً", "طرف"],
  date: ["تاريخ واحد", "تاريخان", "تواريخ", "تاريخاً", "تاريخ"],
  token: ["رمز واحد", "رمزان", "رموز", "رمزاً", "رمز"],
} as const;

/** "٥ مقاطع", "٢٣ سطراً", "سطران", "ختم واحد", "١٠٠ صفحة". Same rules as count_ar on the server. */
export const countAr = (n: number, noun: keyof typeof COUNTED, digits?: string): string => {
  const [one, two, plural, acc, gen] = COUNTED[noun];
  if (n === 1) return one;
  if (n === 2) return two;
  const rest = n % 100;
  const word = n === 0 || (rest >= 3 && rest <= 10) ? plural : rest === 0 || rest === 1 || rest === 2 ? gen : acc;
  return `${toAr(digits ?? n)} ${word}`;
};

export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "-" : toAr(Math.round(v * 100)) + "٪");

export const STATUS_AR: Record<string, { label: string; tone: "success" | "warning" | "danger" | "info" | "muted" }> = {
  processing: { label: "قيد المعالجة", tone: "info" },
  approved: { label: "معتمد تلقائياً", tone: "success" },
  awaiting_review: { label: "بانتظار المراجع", tone: "warning" },
  reviewing: { label: "قيد اعتماد المراجع", tone: "info" },
  approved_by_reviewer: { label: "معتمد من المراجع", tone: "success" },
  rejected: { label: "مرفوض", tone: "danger" },
  changes_requested: { label: "مطلوب تعديلات", tone: "warning" },
  error: { label: "خطأ", tone: "danger" },
};

export const SEVERITY_AR: Record<Severity, string> = { critical: "حرجة", high: "عالية", medium: "متوسطة", low: "منخفضة" };
export const RESULT_AR: Record<string, string> = { pass: "مطابق", fail: "مخالف", missing: "مفقود", not_applicable: "لا ينطبق" };
export const CLAUSE_TYPE_AR: Record<string, string> = {
  parties: "الأطراف",
  subject: "موضوع العقد",
  term: "مدة العقد",
  payment: "المقابل المالي والدفع",
  termination: "الإنهاء",
  liability: "المسؤولية والتعويض",
  confidentiality: "السرية",
  data_protection: "حماية البيانات",
  governing_law: "النظام الواجب التطبيق",
  disputes: "تسوية المنازعات",
  penalties: "الغرامات والجزاءات",
  force_majeure: "القوة القاهرة",
  ip: "الملكية الفكرية",
  assignment: "التنازل",
  notices: "الإشعارات",
  other: "أخرى",
};
export const DOC_TYPE_AR: Record<string, string> = {
  service_agreement: "عقد خدمات",
  supply_contract: "عقد توريد",
  lease: "عقد إيجار",
  employment: "عقد عمل",
  nda: "اتفاقية عدم إفصاح",
  partnership: "عقد شراكة",
  government_procurement: "مشتريات حكومية",
  other_contract: "عقد آخر",
  property_deed: "صك ملكية",
  court_record: "وثيقة قضائية",
  not_a_contract: "ليس عقداً",
};

"""Shared graph state and the Pydantic models that double as Claude structured-output schemas.

State values are stored as plain dicts (model_dump) so the checkpointer can serialize them;
every agent validates its output through these models before writing to state.
"""
from __future__ import annotations

from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

# ---------- Intake ----------

DocType = Literal[
    "service_agreement", "supply_contract", "lease", "employment", "nda",
    "partnership", "government_procurement", "other_contract", "property_deed", "court_record",
    "not_a_contract",
]

# Documents we digitize but do not run through the contract policy agents.
NON_CONTRACT_DOC_TYPES = {"property_deed", "court_record"}


class IntakeLLM(BaseModel):
    """What the Intake agent returns."""
    is_scanned: bool
    quality_score: float
    doc_type: DocType
    language: Literal["ar", "en", "mixed"]
    page_count_seen: int
    readable: bool
    issues_ar: list[str]


class IntakeResult(IntakeLLM):
    page_count: int = 0
    has_text_layer: bool = False
    file_type: str = ""


# ---------- Digitization (one page) ----------

BlockKind = Literal["title", "preamble", "clause", "subclause", "paragraph", "table",
                    "signature_area", "other"]


class Party(BaseModel):
    role_ar: str
    name: str
    id_number: Optional[str] = None


class Block(BaseModel):
    id: str
    kind: BlockKind
    number_raw: Optional[str] = None
    heading: Optional[str] = None
    text: str
    continues_from_previous_page: bool = False
    continues_to_next_page: bool = False
    confidence: float
    bbox: list[float] = Field(description="[x0, y0, x1, y1] normalized 0 to 1 on the page image")
    # Set by the local OCR path (one value per text line); not part of the Claude schema.
    line_confidences: SkipJsonSchema[list[float]] = []


class TableData(BaseModel):
    id: str
    rows: list[list[str]]
    bbox: list[float]
    confidence: float


class DateRef(BaseModel):
    raw: str
    calendar: Literal["hijri", "gregorian", "unknown"]
    block_id: Optional[str] = None


class Stamp(BaseModel):
    bbox: list[float]
    kind: Literal["stamp", "seal", "signature", "fingerprint"]
    text_if_any: Optional[str] = None
    overlaps_text: bool = False


class KeyField(BaseModel):
    """A labelled data point, e.g. deed number, owner, area, boundary length."""
    section: Optional[str] = None
    label: str
    value: str
    block_id: Optional[str] = None
    confidence: float


class PageDigitization(BaseModel):
    """What the Digitization agent returns for one page."""
    title: Optional[str] = None
    parties: list[Party]
    key_fields: list[KeyField]
    blocks: list[Block]
    tables: list[TableData]
    dates: list[DateRef]
    stamps: list[Stamp]
    page_header_footer: list[str]


# ---------- Structuring of local OCR lines (Claude groups lines by ID, never rewrites) ----------

class StructBlock(BaseModel):
    line_ids: list[str]
    kind: BlockKind
    number_raw: Optional[str] = None
    heading: Optional[str] = None


class StructParty(BaseModel):
    role_ar: str
    name: str
    id_number: Optional[str] = None
    line_id: str


class StructKeyField(BaseModel):
    section: Optional[str] = None
    label: str
    value: str
    line_id: str


class StructDate(BaseModel):
    raw: str
    calendar: Literal["hijri", "gregorian", "unknown"]
    line_id: str


class StructuringOutput(BaseModel):
    title_line_id: Optional[str] = None
    blocks: list[StructBlock]
    parties: list[StructParty]
    key_fields: list[StructKeyField]
    dates: list[StructDate]


# ---------- Merged digital contract (built in code) ----------

class PageBlock(Block):
    page: int
    normalized: str = ""


class PageStamp(Stamp):
    id: str
    page: int
    near_block_id: Optional[str] = None


class PageTable(TableData):
    page: int


class ParsedDate(DateRef):
    iso: Optional[str] = None


class ClauseRegion(BaseModel):
    page: int
    bbox: list[float]


class Clause(BaseModel):
    clause_id: str
    number_raw: Optional[str] = None
    heading: Optional[str] = None
    text: str
    normalized: str = ""
    block_ids: list[str]
    pages: list[int]
    regions: list[ClauseRegion]
    confidence: float


class DigitalContract(BaseModel):
    ocr_engine: str = ""
    title: Optional[str] = None
    parties: list[Party]
    key_fields: list[KeyField] = []
    clauses: list[Clause]
    blocks: list[PageBlock]
    tables: list[PageTable]
    dates: list[ParsedDate]
    stamps: list[PageStamp]
    headers_footers: list[str]


class OcrConfidence(BaseModel):
    min: float
    mean: float
    low_conf_block_ids: list[str]


class Outputs(BaseModel):
    docx_path: Optional[str] = None
    pdf_path: Optional[str] = None
    report_path: Optional[str] = None
    warnings: list[str] = []


# ---------- Review ----------

ClauseType = Literal[
    "parties", "subject", "term", "payment", "termination", "liability", "confidentiality",
    "data_protection", "governing_law", "disputes", "penalties", "force_majeure", "ip",
    "assignment", "notices", "other",
]


class ClauseTyping(BaseModel):
    clause_id: str
    type: ClauseType
    secondary_types: list[ClauseType]
    confidence: float


class ClauseOutput(BaseModel):
    clause_types: list[ClauseTyping]
    missing_types: list[ClauseType]


Severity = Literal["critical", "high", "medium", "low"]


class PolicyResult(BaseModel):
    rule_id: str
    clause_id: Optional[str] = None
    status: Literal["pass", "fail", "missing", "not_applicable"]
    severity: Severity
    evidence_ar: str
    explanation_ar: str
    confidence: float


class PolicyOutput(BaseModel):
    policy_results: list[PolicyResult]


class ScoreItem(BaseModel):
    rule_id: str
    severity: Severity
    points: float


class Redline(BaseModel):
    clause_id: Optional[str] = None
    rule_id: str
    severity: Severity
    issue_ar: str
    original_ar: str
    suggested_ar: str
    rationale_ar: str


class RedlineOutput(BaseModel):
    risk_score: int
    score_breakdown: list[ScoreItem]
    redlines: list[Redline]


class DecisionSummary(BaseModel):
    reviewer_summary_ar: str


class HumanReview(BaseModel):
    decision: Literal["approve", "reject", "request_changes"]
    notes: str = ""
    reviewer: str = "المراجع"
    timestamp: str


# ---------- Graph state ----------

class ContractState(TypedDict, total=False):
    contract_id: str
    file_path: str
    file_name: str
    page_images: list[str]
    text_layer: list[str]
    intake: dict                 # IntakeResult
    digital: dict                # DigitalContract
    ocr_pages: list[dict]        # per page: skew, line boxes, stamps, OCR lines (for the UI)
    deed_fields: list[dict]      # deeds: template fields filled from the OCR lines (deed.py)
    ocr_confidence: dict         # OcrConfidence
    outputs: dict                # Outputs
    clause_types: list[dict]     # ClauseTyping
    missing_types: list[str]
    policy_results: list[dict]   # PolicyResult
    redlines: list[dict]         # Redline
    score_breakdown: list[dict]  # ScoreItem
    risk_score: int
    decision: Literal["approve", "escalate"]
    decision_reasons: list[str]
    reviewer_summary_ar: str
    human_review: Optional[dict]  # HumanReview
    final_status: str
    errors: list[str]

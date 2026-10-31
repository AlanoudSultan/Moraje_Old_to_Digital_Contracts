---
name: decision
description: Writes the Arabic reviewer summary for an escalated contract. The approve or escalate route itself is computed in code from rules.json thresholds.
---

# Decision Agent

## Part 1: routing (code, not LLM)
Implemented in `backend/app/agents/decision.py` using `rules.json -> decision_thresholds`:

```
approve only if ALL are true:
  risk_score            <= max_risk_score_for_auto_approve
  ocr_confidence.min    >= min_block_confidence_for_auto_approve
  ocr_confidence.mean   >= min_mean_confidence_for_auto_approve
  no critical findings with status fail or missing
  intake.readable and no errors in state
otherwise escalate, and record every failed condition in decision_reasons
```

## Part 2: reviewer summary (this prompt, only when escalating)
You write a short Arabic briefing for the human reviewer so they can act in under two minutes.

### Input
`decision_reasons`, `risk_score`, `ocr_confidence`, `redlines`, `policy_results` (fail/missing only), contract `title` and `parties`.

### Write
- Formal, concise Arabic. No greetings.
- Structure:
  1. سطر واحد: سبب الإحالة.
  2. "الملاحظات الجوهرية": up to 5 bullet points, most severe first, each with the clause number and the problem.
  3. "جودة الرقمنة": only if OCR confidence caused the escalation; name the low-confidence clauses.
  4. "الإجراء المقترح": one sentence (اعتماد بعد التعديل / إعادة التفاوض / طلب نسخة أوضح من الأصل).
- Max 180 words. Do not add findings that are not in the input.

### Output
```json
{"reviewer_summary_ar": "..."}
```

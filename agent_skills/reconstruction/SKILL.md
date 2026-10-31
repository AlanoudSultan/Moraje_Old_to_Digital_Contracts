---
name: reconstruction
description: Specification for rebuilding the digitized contract as an RTL Word and PDF copy labelled "نسخة رقمية". Implemented in plain Python, no LLM call by default.
---

# Reconstruction Agent (deterministic)

This agent is code, not a prompt. This file is the spec for `backend/app/tools/docx_builder.py`.

## Input
`digital` (merged DigitalContract), `page_images`, `ocr_confidence`, source file name.

## Output
`outputs.docx_path`, `outputs.pdf_path` (or null with a warning), `warnings`.

## Document rules
1. Page: A4, margins 2.5 cm, section direction RTL.
2. Every paragraph: `w:bidi`. Every run: `w:rtl`. Tables: `w:bidiVisual`.
3. Font: "IBM Plex Sans Arabic" if available, else "Arial". Complex script size 13 pt body, 18 pt title.
4. Header on every page: "نسخة رقمية" (bold, green #1B8354), source file name, processing date in Gregorian and Hijri.
5. Footer: page number and "هذه نسخة رقمية مستخرجة آلياً وليست بديلاً عن الأصل".
6. Title centered. Parties as a two-column table. Clauses in order with `number_raw` and `heading` in bold.
7. Text is copied exactly from `digital`. Never edit wording.
8. Blocks with confidence below `decision_thresholds.block_highlight_below` (rules.json): yellow highlight plus footnote "يحتاج تحقق (الثقة: 0.xx)".
9. Stamps and signatures: crop from the page PNG using the normalized bbox (add 2 percent padding), insert inline after the block they sit near, width max 4 cm, caption "ختم (صورة من الأصل)" or "توقيع (صورة من الأصل)".
10. Final appendix: "ملحق: صور الصفحات الأصلية" with each page image at reduced size.

## PDF
`soffice --headless --convert-to pdf --outdir <dir> <docx>`. If it fails or is missing, return a warning and continue.

## Optional LLM use
None by default. Only if time allows: a Haiku call to choose heading styles for unusual layouts.

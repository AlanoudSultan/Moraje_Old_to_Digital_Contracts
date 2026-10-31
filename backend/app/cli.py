"""Run the pipeline from the command line.

    .venv/bin/python -m backend.app.cli samples/old_scanned_lease.jpg
    .venv/bin/python -m backend.app.cli --all-samples
"""
from __future__ import annotations

import argparse
import shutil
import uuid
from pathlib import Path

from . import config, db, graph

SAMPLE_FILES = ["clean_service_agreement.pdf", "risky_supply_contract.pdf", "old_scanned_lease.jpg"]


def run_file(path: Path) -> dict:
    cid = "cli-" + uuid.uuid4().hex[:8]
    folder = config.CONTRACTS_DIR / cid
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / ("source" + path.suffix.lower())
    shutil.copyfile(path, dest)
    db.create_contract(cid, path.name, config.mock_mode())
    state = graph.run_pipeline(cid, str(dest), path.name)
    usage = db.usage_summary(cid)
    ocr = state.get("ocr_confidence") or {}
    return {"file": path.name, "id": cid, "decision": state.get("decision"), "risk_score": state.get("risk_score"),
            "ocr_min": ocr.get("min"), "tokens": usage["total_tokens"], "cost_usd": usage["total_cost_usd"],
            "reasons": state.get("decision_reasons", []), "errors": state.get("errors", []),
            "outputs": state.get("outputs", {})}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all-samples", action="store_true")
    args = ap.parse_args()
    files = [Path(f) for f in args.files]
    if args.all_samples:
        files += [config.SAMPLES_DIR / f for f in SAMPLE_FILES]
    print(f"mock_mode={config.mock_mode()}")
    rows = [run_file(f) for f in files]
    print("\n| File | Decision | Risk | Min OCR conf | Tokens | Cost (USD) |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['file']} | {r['decision']} | {r['risk_score']} | {r['ocr_min']} | {r['tokens']} | {r['cost_usd']} |")
    for r in rows:
        print(f"\n{r['file']} ({r['id']}):")
        for reason in r["reasons"]:
            print("  -", reason)
        for e in r["errors"]:
            print("  ERROR", e)
        print("  outputs:", {k: v for k, v in r["outputs"].items() if k != "warnings"})


if __name__ == "__main__":
    main()

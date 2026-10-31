import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

# Isolated data dir and offline mode before any app import.
_TMP = tempfile.mkdtemp(prefix="contract-review-tests-")
os.environ["DATA_DIR"] = _TMP
os.environ["MOCK_LLM"] = "1"
os.environ["OCR_ENGINE"] = "mock"
os.environ["ENHANCE_IMAGES"] = "0"
os.environ["EMBEDDINGS_ENABLED"] = "0"
os.environ["SEGMENTER"] = "classic"  # tests never start the Kraken worker; its path is tested with a fake
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from backend.app import config, db  # noqa: E402


@pytest.fixture
def new_contract():
    """Copy a sample into the data dir and register it. Returns (contract_id, path, name)."""

    def make(sample: str):
        cid = "t-" + uuid.uuid4().hex[:8]
        folder = config.CONTRACTS_DIR / cid
        folder.mkdir(parents=True, exist_ok=True)
        src = config.SAMPLES_DIR / sample
        dest = folder / ("source" + src.suffix)
        shutil.copyfile(src, dest)
        db.create_contract(cid, sample, True)
        return cid, str(dest), sample

    return make

.PHONY: install kraken models dev backend frontend build test samples run-samples

install:
	python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
	npm --prefix frontend install
	$(MAKE) kraken

# Kraken line segmentation in its own environment (~1.2 GB): it needs older PyTorch than the app.
# Without it the app falls back to the classical segmentation.
kraken:
	python3 -m venv .venv-kraken && .venv-kraken/bin/pip install -r backend/requirements-kraken.txt
	.venv-kraken/bin/pip install scipy==1.17.1  # Kraken's pinned 1.15.3 does not load on current macOS

# Download the local model weights (Real-ESRGAN ~72 MB, multilingual-e5-base ~1.1 GB). Gemini OCR runs in the cloud.
models:
	.venv/bin/python -c "from backend.app.tools.esrgan.enhance import weights_path as w; [print(w(n)) for n in ('x4plus', 'general-x4v3')]"
	.venv/bin/python -c "from huggingface_hub import snapshot_download as d; print(d('intfloat/multilingual-e5-base', allow_patterns=['*.json','model.safetensors','sentencepiece.bpe.model','tokenizer*']))"

# Backend on :8000 and Vite dev server on :5173 (proxying /api).
dev:
	$(MAKE) -j2 backend frontend

backend:
	.venv/bin/uvicorn backend.app.main:app --reload --port 8000

frontend:
	npm --prefix frontend run dev

# Single port: build the UI once, FastAPI serves it on :8000.
build:
	npm --prefix frontend run build

test:
	.venv/bin/python -m pytest backend/tests -q

samples:
	.venv/bin/python samples/make_samples.py

run-samples:
	.venv/bin/python -m backend.app.cli --all-samples

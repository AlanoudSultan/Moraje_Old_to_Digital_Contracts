"""Line segmentation worker. Runs in the separate Kraken environment (.venv-kraken), never imported
by the app: Kraken needs older PyTorch, safetensors and click than the app uses.

Protocol (one JSON object per line):
  stdin:  {"path": "/abs/page.png"}
  stdout: @@KRAKEN@@ {"size": [w, h], "lines": [{"baseline": [[x, y], ...], "boundary": [[x, y], ...]}]}
          or @@KRAKEN@@ {"error": "..."}
Coordinates are in pixels of the given image, lines in reading order (right to left, top to bottom).
The model loads once, then the worker serves pages until stdin closes. Anything else that reaches
stdout (library chatter) lacks the marker and is ignored by the client.
"""
import json
import logging
import sys
import warnings

MARK = "@@KRAKEN@@ "


def main() -> None:
    warnings.filterwarnings("ignore")
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    from PIL import Image
    from kraken.configs import SegmentationInferenceConfig
    from kraken.tasks import SegmentationTaskModel

    model = SegmentationTaskModel.load_model()  # Kraken's bundled default: blla.mlmodel
    cfg = SegmentationInferenceConfig(text_direction="horizontal-rl")
    print(MARK + json.dumps({"ready": True}), flush=True)
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            path = json.loads(raw)["path"]
            with Image.open(path) as im:
                im = im.convert("RGB")
                seg = model.predict(im, cfg)
                size = list(im.size)
            lines = [{"baseline": [list(p) for p in (l.baseline or [])], "boundary": [list(p) for p in l.boundary]}
                     for l in seg.lines if getattr(l, "boundary", None)]
            out = {"size": size, "lines": lines}
        except Exception as exc:  # report and keep serving
            out = {"error": f"{type(exc).__name__}: {exc}"}
        print(MARK + json.dumps(out), flush=True)


if __name__ == "__main__":
    main()

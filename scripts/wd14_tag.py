"""WD14 tagger — booru-tag captioning for a dataset folder.

Runs inside the trainer venv (it has numpy/PIL/huggingface_hub; we add onnxruntime).
Loads a SmilingWolf WD14 ONNX model, tags every PNG, writes a `.txt` next to each,
and prints one JSON event per image so the Loom server can stream progress + live
caption updates to the Dataset tab. Output is Illustrious-ready: underscores ->
spaces, parens escaped, character tags first.

Invoked by the server:
  <trainer_python> wd14_tag.py --dataset-dir D --repo SmilingWolf/wd-vit-tagger-v3
"""

import argparse
import csv
import json
import sys
from pathlib import Path


def emit(obj):
    print(json.dumps(obj), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--repo", default="SmilingWolf/wd-vit-tagger-v3")
    ap.add_argument("--general-thresh", type=float, default=0.35)
    ap.add_argument("--character-thresh", type=float, default=0.85)
    args = ap.parse_args()

    try:
        import numpy as np
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        emit({"type": "fatal", "error": f"missing dependency in trainer venv: {exc}"})
        sys.exit(2)

    emit({"type": "log", "line": f"loading {args.repo}…"})
    try:
        model_path = hf_hub_download(args.repo, "model.onnx")
        csv_path = hf_hub_download(args.repo, "selected_tags.csv")
    except Exception as exc:  # noqa: BLE001
        emit({"type": "fatal", "error": f"could not download model: {exc}"})
        sys.exit(2)

    names, cats = [], []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            names.append(row["name"])
            cats.append(int(row["category"]))
    cats = np.array(cats)

    sess = ort.InferenceSession(
        model_path, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    size = inp.shape[1] if isinstance(inp.shape[1], int) else 448  # NHWC
    in_name, out_name = inp.name, sess.get_outputs()[0].name
    emit({"type": "log", "line": f"providers: {sess.get_providers()} · input {size}px"})

    def preprocess(img):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.alpha_composite(img)
        img = bg.convert("RGB")
        w, h = img.size
        s = max(w, h)
        square = Image.new("RGB", (s, s), (255, 255, 255))
        square.paste(img, ((s - w) // 2, (s - h) // 2))
        square = square.resize((size, size), Image.Resampling.BICUBIC)
        arr = np.asarray(square, dtype=np.float32)[:, :, ::-1]  # RGB -> BGR
        return arr[None, ...]

    def fmt(name):
        return name.replace("_", " ").replace("(", "\\(").replace(")", "\\)")

    pngs = sorted(Path(args.dataset_dir).glob("*.png"))
    total = len(pngs)
    for i, png in enumerate(pngs):
        try:
            probs = sess.run([out_name], {in_name: preprocess(Image.open(png))})[0][0]
            chars, general = [], []
            for j, p in enumerate(probs):
                if cats[j] == 4 and p >= args.character_thresh:
                    chars.append((p, names[j]))
                elif cats[j] == 0 and p >= args.general_thresh:
                    general.append((p, names[j]))
            chars.sort(key=lambda x: -x[0])
            general.sort(key=lambda x: -x[0])
            caption = ", ".join(fmt(n) for _, n in chars + general)
            png.with_suffix(".txt").write_text(caption, encoding="utf-8")
            emit({"type": "caption", "file": png.name, "caption": caption, "index": i, "total": total})
        except Exception as exc:  # noqa: BLE001
            emit({"type": "error", "file": png.name, "index": i, "total": total, "error": str(exc)})
    emit({"type": "done"})


main()

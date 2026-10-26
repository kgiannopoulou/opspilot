"""Assemble docs/media/frames/*.png (recorded by frontend/tests/media/demo.spec.ts) into opspilot-demo.gif.

    python docs/media/build_gif.py            # needs Pillow

Frames carry their own display time (frames.json), so the GIF plays at the pace of the walkthrough (~90 s) while
staying small: one frame per moment instead of 25 per second. A shared palette keeps the dark UI free of banding.
"""

import json
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
WIDTH = 1024  # GitHub renders the README at ~880 px; 1024 stays sharp on high-density screens


def main() -> None:
    manifest = json.loads((HERE / "frames" / "frames.json").read_text(encoding="utf-8"))
    images = []
    for entry in manifest:
        img = Image.open(HERE / "frames" / entry["file"]).convert("RGB")
        img = img.resize((WIDTH, round(img.height * WIDTH / img.width)), Image.Resampling.LANCZOS)
        images.append(img)
    # One adaptive palette from a sample of frames, applied to all: stable colours, no per-frame flicker.
    sample = Image.new("RGB", (WIDTH, images[0].height * 4))
    for n, img in enumerate(images[:: max(1, len(images) // 4)][:4]):
        sample.paste(img, (0, n * img.height))
    palette = sample.quantize(colors=200, method=Image.Quantize.MEDIANCUT)
    frames = [img.quantize(palette=palette, dither=Image.Dither.NONE) for img in images]
    out = HERE / "opspilot-demo.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=[e["ms"] for e in manifest], loop=0,
                   optimize=True, disposal=1)  # fmt: skip
    total = sum(e["ms"] for e in manifest) / 1000
    print(f"{out.name}: {len(frames)} frames, {total:.0f} s, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

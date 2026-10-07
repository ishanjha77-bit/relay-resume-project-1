"""Turn the recorded console demo (DEMO=1 npx playwright test e2e/demo.spec.ts) into
the README's GIF: docs/demo.gif.

Uses imageio-ffmpeg's static ffmpeg (Playwright's own build can only encode
video), with a two-pass palette so a dark UI stays crisp in 256 colours.

    uv run --no-project --with imageio-ffmpeg python scripts/demo_gif.py [video.webm]
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "demo.gif"
# Small enough for a README (GitHub stops inlining images past 10 MB); override to taste.
FPS = int(os.environ.get("GIF_FPS", 8))
WIDTH = int(os.environ.get("GIF_WIDTH", 900))
COLORS = int(os.environ.get("GIF_COLORS", 96))


def newest_video() -> Path:
    videos = sorted((ROOT / "apps/console/test-results").rglob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not videos:
        raise SystemExit("no video: run  DEMO=1 npx playwright test e2e/demo.spec.ts  in apps/console first")
    return videos[-1]


def main() -> int:
    video = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_video()
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    scale = f"fps={FPS},scale={WIDTH}:-1:flags=lanczos"
    palette = OUT.with_suffix(".palette.png")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            f"{scale},palettegen=max_colors={COLORS}:stats_mode=diff",
            str(palette),
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-i",
            str(palette),
            "-lavfi",
            f"{scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
            str(OUT),
        ],
        check=True,
    )
    palette.unlink(missing_ok=True)
    print(f"{OUT} ({OUT.stat().st_size / 1e6:.1f} MB) from {video}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

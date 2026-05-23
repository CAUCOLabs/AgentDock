#!/usr/bin/env python3
from pathlib import Path
import sys

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
except ModuleNotFoundError:
    print(
        "Pillow is required to generate the Google Play tablet screenshots.\n"
        "Install it locally, then rerun this script:\n\n"
        "  python3 -m pip install Pillow\n"
        "  python3 scripts/generate_play_store_tablet_screenshots.py\n",
        file=sys.stderr,
    )
    sys.exit(1)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "docs" / "screenshots"
OUTPUT_DIR = ROOT / "docs" / "play-store"

JOBS = [
    ("Home_AisoFlow.PNG", "tablet-7-home.png", (1200, 1920), 96),
    ("repository_workflow.PNG", "tablet-7-workflow.png", (1200, 1920), 96),
    ("Home_AisoFlow.PNG", "tablet-10-home.png", (1600, 2560), 128),
    ("repository_workflow.PNG", "tablet-10-workflow.png", (1600, 2560), 128),
]


def make_background(size):
    width, height = size
    top = (7, 14, 26)
    bottom = (11, 24, 42)
    accent = (18, 83, 95)
    image = Image.new("RGB", size, top)
    pixels = image.load()
    cx, cy = width * 0.5, height * 0.18
    max_radius = (width * width + height * height) ** 0.5

    for y in range(height):
        vertical = y / max(height - 1, 1)
        for x in range(width):
            radial = max(0.0, 1.0 - (((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / max_radius) * 2.4)
            base = tuple(round(top[i] * (1 - vertical) + bottom[i] * vertical) for i in range(3))
            pixels[x, y] = tuple(min(255, round(base[i] + accent[i] * radial * 0.28)) for i in range(3))

    return image


def rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def paste_frame(canvas, screenshot, padding):
    target_w, target_h = canvas.size
    max_content = (target_w - padding * 2, target_h - padding * 2)
    resized = ImageOps.contain(screenshot, max_content, Image.Resampling.LANCZOS)

    frame_padding = max(10, target_w // 120)
    frame_size = (resized.width + frame_padding * 2, resized.height + frame_padding * 2)
    frame_pos = ((target_w - frame_size[0]) // 2, (target_h - frame_size[1]) // 2)

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_box = (
        frame_pos[0] + 10,
        frame_pos[1] + 18,
        frame_pos[0] + frame_size[0] + 10,
        frame_pos[1] + frame_size[1] + 18,
    )
    radius = max(24, target_w // 28)
    shadow_draw.rounded_rectangle(shadow_box, radius=radius, fill=(0, 0, 0, 95))
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(18, target_w // 40)))
    canvas.alpha_composite(shadow)

    frame = Image.new("RGBA", frame_size, (12, 22, 36, 255))
    frame_draw = ImageDraw.Draw(frame)
    frame_draw.rounded_rectangle(
        (0, 0, frame_size[0] - 1, frame_size[1] - 1),
        radius=radius,
        fill=(12, 22, 36, 255),
        outline=(48, 74, 94, 255),
        width=max(2, target_w // 400),
    )
    canvas.alpha_composite(frame, frame_pos)

    screenshot_pos = (frame_pos[0] + frame_padding, frame_pos[1] + frame_padding)
    mask = rounded_mask(resized.size, max(18, target_w // 44))
    canvas.paste(resized.convert("RGBA"), screenshot_pos, mask)


def human_size(path):
    size = path.stat().st_size
    return f"{size / (1024 * 1024):.2f} MB"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for source_name, output_name, size, padding in JOBS:
        source = SOURCE_DIR / source_name
        output = OUTPUT_DIR / output_name
        if not source.exists():
            raise FileNotFoundError(f"Missing source screenshot: {source}")

        with Image.open(source) as screenshot:
            screenshot = screenshot.convert("RGB")
            canvas = make_background(size).convert("RGBA")
            paste_frame(canvas, screenshot, padding)
            canvas.convert("RGB").save(output, format="PNG", optimize=True)

        with Image.open(output) as generated:
            results.append((output, generated.size, human_size(output)))

    for output, dimensions, size in results:
        print(f"{output} - {dimensions[0]}x{dimensions[1]} - {size}")


if __name__ == "__main__":
    main()

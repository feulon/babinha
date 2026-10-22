"""Render the Babinha football pitch icons: uv run python scripts/generate_icons.py."""

from pathlib import Path

from PIL import Image, ImageDraw

ICONS = Path(__file__).resolve().parents[1] / "src" / "static" / "icons"
GREEN = "#0f5132"
WHITE = "#ffffff"


def pitch(size: int, *, maskable: bool = False) -> Image.Image:
    """Supersample crisp field markings, keeping maskable art in the safe circle."""
    scale = 4
    image = Image.new("RGB", (512 * scale, 512 * scale), GREEN)
    draw = ImageDraw.Draw(image)
    inset = 120 if maskable else 72
    top = 104 if maskable else 48
    left, right, bottom = inset, 512 - inset, 512 - top
    width = 12

    def line(points):
        draw.line([(x * scale, y * scale) for x, y in points], fill=WHITE, width=width * scale)

    def rectangle(box):
        draw.rectangle(tuple(value * scale for value in box), outline=WHITE, width=width * scale)

    rectangle((left, top, right, bottom))
    line([(left, 256), (right, 256)])
    draw.ellipse(
        (208 * scale, 208 * scale, 304 * scale, 304 * scale), outline=WHITE, width=width * scale
    )
    rectangle((200, top, 312, top + 60))
    rectangle((200, bottom - 60, 312, bottom))
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    for name, size, maskable in (
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
        ("apple-touch-icon.png", 180, False),
        ("favicon.png", 32, False),
    ):
        pitch(size, maskable=maskable).save(ICONS / name, optimize=True)


if __name__ == "__main__":
    main()

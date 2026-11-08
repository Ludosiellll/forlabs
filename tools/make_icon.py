"""Значок ForLabs (packaging/forlabs.ico): лист отчёта на тёмной плитке в цветах установщика."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
S = 1024


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=200, fill=(32, 32, 32, 255), outline=(60, 60, 60, 255),
                        width=12)
    # лист с загнутым углом
    x0, y0, x1, y1, fold = 270, 170, 754, 854, 170
    d.polygon([(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)], fill=(96, 205, 255, 255))
    d.polygon([(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)], fill=(40, 130, 175, 255))
    for i, w in enumerate((330, 330, 230)):
        y = 520 + i * 105
        d.rounded_rectangle((x0 + 85, y, x0 + 85 + w, y + 46), radius=23, fill=(0, 0, 0, 255))
    return img


def main() -> None:
    out = ROOT / "packaging" / "forlabs.ico"
    out.parent.mkdir(exist_ok=True)
    draw().save(out, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128),
                            (256, 256)])
    print(out)


if __name__ == "__main__":
    main()

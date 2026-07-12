from pathlib import Path

from PIL import Image, ImageDraw


def build_icon(size: int = 512) -> Image.Image:
    image = Image.new("RGBA", (size, size), "#0b0d10")
    draw = ImageDraw.Draw(image)
    pad = size // 10
    radius = size // 5
    draw.rounded_rectangle((pad, pad, size - pad, size - pad), radius=radius, fill="#12151a", outline="#303846", width=size // 40)
    width = size // 22
    left = [(size * 0.39, size * 0.30), (size * 0.25, size * 0.50), (size * 0.39, size * 0.70)]
    right = [(size * 0.61, size * 0.30), (size * 0.75, size * 0.50), (size * 0.61, size * 0.70)]
    draw.line(left, fill="#70a5ff", width=width, joint="curve")
    draw.line(right, fill="#a78bfa", width=width, joint="curve")
    draw.line((size * 0.53, size * 0.27, size * 0.47, size * 0.73), fill="#e5e7eb", width=width // 2)
    return image


if __name__ == "__main__":
    output = Path(__file__).parents[1] / "apps" / "desktop" / "src-tauri" / "icons"
    output.mkdir(parents=True, exist_ok=True)
    icon = build_icon()
    icon.save(output / "icon.png")
    icon.save(output / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

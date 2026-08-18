"""生成应用图标 assets/icon.ico（紫蓝渐变圆角方块 + 白色下载箭头，与 favicon 同款设计）。"""
from pathlib import Path

from PIL import Image, ImageDraw


def _gradient(size: int, top, bottom) -> Image.Image:
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (size, y)], fill=c)
    return img


def make(size: int) -> Image.Image:
    TOP, BOTTOM = (139, 92, 246), (59, 130, 246)  # #8B5CF6 → #3B82F6
    s = size * 4  # 超采样抗锯齿
    base = _gradient(s, TOP, BOTTOM)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=255)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(base, (0, 0), mask)
    d = ImageDraw.Draw(img)
    w = max(2, int(s * 0.075))  # 线宽
    # 竖线 + 箭头两翼 + 底部托盘
    cx, top_y, bot_y = s / 2, s * 0.26, s * 0.52
    d.line([(cx, top_y), (cx, bot_y)], fill="white", width=w)
    for dx in (-1, 1):
        d.line([(cx + dx * s * 0.14, bot_y - s * 0.13), (cx, bot_y)], fill="white", width=w)
    ty = s * 0.68
    d.line([(s * 0.28, ty), (s * 0.72, ty)], fill="white", width=w)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    out = Path("assets")
    out.mkdir(exist_ok=True)
    make(256).save(out / "icon.ico",
                   sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("已生成 assets/icon.ico")

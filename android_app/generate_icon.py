# -*- coding: utf-8 -*-
"""
Endüstriyel APK İkonu Üretici (Braun / Teenage Engineering / Nothing OS Tarzı)
4x Supersampling (2048x2048 -> 512x512 ve mipmap boyutları)
"""
import os
import math
from PIL import Image, ImageDraw

def create_industrial_icon():
    # Süper örnekleme boyutu: 2048x2048
    SIZE = 2048
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. ARKA PLAN: Mat Koyu Grafit/Karbon Squircle (#0E0E10)
    margin = 72
    bg_box = [margin, margin, SIZE - margin, SIZE - margin]
    bg_radius = 440
    bg_color = (14, 14, 16, 255)  # #0E0E10
    draw.rounded_rectangle(bg_box, radius=bg_radius, fill=bg_color)

    # İnce mekanik iç çerçeve (#242429) - 2048 ölçeğinde 6px (~512'de 1.5px)
    inner_pad = 36
    frame_box = [
        margin + inner_pad,
        margin + inner_pad,
        SIZE - margin - inner_pad,
        SIZE - margin - inner_pad
    ]
    frame_color = (36, 36, 41, 255)  # #242429
    draw.rounded_rectangle(frame_box, radius=bg_radius - inner_pad, outline=frame_color, width=6)

    # 2. MERKEZ GÖVDE: Fiziksel Donanım Tuşu (Tactile Keycap) (#18181C)
    cx, cy = SIZE // 2, SIZE // 2
    key_half = 640  # 1280x1280 tuş boyutu
    key_box = [cx - key_half, cy - key_half, cx + key_half, cy + key_half]
    key_radius = 300
    key_bg = (24, 24, 28, 255)  # #18181C
    key_border = (46, 46, 52, 255)  # #2E2E34
    draw.rounded_rectangle(key_box, radius=key_radius, fill=key_bg, outline=key_border, width=8)

    # Tuş yüzeyinde hafif mekanik iç çizgi
    key_inner_pad = 14
    key_inner_box = [
        key_box[0] + key_inner_pad,
        key_box[1] + key_inner_pad,
        key_box[2] - key_inner_pad,
        key_box[3] - key_inner_pad
    ]
    draw.rounded_rectangle(key_inner_box, radius=key_radius - key_inner_pad, outline=(34, 34, 40, 255), width=4)

    # 3. İMZA DETAYI: Durum Noktası (Solid LED Dot - Braun / Teenage Engineering dokunuşu)
    # Tuşun sağ üst köşesinde, net ve solid endüstriyel turuncu-kırmızı (#FF4500)
    led_cx = key_box[2] - 170
    led_cy = key_box[1] + 170
    led_radius = 42

    # Mat koyu montaj soketi halkası (#141416 zemin, ince #32323A kenarlık)
    socket_pad = 14
    draw.ellipse(
        [led_cx - (led_radius + socket_pad), led_cy - (led_radius + socket_pad),
         led_cx + (led_radius + socket_pad), led_cy + (led_radius + socket_pad)],
        fill=(20, 20, 23, 255),
        outline=(50, 50, 58, 255),
        width=4
    )
    # Tamamen solid, sıfır glow, keskin endüstriyel turuncu-kırmızı LED (#FF4500)
    led_color = (255, 69, 0, 255)
    draw.ellipse(
        [led_cx - led_radius, led_cy - led_radius, led_cx + led_radius, led_cy + led_radius],
        fill=led_color
    )

    # 4. IEC GÜÇ SEMBOLÜ: Mat Beyaz (#F4F4F5)
    symbol_color = (244, 244, 245, 255)  # #F4F4F5
    stroke_w = 88  # Kalın ve tok stroke
    r_center = 340  # Halka merkez ekseni yarıçapı

    arc_box = [
        cx - (r_center + stroke_w / 2.0),
        cy - (r_center + stroke_w / 2.0),
        cx + (r_center + stroke_w / 2.0),
        cy + (r_center + stroke_w / 2.0)
    ]

    draw.arc(arc_box, start=305, end=235, fill=symbol_color, width=stroke_w)

    cap_r = stroke_w / 2.0
    for deg in [305, 235]:
        rad = math.radians(deg)
        cap_cx = cx + r_center * math.cos(rad)
        cap_cy = cy + r_center * math.sin(rad)
        draw.ellipse([cap_cx - cap_r, cap_cy - cap_r, cap_cx + cap_r, cap_cy + cap_r], fill=symbol_color)

    line_w = stroke_w
    line_top = cy - r_center - 80
    line_bottom = cy + 40
    line_box = [cx - line_w / 2.0, line_top, cx + line_w / 2.0, line_bottom]
    draw.rounded_rectangle(line_box, radius=line_w / 2.0, fill=symbol_color)

    return img

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    targets = [
        (os.path.join(base_dir, "res", "drawable", "ic_launcher.png"), 512),
        (os.path.join(base_dir, "res", "mipmap-mdpi", "ic_launcher.png"), 48),
        (os.path.join(base_dir, "res", "mipmap-hdpi", "ic_launcher.png"), 72),
        (os.path.join(base_dir, "res", "mipmap-xhdpi", "ic_launcher.png"), 96),
        (os.path.join(base_dir, "res", "mipmap-xxhdpi", "ic_launcher.png"), 144),
        (os.path.join(base_dir, "res", "mipmap-xxxhdpi", "ic_launcher.png"), 192),
    ]

    print("2048x2048 Süper Örnekleme ile İkon Çiziliyor...")
    icon_master = create_industrial_icon()

    for path, size in targets:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        resized = icon_master.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(path, format="PNG")
        print(f" -> Kaydedildi ({size}x{size}): {path}")

    # Geçici önizleme dosyasını temizle
    preview_file = os.path.join(base_dir, "res", "drawable", "test_icon_preview.png")
    if os.path.exists(preview_file):
        os.remove(preview_file)

    print("\nTüm ikonlar başarıyla üretildi ve yerleştirildi!")

if __name__ == "__main__":
    main()

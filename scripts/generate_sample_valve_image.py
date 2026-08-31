"""
scripts/generate_sample_valve_image.py
---------------------------------------
Generates a clear synthetic industrial equipment inspection image with:
- Equipment tag: MOV-4102-B
- Unit: Crude Distillation Unit (CDU-1) Bottoms Line
- Visual anomaly: Flange face localized pitting corrosion & gasket seepage
- Stamped Rating: ANSI Class 300 / 51 bar
Saved to data/uploads/images/corroded_valve_sample.png for Use Case 2 demo.
"""

import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = PROJECT_ROOT / "data" / "uploads" / "images"
TARGET_DIR.mkdir(parents=True, exist_ok=True)
TARGET_FILE = TARGET_DIR / "corroded_valve_sample.png"

# Canvas dimensions
WIDTH, HEIGHT = 900, 600

# Background: Industrial inspection dark background
img = Image.new("RGB", (WIDTH, HEIGHT), color=(22, 28, 38))
draw = ImageDraw.Draw(img)

# Try loading standard font or fallback
try:
    font_large = ImageFont.truetype("arial.ttf", 26)
    font_medium = ImageFont.truetype("arial.ttf", 18)
    font_small = ImageFont.truetype("arial.ttf", 14)
except Exception:
    font_large = ImageFont.load_default()
    font_medium = ImageFont.load_default()
    font_small = ImageFont.load_default()

# Header banner
draw.rectangle([(0, 0), (WIDTH, 60)], fill=(15, 20, 28))
draw.text((20, 16), "DIGITAL INDUSTRIAL INSPECTION CAMERA — UNIT CDU-1", fill=(56, 189, 248), font=font_large)

# Stamped Tag Nameplate Box
draw.rectangle([(50, 90), (380, 200)], outline=(148, 163, 184), width=2, fill=(30, 41, 59))
draw.text((65, 102), "EQUIPMENT TAG: MOV-4102-B", fill=(255, 255, 255), font=font_medium)
draw.text((65, 130), "SERVICE: CDU ATMOSPHERIC BOTTOMS", fill=(203, 213, 225), font=font_small)
draw.text((65, 150), "SPEC: API 600 GATE / ANSI CL-300", fill=(203, 213, 225), font=font_small)
draw.text((65, 170), "DESIGN PRESS: 51.0 BAR @ 38°C", fill=(203, 213, 225), font=font_small)

# Valve & Flange Schematic Graphic
center_x, center_y = 580, 320

# Pipe section (horizontal)
draw.rectangle([(center_x - 180, center_y - 45), (center_x + 180, center_y + 45)], fill=(71, 85, 105), outline=(100, 116, 139), width=3)

# Flange 1 (Left)
draw.rectangle([(center_x - 110, center_y - 95), (center_x - 80, center_y + 95)], fill=(100, 116, 139), outline=(148, 163, 184), width=2)
# Flange 2 (Right)
draw.rectangle([(center_x + 80, center_y - 95), (center_x + 110, center_y + 95)], fill=(100, 116, 139), outline=(148, 163, 184), width=2)

# Central Valve Body (Globe/Gate chamber)
draw.ellipse([(center_x - 75, center_y - 75), (center_x + 75, center_y + 75)], fill=(51, 65, 85), outline=(148, 163, 184), width=3)

# Valve Bonnet & Actuator Stem (Vertical)
draw.rectangle([(center_x - 20, center_y - 190), (center_x + 20, center_y - 70)], fill=(71, 85, 105), outline=(148, 163, 184), width=2)
# Motor Actuator Enclosure
draw.rectangle([(center_x - 60, center_y - 250), (center_x + 60, center_y - 190)], fill=(30, 41, 59), outline=(56, 189, 248), width=3)
draw.text((center_x - 45, center_y - 225), "MOV ACTUATOR", fill=(56, 189, 248), font=font_small)

# Pitting Corrosion & Defect Region (Right Flange Interface)
# Bounding box indicator
corrosion_box = [(center_x + 70, center_y - 60), (center_x + 150, center_y + 60)]
draw.rectangle(corrosion_box, outline=(239, 68, 68), width=3)

# Draw simulated pitting spots and rusty rust wash
for offset_x, offset_y, r in [
    (95, -30, 5), (105, -20, 8), (90, -5, 6), (115, 10, 7), (100, 25, 9), (120, 35, 6)
]:
    draw.ellipse(
        [(center_x + offset_x - r, center_y + offset_y - r), (center_x + offset_x + r, center_y + offset_y + r)],
        fill=(180, 83, 9), outline=(120, 53, 15)
    )

# Annotation Pointer
draw.line([(center_x + 150, center_y), (center_x + 220, center_y - 50)], fill=(239, 68, 68), width=2)
draw.rectangle([(center_x + 220, center_y - 80), (WIDTH - 30, center_y - 20)], fill=(69, 10, 10), outline=(239, 68, 68), width=2)
draw.text((center_x + 230, center_y - 72), "ANOMALY DETECTED:", fill=(254, 202, 202), font=font_small)
draw.text((center_x + 230, center_y - 52), "Severe Flange Pitting Corrosion & Salt Cake", fill=(255, 255, 255), font=font_small)

# Footer telemetry
draw.rectangle([(0, HEIGHT - 40), (WIDTH, HEIGHT)], fill=(15, 20, 28))
draw.text((20, HEIGHT - 28), "SOVEREIGN INSPECTION ASSET #9942 | CALIBRATION: ACTIVE | OCR ACCURACY: HIGH", fill=(100, 116, 139), font=font_small)

img.save(TARGET_FILE)
print(f"Generated synthetic valve inspection image at: {TARGET_FILE}")

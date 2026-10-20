"""Generate original light-glass / cobalt Windows ICO and tray PNG.

Uses Pillow during CI; source vectors are drawn here rather than embedding
a borrowed proprietary brand icon.
"""
from PIL import Image, ImageDraw, ImageFilter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "assets"
DEST.mkdir(exist_ok=True)

S = 512
im = Image.new("RGBA",(S,S),(0,0,0,0))
shadow = Image.new("RGBA",(S,S),(0,0,0,0))
d = ImageDraw.Draw(shadow)
d.rounded_rectangle((55,58,459,464),radius=118,fill=(46,79,155,58))
shadow = shadow.filter(ImageFilter.GaussianBlur(16))
im.alpha_composite(shadow)
d = ImageDraw.Draw(im)
d.rounded_rectangle((43,43,467,467),radius=115,fill=(242,247,255,255),outline=(255,255,255,250),width=12)
d.rounded_rectangle((78,78,432,432),radius=92,fill=(79,124,255,255))
# White radar geometry: compact, readable at 24 px
d.arc((148,149,360,361),200,335,fill=(239,245,255,240),width=25)
d.arc((194,195,315,316),200,335,fill=(239,245,255,230),width=23)
d.ellipse((239,239,274,274),fill=(255,255,255,255))
# Light reflection gives a restrained glass finish
overlay = Image.new("RGBA",(S,S),(0,0,0,0))
od = ImageDraw.Draw(overlay)
od.arc((86,82,426,422),190,285,fill=(255,255,255,72),width=12)
im.alpha_composite(overlay)
im.save(DEST/"void-leads.png")
im.save(DEST/"void-leads.ico",format="ICO",sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("Created",DEST/"void-leads.ico")

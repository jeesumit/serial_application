"""Generate assets/icon.png + icon.ico (needs Pillow): python tools/make_icon.py"""
from PIL import Image, ImageDraw
S = 1024
im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
d.rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=(37, 99, 235, 255))
w = (255, 255, 255, 255)
d.rounded_rectangle((330, 200, 400, 440), radius=30, fill=w); d.rounded_rectangle((624, 200, 694, 440), radius=30, fill=w)   # prongs
d.rounded_rectangle((250, 420, 774, 640), radius=70, fill=w)
d.rectangle((250, 500, 774, 630), fill=w)                                                              # body
d.pieslice((250, 480, 774, 780), 0, 180, fill=w)                                                                          # rounded base
d.rounded_rectangle((482, 740, 542, 900), radius=28, fill=w)                                                              # cable
pts = [(512, 900), (512, 930), (640, 930), (700, 880)]
d.line(pts, fill=w, width=44, joint="curve")
im.save("assets/icon.png")
im.save("assets/icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icons written")

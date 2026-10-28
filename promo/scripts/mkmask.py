#!/usr/bin/env python3
# Feathered central ellipse mask (white = keep sharp, black = blur) for the
# beat-4 webcam punch-in. The zoompan keeps the face at frame centre, so a
# fixed centred ellipse tracks it.
from PIL import Image, ImageDraw, ImageFilter
W, H = 1920, 1080
m = Image.new("L", (W, H), 0)
d = ImageDraw.Draw(m)
cx, cy, rx, ry = 950, 500, 430, 500
d.ellipse([cx-rx, cy-ry, cx+rx, cy+ry], fill=255)
m = m.filter(ImageFilter.GaussianBlur(70))
m.save("mask_face.png")
print("mask_face.png written")

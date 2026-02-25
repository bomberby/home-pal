# Image dithering handling, move to a different file
from PIL import Image, ImageOps, ImageEnhance
import io

def dither_image():
  # Mapping: 0:black, 1:white, 2:green, 3:blue, 4:red, 5:yellow, 6:orange
  acep_palette = [
      0, 0, 0,        # 0: Black
      255, 255, 255,  # 1: White
      0, 255, 0,      # 2: Green
      0, 0, 255,      # 3: Blue
      255, 0, 0,      # 4: Red
      200, 200, 0,    # 5: Yellow
      255, 165, 0     # 6: Orange
  ] + [0]*(256*3-21)
  
  p_img = Image.new("P", (1, 1))
  p_img.putpalette(acep_palette)

  img = Image.open("tmp/IMG_3061.jpg").convert("RGB").quantize(colors=16, method=Image.MAXCOVERAGE).convert("RGB").resize((800, 480))

  converter = ImageEnhance.Color(img)
  img = converter.enhance(1.2)

  contrast = ImageEnhance.Contrast(img)
  img = contrast.enhance(1.2)

  r, g, b = img.split()
  b = b.point(lambda i: i * 1.05) # Boost blue intensity by 10%
  img = Image.merge('RGB', (r, g, b))

  # Randomly spread pixels by a tiny amount (radius 1 or 2)
  # This acts as a 'physical jitter' that breaks the dithering patterns
  img = img.effect_spread(distance=2)

  # Quantize using Floyd-Steinberg dithering for better looks
  quantized = img.quantize(palette=p_img, dither=Image.FLOYDSTEINBERG)
  quantized.save("tmp/differed.bmp")
  return quantized

def dither_pil_image(img):
    """Dither a PIL RGB image to the 7-color ACeP palette and return packed bytes (192000 bytes for 800×480)."""
    acep_palette = [
        0, 0, 0,        # 0: Black
        255, 255, 255,  # 1: White
        0, 255, 0,      # 2: Green
        0, 0, 255,      # 3: Blue
        255, 0, 0,      # 4: Red
        200, 200, 0,    # 5: Yellow
        255, 165, 0     # 6: Orange
    ] + [0] * (256 * 3 - 21)

    p_img = Image.new("P", (1, 1))
    p_img.putpalette(acep_palette)

    img = img.convert("RGB").resize((800, 480))
    img = ImageEnhance.Color(img).enhance(1.2)
    img = ImageEnhance.Contrast(img).enhance(1.2)
    quantized = img.quantize(palette=p_img, dither=Image.FLOYDSTEINBERG)

    pixels = list(quantized.getdata())
    packed_bytes = bytearray()
    for i in range(0, len(pixels), 2):
        packed_bytes.append(((pixels[i] & 0x0F) << 4) | (pixels[i + 1] & 0x0F))
    return bytes(packed_bytes)


def dither_bw_image():
  # Load and Resize to 800x480
  img = Image.open("tmp/IMG_3060.jpg").convert("RGB").resize((800, 480))
  img = img.convert('L')
      
  # INVERT the colors (Black becomes White and vice-versa)
  # This fixes the inversion on the e-ink display
  img = ImageOps.invert(img)
  
  # Apply Dithering
  dithered = img.convert('1')
  
  img_io = io.BytesIO()
  dithered.save(img_io, format='BMP')
  img_io.seek(0)

  return img_io
  # usage: 
  # send_file(img_io, mimetype='image/bmp')
      
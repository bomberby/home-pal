"""Real-ESRGAN upscaler for legacy persona images with no stored prompt.

Contains a minimal RRDB network implementation (no basicsr dependency)
and weight downloading/caching.  Used by ImageGenService._upgrade_loop()
as a fallback when an image has no tEXt metadata for prompt-based HQ regen.
"""
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

# Final output size for HQ images
FINAL_SIZE = 1024

# Real-ESRGAN weights (fallback upscaler for legacy images with no stored prompt)
_REALESRGAN_WEIGHTS_URL  = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"
_REALESRGAN_WEIGHTS_PATH = Path("env/weights/RealESRGAN_x4plus_anime_6B.pth")


# ------------------------------------------------------------------ #
#  Minimal RRDB network for Real-ESRGAN — no basicsr dependency       #
# ------------------------------------------------------------------ #

class _ResidualDenseBlock(nn.Module):
    def __init__(self, num_feat=64, num_grow_ch=32):
        super().__init__()
        self.conv1 = nn.Conv2d(num_feat, num_grow_ch, 3, 1, 1)
        self.conv2 = nn.Conv2d(num_feat + num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv3 = nn.Conv2d(num_feat + 2 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv4 = nn.Conv2d(num_feat + 3 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv5 = nn.Conv2d(num_feat + 4 * num_grow_ch, num_feat, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        x1 = self.lrelu(self.conv1(x))
        x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
        return x5 * 0.2 + x


class _RRDB(nn.Module):
    def __init__(self, num_feat, num_grow_ch=32):
        super().__init__()
        self.rdb1 = _ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb2 = _ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb3 = _ResidualDenseBlock(num_feat, num_grow_ch)

    def forward(self, x):
        out = self.rdb3(self.rdb2(self.rdb1(x)))
        return out * 0.2 + x


class _RRDBNet(nn.Module):
    def __init__(self, num_in_ch, num_out_ch, scale, num_feat, num_block, num_grow_ch=32):
        super().__init__()
        self.conv_first = nn.Conv2d(num_in_ch, num_feat, 3, 1, 1)
        self.body = nn.Sequential(*[_RRDB(num_feat, num_grow_ch) for _ in range(num_block)])
        self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up1  = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up2  = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_hr   = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_last = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        feat = self.conv_first(x)
        feat = feat + self.conv_body(self.body(feat))
        feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode='nearest')))
        feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode='nearest')))
        return self.conv_last(self.lrelu(self.conv_hr(feat)))


# ------------------------------------------------------------------ #
#  Upscaler singleton + public API                                    #
# ------------------------------------------------------------------ #

_upscaler: _RRDBNet | None = None


def get_upscaler() -> _RRDBNet | None:
    """Lazy-load the Real-ESRGAN model, downloading weights on first call."""
    global _upscaler
    if _upscaler is not None:
        return _upscaler
    _REALESRGAN_WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _REALESRGAN_WEIGHTS_PATH.exists():
        print("[ImageGen] Downloading RealESRGAN anime weights (~17MB)...")
        import urllib.request
        try:
            urllib.request.urlretrieve(_REALESRGAN_WEIGHTS_URL, _REALESRGAN_WEIGHTS_PATH)
            print("[ImageGen] RealESRGAN weights downloaded.")
        except Exception as e:
            print(f"[ImageGen] Failed to download weights: {e}")
            return None
    model = _RRDBNet(num_in_ch=3, num_out_ch=3, scale=4, num_feat=64, num_block=6, num_grow_ch=32)
    state_dict = torch.load(_REALESRGAN_WEIGHTS_PATH, map_location='cpu', weights_only=True)
    state_dict = state_dict.get('params_ema') or state_dict.get('params') or state_dict
    model.load_state_dict(state_dict, strict=True)
    model.eval()
    _upscaler = model
    print("[ImageGen] RealESRGAN upscaler ready.")
    return _upscaler


def upscale(src: Path, dst: Path) -> None:
    """Real-ESRGAN upscale a PNG file to dst (atomic write via tmp rename)."""
    from PIL import Image
    img = Image.open(src).convert('RGB')
    upscale_image(img, dst)


def upscale_image(img, dst: Path) -> None:
    """Real-ESRGAN upscale a PIL Image to dst (atomic write via tmp rename)."""
    model = get_upscaler()
    if model is None:
        return
    try:
        import numpy as np
        from PIL import Image
        tensor = torch.from_numpy(
            np.array(img).astype('float32') / 255.0
        ).permute(2, 0, 1).unsqueeze(0)
        with torch.no_grad():
            output = model(tensor).squeeze(0).permute(1, 2, 0).clamp(0, 1)
        result = Image.fromarray((output.numpy() * 255).astype('uint8'))
        if result.width > FINAL_SIZE or result.height > FINAL_SIZE:
            result = result.resize((FINAL_SIZE, FINAL_SIZE), Image.LANCZOS)
        tmp = dst.with_suffix('.tmp.png')
        result.save(tmp)
        tmp.rename(dst)
        print(f"[ImageGen] HQ saved: {dst.name} ({dst.stat().st_size // 1024}KB)")
    except Exception as e:
        print(f"[ImageGen] Upscale failed for '{dst.stem}': {e}")

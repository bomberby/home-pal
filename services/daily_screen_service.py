import datetime
import os

from PIL import Image, ImageDraw, ImageFont

from services.weather_service import get_default_location, get_hourly_forecast
from services.calendar_utils import is_event_on, parse_dt, event_label
import services.google_calendar as google_calendar
from services.home_context_service import HomeContextService
from models import Task, ShoppingListItem
import config

# ── Layout constants ──────────────────────────────────────────────────────────
W, H       = 800, 480
LEFT_W     = 220          # persona image panel width
RIGHT_X    = LEFT_W       # right panel x start
RIGHT_W    = W - LEFT_W   # 580 px

SENSOR_H   = 45           # indoor strip, full width
QUOTE_H    = 55           # quote strip, full width
CONTENT_H  = H - SENSOR_H - QUOTE_H   # 380 px  (left + right panels)

HEADER_H   = 45           # date header inside right panel
WEATHER_H  = 115          # forecast strip inside right panel
ROWS_Y     = HEADER_H + WEATHER_H          # 160 — row section y start
ROWS_H     = CONTENT_H - ROWS_Y           # 220 px for rows
MAX_ROWS   = 5
ROW_H      = ROWS_H // MAX_ROWS           # 44 px per row

# ── Palette colours matching the 7-colour ACeP dithering palette ──────────────
BLACK  = (0,   0,   0)
WHITE  = (255, 255, 255)
RED    = (220, 0,   0)
BLUE   = (0,   0,   220)
GREEN  = (0,   180, 0)
YELLOW = (200, 180, 0)
ORANGE = (240, 120, 0)
GREY   = (160, 160, 160)
LGREY  = (235, 235, 235)

CAL_DEFAULT_RGB = (252, 186, 3)  # amber fallback when calendar_color_rgb is missing


# ── Helpers ───────────────────────────────────────────────────────────────────

def _font(size, bold=False):
    candidates = (
        [
            # Windows
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
            # macOS
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            # Linux
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ] if bold else [
            # Windows
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            # macOS
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            # Linux
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()



def _temp_color(temp):
    temp = max(-5, min(35, temp))
    if temp <= 0:
        return (30, 80, 220)
    if temp <= 15:
        t = temp / 15
        return (0, int(220 * t), int(220 * (1 - t)))
    if temp <= 30:
        t = (temp - 15) / 15
        return (int(220 * t), int(180 * (1 - t)), 0)
    return (220, 0, 0)


def _build_rows(today_events):
    """Fill up to MAX_ROWS: events → overdue tasks → due-today tasks → shopping."""
    rows = []
    now_tz      = datetime.datetime.now(datetime.timezone.utc).astimezone()
    today_start = datetime.datetime.combine(datetime.date.today(), datetime.time.min)
    today_end   = datetime.datetime.combine(datetime.date.today(), datetime.time.max)

    # 1. Today's timed events, skip already-ended ones
    timed = sorted(
        [e for e in today_events if e.get('start', {}).get('dateTime')],
        key=lambda e: e['start']['dateTime'],
    )
    for event in timed:
        if len(rows) >= MAX_ROWS:
            break
        end_str = event.get('end', {}).get('dateTime')
        if end_str and parse_dt(end_str) < now_tz:
            continue
        rows.append({'type': 'event', 'data': event})

    # 2. Overdue tasks
    if len(rows) < MAX_ROWS:
        for task in (Task.select()
                     .where(Task.completed == False, Task.due_date < today_start)
                     .order_by(Task.due_date)):
            if len(rows) >= MAX_ROWS:
                break
            rows.append({'type': 'task_overdue', 'data': task})

    # 3. Tasks due today
    if len(rows) < MAX_ROWS:
        for task in (Task.select()
                     .where(Task.completed == False,
                            Task.due_date >= today_start,
                            Task.due_date <= today_end)
                     .order_by(Task.due_date)):
            if len(rows) >= MAX_ROWS:
                break
            rows.append({'type': 'task_today', 'data': task})

    # 4. Shopping list items
    if len(rows) < MAX_ROWS:
        for item in (ShoppingListItem.select()
                     .where(ShoppingListItem.purchased == False)
                     .limit(MAX_ROWS - len(rows))):
            rows.append({'type': 'shopping', 'data': item})

    return rows


# ── Section renderers ─────────────────────────────────────────────────────────

def _draw_weather_strip(draw, x, y, w, h, temps, precips, condition_labels):
    """24-hour forecast: coloured temperature curve + precip bars + condition labels."""
    if not temps:
        return
    n       = len(temps)
    bar_w   = w / n
    min_t   = min(temps)
    max_t   = max(temps)
    t_range = max(max_t - min_t, 1.0)

    label_h  = 38   # top area for time + condition labels
    prec_h   = 18   # bottom area for precipitation bars
    curve_y0 = y + label_h
    curve_h  = h - label_h - prec_h

    fn_small = _font(16)

    # Precipitation bars (bottom strip, blue)
    max_p = max(precips) if max(precips) > 0 else 1.0
    for i, p in enumerate(precips):
        if p > 0:
            bh  = int((p / max_p) * prec_h)
            bx  = x + int(i * bar_w)
            bw  = max(2, int(bar_w) - 1)
            draw.rectangle([bx, y + h - bh, bx + bw, y + h], fill=(100, 140, 220))

    # Temperature curve (coloured by temperature)
    pts = []
    for i, t in enumerate(temps):
        tx = x + int(i * bar_w) + int(bar_w / 2)
        ty = curve_y0 + curve_h - int((t - min_t) / t_range * curve_h)
        pts.append((tx, ty))

    for i in range(len(pts) - 1):
        col = _temp_color((temps[i] + temps[i + 1]) / 2)
        draw.line([pts[i], pts[i + 1]], fill=col, width=3)

    # Time + condition labels every 6 hours
    now_hour = datetime.datetime.now().hour
    for tick in range(0, min(n, 24), 6):
        tx      = x + int(tick * bar_w) + int(bar_w / 2)
        hour    = (now_hour + tick) % 24
        time_lbl = f"{hour:02d}h"
        cond_lbl = condition_labels[tick] if condition_labels and tick < len(condition_labels) else ""
        draw.text((tx, y + 10), time_lbl, font=fn_small, fill=BLACK, anchor="mm")
        draw.text((tx, y + 28), cond_lbl, font=fn_small, fill=BLACK, anchor="mm")

    # Min / max in top-right corner
    draw.text((x + w - 4, y + 10), f"{min_t:.0f}°–{max_t:.0f}°C", font=fn_small, fill=BLACK, anchor="rm")


def _draw_row(draw, x, y, w, h, row):
    """Render a single event / task / shopping row."""
    fn      = _font(24)
    fn_bold = _font(24, bold=True)
    rtype   = row['type']
    data    = row['data']
    pad = 8
    cy  = y + h // 2        # vertical centre of row

    if rtype == 'event':
        color = tuple(data.get('calendar_color_rgb', CAL_DEFAULT_RGB))
        draw.ellipse([x + pad, cy - 7, x + pad + 14, cy + 7], fill=color)
        start_str = data.get('start', {}).get('dateTime', '')
        if start_str:
            draw.text((x + pad + 20, cy), parse_dt(start_str).strftime('%H:%M'),
                      font=fn_bold, fill=BLACK, anchor="lm")
        title = event_label(data)[:32]
        draw.text((x + pad + 110, cy), title, font=fn, fill=BLACK, anchor="lm")

    elif rtype == 'task_overdue':
        draw.ellipse([x + pad, cy - 7, x + pad + 14, cy + 7], fill=ORANGE)
        draw.text((x + pad + 20, cy), "OVERDUE  " + data.task_name[:28],
                  font=fn, fill=ORANGE, anchor="lm")

    elif rtype == 'task_today':
        draw.rectangle([x + pad, cy - 7, x + pad + 14, cy + 7],
                       outline=BLACK, width=2)
        draw.text((x + pad + 20, cy), data.task_name[:36], font=fn, fill=BLACK, anchor="lm")

    elif rtype == 'shopping':
        draw.ellipse([x + pad + 2, cy - 5, x + pad + 12, cy + 5], fill=GREEN)
        qty   = f" ×{data.quantity}" if data.quantity > 1 else ""
        draw.text((x + pad + 20, cy), data.item_name[:36] + qty, font=fn, fill=BLACK, anchor="lm")

    # Subtle row separator
    draw.line([(x + pad, y + h - 1), (x + w - pad, y + h - 1)], fill=(210, 210, 210))


def _draw_sensor_strip(draw, x, y, w, h):
    """Indoor sensors with smart air quality label."""
    fn = _font(20)
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=LGREY)
    draw.line([(x, y), (x + w - 1, y)], fill=BLACK)

    t   = HomeContextService._indoor_temp
    hum = HomeContextService._indoor_humidity
    nox = HomeContextService._nox

    parts = []
    if t   is not None: parts.append((f"{t:.1f}°C",  BLACK))
    if hum is not None: parts.append((f"{hum:.0f}% RH", BLACK))

    quality = HomeContextService.air_quality()
    if quality == 'alert': parts.append(("Air: Alert !", (200, 0,   0)))
    elif quality == 'poor': parts.append(("Air: Poor",   (180, 80,  0)))
    elif quality == 'good': parts.append(("Air: Good ✓", (0,  130,  0)))

    if nox is not None and nox > 50:
        parts.append((f"NOx: {nox:.0f}", (180, 80, 0)))

    fn_label = _font(16)
    cy_s = y + h // 2
    cx   = x + 16
    draw.text((cx, cy_s), "INDOOR", font=fn_label, fill=BLACK, anchor="lm")
    cx += int(draw.textlength("INDOOR", font=fn_label)) + 20
    for label, color in parts:
        draw.text((cx, cy_s), label, font=fn, fill=color, anchor="lm")
        cx += int(draw.textlength(label, font=fn)) + 24


def _draw_quote_strip(draw, x, y, w, h, quote):
    """Persona quote centred vertically in the bottom strip."""
    fn = _font(22)
    draw.line([(x, y), (x + w - 1, y)], fill=BLACK)
    # Soft word-wrap: try to fit on one line, split at ~90 chars if needed
    max_line = 90
    text = f'"{quote}"'
    cy_q = y + h // 2
    if len(text) > max_line:
        # Split at a word boundary near the midpoint
        mid = len(text) // 2
        split = text.rfind(' ', 0, mid + 15)
        if split < 20:
            split = mid
        lines = [text[:split].strip(), text[split:].strip()]
        line_h = 26
        ty = cy_q - line_h // 2  # first line slightly above centre so block is centred
        for line in lines:
            draw.text((x + 20, ty), line, font=fn, fill=BLACK, anchor="lm")
            ty += line_h
    else:
        draw.text((x + 20, cy_q), text, font=fn, fill=BLACK, anchor="lm")


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_daily_image() -> Image.Image:
    img  = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    today     = datetime.date.today()
    today_str = today.strftime('%Y-%m-%d')

    # ── Persona image (left panel, x=0-219, y=0-379) ─────────────────────────
    from agents.persona_agent import PersonaAgent
    from services.image_gen_service import ImageGenService
    persona    = PersonaAgent.get_current_state()
    state_key  = persona.get('state', '')
    image_path = ImageGenService.get_cached(state_key)

    if image_path:
        try:
            pi = Image.open(image_path).convert("RGB")
            # Scale to fill LEFT_W × CONTENT_H, then centre-crop
            scale = max(LEFT_W / pi.width, CONTENT_H / pi.height)
            nw, nh = int(pi.width * scale), int(pi.height * scale)
            pi = pi.resize((nw, nh), Image.LANCZOS)
            xo = (nw - LEFT_W) // 2
            yo = (nh - CONTENT_H) // 2
            pi = pi.crop((xo, yo, xo + LEFT_W, yo + CONTENT_H))
            img.paste(pi, (0, 0))
        except Exception as e:
            print(f"[Daily] Persona image error: {e}")

    # Vertical divider
    draw.line([(LEFT_W, 0), (LEFT_W, CONTENT_H)], fill=BLACK, width=2)

    # ── Date header (right panel, y=0-44) ────────────────────────────────────
    date_label = today.strftime('%A, %b %d').upper()
    draw.rectangle([(RIGHT_X, 0), (W - 1, HEADER_H - 1)], fill=LGREY)
    draw.text((RIGHT_X + 12, HEADER_H // 2), date_label, font=_font(26, bold=True), fill=BLACK, anchor="lm")
    draw.line([(RIGHT_X, HEADER_H), (W - 1, HEADER_H)], fill=BLACK)

    # ── Weather strip (right panel, y=45-159) ────────────────────────────────
    forecast = get_hourly_forecast(get_default_location(), count=24)
    if forecast:
        _draw_weather_strip(
            draw,
            RIGHT_X + 4, HEADER_H + 4,
            RIGHT_W - 8, WEATHER_H - 8,
            forecast['temps'],
            forecast['precips'],
            forecast['condition_descriptions'],
        )
    else:
        draw.text((RIGHT_X + 10, HEADER_H + 10), "Weather unavailable",
                  font=_font(15), fill=GREY)

    draw.line([(RIGHT_X, HEADER_H + WEATHER_H), (W - 1, HEADER_H + WEATHER_H)], fill=BLACK)

    # ── Event / task / shopping rows (right panel, y=160-379) ────────────────
    try:
        all_events   = google_calendar.get_all_events()
        today_events = [e for e in all_events if is_event_on(e, today_str)]
    except Exception as e:
        print(f"[Daily] Calendar error: {e}")
        today_events = []

    rows = _build_rows(today_events)

    if not rows:
        draw.text((RIGHT_X + 12, ROWS_Y + ROW_H // 2), "Nothing scheduled today",
                  font=_font(20), fill=GREY, anchor="lm")
    else:
        for i, row in enumerate(rows):
            _draw_row(draw, RIGHT_X, ROWS_Y + i * ROW_H, RIGHT_W, ROW_H, row)

    # ── Sensor strip (full width, y=380-424) ─────────────────────────────────
    _draw_sensor_strip(draw, 0, CONTENT_H, W, SENSOR_H)

    # ── Quote strip (full width, y=425-479) ──────────────────────────────────
    quote = persona.get('quote') or "Every day is a fresh start."
    _draw_quote_strip(draw, 0, H - QUOTE_H, W, QUOTE_H, quote)

    return img

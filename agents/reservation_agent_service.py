import re
import datetime
import services.tablecheck_service as tc

# Configurable default guest used when no name is extracted from the query.
DEFAULT_LAST_NAME = "Omer"
DEFAULT_FIRST_NAME = None

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


class ReservationAgentService:

    # ------------------------------------------------------------------
    # Intent router — called from AgentService
    # ------------------------------------------------------------------

    @staticmethod
    def handle_intent(query: str) -> str | None:
        q = query.lower().strip()
        words = set(re.sub(r'[^\w\s]', '', q).split())

        if words.intersection({"restaurant", "restaurants", "availability", "available"}):
            if re.search(r'\bin\s+[A-Z][a-z]+', query):
                return ReservationAgentService.list_restaurants(query)
            if words.intersection({"book", "reserve", "table"}):
                return ReservationAgentService.book_table(query)
            if words.intersection({"availability", "available", "slots", "times"}):
                return ReservationAgentService.check_availability(query)
            return ReservationAgentService.list_restaurants(query)

        if words.intersection({"reservation", "reservations"}):
            if "cancel" in q:
                return ReservationAgentService.cancel_reservation(query)
            if words.intersection({"note", "notes", "request", "dietary", "allergy"}):
                return ReservationAgentService.add_note(query)
            if words.intersection({"book", "make", "create"}):
                return ReservationAgentService.book_table(query)
            return ReservationAgentService.list_reservations()

        if "book" in q and words.intersection({"table", "seat", "dinner", "lunch"}):
            return ReservationAgentService.book_table(query)

        return None

    # ------------------------------------------------------------------
    # Public intent handlers
    # ------------------------------------------------------------------

    @staticmethod
    def list_restaurants(query: str | None = None) -> str:
        try:
            shops = tc.list_shops()
        except Exception as e:
            return f"Couldn't reach the restaurant service: {e}"
        if not shops:
            return "No restaurants found."

        city = ReservationAgentService._extract_city(query, shops) if query else None
        if city:
            shops = [s for s in shops if s.get("city", "").lower() == city.lower()]
            if not shops:
                return f"No restaurants found in {city}."

        lines = [
            f"{s['name']} ({s.get('city', '?')})"
            + (f", {', '.join(s['cuisines'])}" if s.get('cuisines') else "")
            for s in shops
        ]
        prefix = f"Restaurants in {city}: " if city else "Available restaurants: "
        return prefix + "; ".join(lines) + "."

    @staticmethod
    def _extract_city(query: str, shops: list[dict]) -> str | None:
        known = {s.get("city", "").lower(): s.get("city") for s in shops if s.get("city")}
        q_lower = query.lower()
        for city_lower, city_name in known.items():
            if city_lower in q_lower:
                return city_name
        return None

    @staticmethod
    def check_availability(query: str) -> str:
        shop, date_str, pax = ReservationAgentService._extract_booking_params(query)
        if not shop:
            return "Which restaurant? Try 'check availability at [name] for [N] on [date]'."
        if not date_str:
            return "Which date? Try 'for 2 on June 15'."
        if not pax:
            return "How many guests? Try 'for 2 people'."

        shop_id, shop_name = ReservationAgentService._resolve_shop(shop)
        if not shop_id:
            return f"I couldn't find a restaurant matching '{shop}'. Say 'show restaurants' to see the list."

        try:
            slots = tc.get_availability(shop_id, date_str, pax)
        except Exception as e:
            return f"Couldn't fetch availability: {e}"

        if not slots:
            return f"No available slots at {shop_name} on {date_str} for {pax} guests."

        formatted = [_fmt_slot(s) for s in slots[:6]]
        suffix = f" (and {len(slots) - 6} more)" if len(slots) > 6 else ""
        return (
            f"Available times at {shop_name} on {date_str} for {pax} guests: "
            + ", ".join(formatted) + suffix + "."
        )

    @staticmethod
    def book_table(query: str) -> str:
        shop, date_str, pax = ReservationAgentService._extract_booking_params(query)
        if not shop:
            return "Which restaurant? Try 'book a table at [name] for [N] on [date]'."
        if not date_str:
            return "Which date? Try 'on June 15' or 'tomorrow'."
        if not pax:
            return "How many guests? Try 'for 2 people'."

        shop_id, shop_name = ReservationAgentService._resolve_shop(shop)
        if not shop_id:
            return f"I couldn't find a restaurant matching '{shop}'. Say 'show restaurants' to see the list."

        # Extract requested time (e.g. "at 7pm", "at 19:00") to pick the best slot.
        preferred_time = ReservationAgentService._extract_preferred_time(query)

        try:
            slots = tc.get_availability(shop_id, date_str, pax)
        except Exception as e:
            return f"Couldn't check availability: {e}"

        if not slots:
            return f"No available slots at {shop_name} on {date_str} for {pax} guests."

        slot = _pick_slot(slots, preferred_time)

        last_name, first_name = ReservationAgentService._extract_guest_name(query)

        try:
            res = tc.create_reservation(
                shop_id=shop_id,
                start_at=slot,
                pax=pax,
                last_name=last_name,
                first_name=first_name,
            )
        except Exception as e:
            return f"Booking failed: {e}"

        code = res.get("code", res.get("id", "?"))
        time_str = _fmt_slot(slot)
        name_str = f"{first_name} {last_name}".strip() if first_name else last_name
        return (
            f"Booked! {name_str}, {pax} guests at {shop_name} on {date_str} at {time_str}. "
            f"Confirmation code: {code}."
        )

    @staticmethod
    def list_reservations() -> str:
        try:
            reservations = tc.list_reservations()
        except Exception as e:
            return f"Couldn't fetch reservations: {e}"
        if not reservations:
            return "You have no recent reservations."
        lines = []
        for res in reservations[:5]:
            guest = res.get("guest", {})
            name = " ".join(filter(None, [guest.get("first_name"), guest.get("last_name")])) or "?"
            time_str = _fmt_slot(res["start_at"]) if res.get("start_at") else "?"
            lines.append(
                f"{res.get('code', res['id'])}: {res['shop_id']} on {res['start_at'][:10]} "
                f"at {time_str}, {res.get('pax', '?')} guests ({name}, {res.get('status', '?')})"
            )
        return "Recent reservations: " + "; ".join(lines) + "."

    @staticmethod
    def add_note(query: str) -> str:
        code = ReservationAgentService._extract_code(query)
        if not code:
            return "Which reservation? Include the code, e.g. 'add a note to reservation ABC123 that ...'."

        # Extract the note text — everything after "that", "saying", or "note:"
        note_match = re.search(r'\b(?:that|saying|note[:\s]+)\s+(.+)$', query, re.IGNORECASE)
        if not note_match:
            return "What should the note say? Try 'add a note to reservation ABC123 that I don't eat shrimp'."
        note = note_match.group(1).strip().rstrip('.')

        reservation_id = ReservationAgentService._find_id_by_code(code)
        if not reservation_id:
            return f"Couldn't find a reservation with code {code}."

        try:
            tc.update_reservation(reservation_id, note)
        except Exception as e:
            return f"Couldn't update reservation {code}: {e}"
        return f"Got it — added to reservation {code}: '{note}'."

    @staticmethod
    def cancel_reservation(query: str) -> str:
        code = ReservationAgentService._extract_code(query)
        if not code:
            return "Which reservation? Include the confirmation code, e.g. 'cancel reservation ABC123'."

        # Try to find the reservation by code across the list.
        reservation_id = ReservationAgentService._find_id_by_code(code)
        if not reservation_id:
            return f"Couldn't find a reservation with code {code}."

        try:
            tc.cancel_reservation(reservation_id)
        except Exception as e:
            return f"Cancellation failed: {e}"
        return f"Reservation {code} has been cancelled."

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_booking_params(query: str) -> tuple[str | None, str | None, int | None]:
        """Return (shop_fragment, date_str YYYY-MM-DD, pax) from free text."""
        q = query.lower()

        # --- pax ---
        pax = None
        pax_match = re.search(r'for\s+(\d+)\s*(?:people|guests?|persons?|pax)?', q)
        if pax_match:
            pax = int(pax_match.group(1))
        else:
            for word, n in _NUMBER_WORDS.items():
                if re.search(rf'\bfor\s+{word}\b', q):
                    pax = n
                    break

        # --- date ---
        date_str = None
        today = datetime.date.today()

        if "today" in q:
            date_str = today.isoformat()
        elif "tomorrow" in q:
            date_str = (today + datetime.timedelta(days=1)).isoformat()
        else:
            # "on June 15", "on the 15th", "on 2026-06-15"
            iso_match = re.search(r'(\d{4}-\d{2}-\d{2})', query)
            if iso_match:
                date_str = iso_match.group(1)
            else:
                month_day = re.search(
                    r'(?:on\s+)?(?:the\s+)?'
                    r'(?:(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?'
                    r'|(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(\w+))',
                    q,
                )
                if month_day:
                    g = month_day.groups()
                    # form: "June 15" or "15 June"
                    month_word = (g[0] or g[3] or "").lower()
                    day_num = int(g[1] or g[2] or 0)
                    month_num = _MONTH_NAMES.get(month_word)
                    if month_num and day_num:
                        year = today.year
                        candidate = datetime.date(year, month_num, day_num)
                        if candidate < today:
                            candidate = datetime.date(year + 1, month_num, day_num)
                        date_str = candidate.isoformat()

        # --- restaurant name ---
        # "at Ron Simphony", "at the park restaurant"
        shop = None
        at_match = re.search(
            r'\bat\s+(?:the\s+)?([a-zA-Z0-9][a-zA-Z0-9 \-\']*?)'
            r'(?=\s+(?:for\b|on\b|tomorrow\b|today\b|\d)|,|$)',
            query, re.IGNORECASE,
        )
        if at_match:
            shop = at_match.group(1).strip()
        if not shop:
            # "Does Ron Resto have availability" / "Is Ron Resto available tomorrow"
            have_match = re.search(
                r'\b(?:does|is|do)\s+(.+?)\s+(?:have|has)\s+(?:availability|available)',
                query, re.IGNORECASE,
            )
            if have_match:
                shop = have_match.group(1).strip()
        return shop, date_str, pax

    @staticmethod
    def _extract_preferred_time(query: str) -> int | None:
        """Return preferred hour (0-23) from free text, or None."""
        q = query.lower()
        # "at 7pm", "at 19:00", "at 7:30pm"
        if "breakfast" in q:
            return 8
        if "lunch" in q:
            return 12
        if "dinner" in q:
            return 19
        match = re.search(r'at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', q)
        if not match:
            return None
        hour = int(match.group(1))
        meridiem = match.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return hour

    @staticmethod
    def _extract_guest_name(query: str) -> tuple[str, str | None]:
        """Return (last_name, first_name). Falls back to DEFAULT_LAST_NAME."""
        # "name Yamada Taro", "for Yamada", "book for John Smith"
        match = re.search(r'(?:name|for)\s+([A-Z][a-z]+)(?:\s+([A-Z][a-z]+))?', query)
        if match:
            first = match.group(1)
            second = match.group(2)
            if second:
                return second, first  # last, first
            return first, None
        return DEFAULT_LAST_NAME, DEFAULT_FIRST_NAME

    @staticmethod
    def _resolve_shop(fragment: str) -> tuple[str | None, str | None]:
        """Return (shop_id, shop_name) by fuzzy-matching fragment against the shop list."""
        try:
            shops = tc.list_shops()
        except Exception:
            return None, None
        fragment_lower = fragment.lower()
        for shop in shops:
            if fragment_lower in shop["name"].lower() or fragment_lower in shop["id"].lower():
                return shop["id"], shop["name"]
        # Try word-level overlap
        fragment_words = set(fragment_lower.split())
        for shop in shops:
            name_words = set(shop["name"].lower().split())
            if fragment_words & name_words:
                return shop["id"], shop["name"]
        return None, None

    @staticmethod
    def _extract_code(query: str) -> str | None:
        """Extract a reservation code from free text.

        Tries two strategies in order:
        1. The token immediately after the word 'reservation'.
        2. Any token that looks like a code: uppercase letters + digits, 4-10 chars,
           must contain at least one digit (excludes plain English words like 'Please').
        """
        after_keyword = re.search(r'\breservation\s+([A-Za-z0-9]{4,10})\b', query, re.IGNORECASE)
        if after_keyword:
            return after_keyword.group(1).upper()
        code_like = re.search(r'\b(?=[A-Z0-9]{4,10}\b)(?=[^a-z]*\d)[A-Z0-9]{4,10}\b', query)
        if code_like:
            return code_like.group(0).upper()
        return None

    @staticmethod
    def _find_id_by_code(code: str) -> str | None:
        """Look up reservation ID by human-readable code."""
        try:
            reservations = tc.list_reservations()
        except Exception:
            return None
        for res in reservations:
            if res.get("code", "").upper() == code or res.get("id", "").upper() == code:
                return res["id"]
        return None


def _fmt_slot(iso: str) -> str:
    """Format an ISO 8601 timestamp as HH:MM in local time for TTS."""
    try:
        iso_clean = iso.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(iso_clean)
        return dt.astimezone().strftime("%H:%M")
    except Exception:
        return iso


def _pick_slot(slots: list[str], preferred_hour: int | None) -> str:
    """Return the slot closest to preferred_hour, or the first slot."""
    if preferred_hour is None or not slots:
        return slots[0]
    best = min(
        slots,
        key=lambda s: abs(_slot_hour(s) - preferred_hour),
    )
    return best


def _slot_hour(iso: str) -> int:
    try:
        iso_clean = iso.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(iso_clean)
        return dt.astimezone().hour  # convert to system local time before comparing
    except Exception:
        return 0

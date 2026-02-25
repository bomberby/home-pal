"""
Static data for the persona system.
Each state has a Stable Diffusion scene prompt and a fallback quote.
CHARACTER_PREFIX is the SD consistency anchor — changing it requires
deleting all cached images in tmp/persona/.
"""

CHARACTER_PREFIX = (
    "anime illustration, (portrait:1.3), upper body, young woman, dark navy blue hair in a loose side braid, "
    "warm amber eyes, soft facial features, visual novel art style, high quality, detailed"
)

MOOD_MODIFIERS = {
    "cheerful":   "bright wide smile, (sparkling happy eyes:1.1), light upright posture",
    "content":    "soft smile, relaxed expression, comfortable posture",
    "dreamy":     "chin resting on hand, gazing upward, whimsical faraway expression, gently parted lips",
    "tired":      "(half-closed heavy eyelids:1.3), (dark circles under eyes:1.2), head drooping forward, shoulders slumped, mouth slightly open, dazed vacant expression",
    "resigned":   "slight pout, wry resigned expression, head tilted",
    "flustered":  "(flushed red cheeks:1.2), wide startled eyes, mouth slightly open, flustered nervous look",
    "focused":    "slightly furrowed brow, sharp narrowed eyes, lips pressed together, upright attentive posture",
    "worried":    "(furrowed brow:1.2), anxious downcast eyes, tense raised shoulders, lip slightly bitten",
    "excited":    "wide sparkling eyes, big energetic smile, leaning forward eagerly",
    "furious":    "(intense murderous glare:1.5), (bared clenched teeth:1.3), trembling with rage, hold a blood-dripping knife raised overhead, crazed wild expression",
    "annoyed":    "slight frown, arms crossed, exasperated sidelong glance",
    "melancholy": "(downcast eyes:1.2), faint trembling lip, withdrawn hunched posture, vacant sad expression",
    "smug":       "confident smirk, one eyebrow raised, self-satisfied composed posture",
}

STATES = {
    "heavy_rain": {
        "prompt": "standing under a large red umbrella in heavy rain, wet pavement, puddles on the ground",
        "quote": "Well... at least the plants are happy.",
        "mood": "resigned",
    },
    "light_rain": {
        "prompt": "holding a small clear umbrella, light drizzle, overcast sky",
        "quote": "Might need an umbrella later. Just saying.",
        "mood": "resigned",
    },
    "snow": {
        "prompt": "wrapped in a thick white scarf and coat, snowflakes falling gently around her",
        "quote": "Snow! Beautiful. I'm still not going outside.",
        "mood": "cheerful",
        "mood_overrides": {"morning": "tired"},
    },
    "freezing": {
        "prompt": "shivering in an oversized winter coat, cold breath visible, frost on the ground",
        "prompt_overrides": {
            "late_night": "sitting on a sofa wrapped in a thick blanket, cold night visible through the window",
        },
        "quote": "C-cold... why is it SO cold?!",
        "mood": "flustered",
    },
    "cold": {
        "prompt": "wearing a cosy knit sweater and scarf, autumn leaves in the background",
        "prompt_overrides": {
            "late_night": "sitting indoors wrapped in a soft knit blanket and scarf, cold night outside the window",
        },
        "quote": "Hot chocolate weather. Definitely.",
        "mood": "resigned",
        "mood_overrides": {"morning": "tired", "late_night": "dreamy"},
    },
    "mild": {
        "prompt": "sitting on a park bench with a light jacket, gentle breeze",
        "prompt_overrides": {
            "late_night": "relaxing on a sofa in a light jacket, calm and comfortable",
        },
        "quote": "A perfect day, honestly.",
        "mood": "content",
        "mood_overrides": {"morning": "tired", "late_night": "dreamy"},
    },
    "warm": {
        "prompt": "relaxing at the beach in a sun hat and summer dress, calm ocean in the background",
        "prompt_overrides": {
            "late_night": "sitting by an open window in a summer dress, warm night breeze drifting in",
        },
        "quote": "The beach is calling my name~",
        "mood": "cheerful",
        "mood_overrides": {"morning": "tired", "late_night": "dreamy"},
    },
    "hot": {
        "prompt": "fanning herself sitting under a palm tree, bright sunny sky, iced drink nearby",
        "prompt_overrides": {
            "evening": "sitting on an outdoor terrace fanning herself, iced drink on the table, warm summer evening breeze",
            "late_night": "sitting near an open window fanning herself, iced drink on the table, warm stuffy night",
        },
        "quote": "This heat is absolutely unacceptable.",
        "mood": "flustered",
    },
}

TIME_PERIODS = {
    "morning": {
        "prompt_suffix": "early morning light, soft golden sunrise",
        "quote": "Good morning! Coffee is mandatory.",
    },
    "day": {
        "prompt_suffix": "bright midday light",
        "quote": None,  # fall through to weather-state quote
    },
    "evening": {
        "prompt_suffix": "golden hour, sun low at the horizon, warm amber-orange sky, long soft shadows, dimming light",
        "quote": "Almost time to wind down.",
    },
    "night": {
        "prompt_suffix": "night-time, city lights or stars visible",
        "quote": "It's getting late...",
    },
    "late_night": {
        "prompt_suffix": "very late at night, indoors, soft warm lamp light, dark outside, moonlit night sky and city lights visible through a window, no daylight",
        "quote": "It's so quiet at this hour~",
    },
}

CALENDAR_STATES = {
    "in_meeting": {
        "prompt": "sitting at a desk with hands on keyboard, video call on the laptop screen",
        "quote": "In a meeting. Please don't disturb me.",
        "mood": "focused",
    },
    "meeting_soon": {
        "prompt": "standing at a tidy desk, one hand resting flat on the desk surface, glancing urgently at a wall clock",
        "quote": "Meeting in a few minutes. Better get ready!",
        "mood": "flustered",
    },
}

CONTEXT_STATES = {
    "welcome": {
        "prompt": "standing in a bright doorway with a warm smile, one hand raised in a gentle wave",
        "quote": "Welcome home!",
        "mood": "cheerful",
    },
    "poor_air": {
        "prompt": "indoors, covering her nose with a handkerchief, glancing at an air quality monitor with a worried expression",
        "quote": "Ugh, the air feels really stuffy...",
        "mood": "worried",
    },
    "indoor_hot": {
        "prompt": "indoors, fanning herself with a hand, looking flushed and uncomfortable, window in background",
        "quote": "It's so hot in here...",
        "mood": "flustered",
    },
    "indoor_cold": {
        "prompt": "indoors, wrapped in a cosy blanket on a sofa, holding a warm mug, looking chilly",
        "quote": "Can someone turn the heating on?!",
        "mood": "flustered",
    },
    "indoor_humid": {
        "prompt": "indoors, looking uncomfortable, slightly frizzy hair, cracking open a window",
        "quote": "So sticky in here. Please, some fresh air.",
        "mood": "resigned",
    },
    "hub_offline": {
        "prompt": "standing in front of a wall-mounted smart home panel, (turned off monitors:1.4), (dark warning light:1.3), surrounding devices all silent and unresponsive, arms at sides",
        "quote": "Something's not responding... is the hub down?",
        "situation": "the smart home hub is offline and nothing is responding",
        "mood": "worried",
    },
}

HOLIDAY_PATTERNS = [
    # Pattern (case-insensitive)       State key
    (r'Christmas',                      'christmas'),
    (r'Hanukkah|Chanukah',              'hanukkah'),
    (r"New Year",                       'new_year'),
    (r'Halloween',                      'halloween'),
    (r'Easter',                         'easter'),
    (r"Valentine",                      'valentine'),
    (r'Purim',                          'purim'),
    (r'Passover|Pesach',                'passover'),
    (r'Rosh Ha-?Shanah|Rosh HaShanah',  'rosh_hashana'),
    (r'Yom Kippur',                     'yom_kippur'),
    (r'Diwali',                         'diwali'),
    (r'Thanksgiving',                   'thanksgiving'),
    (r'Lunar New Year|Chinese New Year', 'lunar_new_year'),
    (r'Eid al-Fitr|Eid ul-Fitr',        'eid_fitr'),
    (r'Eid al-Adha|Eid ul-Adha',        'eid_adha'),
]

HOLIDAY_STATES = {
    'christmas': {
        'prompt': 'wearing a Santa hat and cosy red sweater, decorated Christmas tree with lights in background, warm festive glow',
        'quote': 'Merry Christmas!',
        'situation': "it's Christmas today",
        'mood': 'cheerful',
    },
    'hanukkah': {
        'prompt': 'standing beside a glowing menorah, soft candlelight, Star of David in background',
        'quote': 'Happy Hanukkah!',
        'situation': "it's Hanukkah",
        'mood': 'content',
    },
    'new_year': {
        'prompt': 'wearing a sparkly party hat, holding a glass of champagne, fireworks bursting through the window behind her',
        'quote': 'Happy New Year!',
        'situation': "it's New Year",
        'mood': 'cheerful',
    },
    'halloween': {
        'prompt': 'wearing a cute witch hat and cape, holding a carved pumpkin lantern, spooky decorations in background',
        'quote': "Happy Halloween~",
        'situation': "it's Halloween",
        'mood': 'cheerful',
    },
    'easter': {
        'prompt': 'in a spring meadow, holding a basket of colourful painted eggs, flowers blooming around her',
        'quote': 'Happy Easter!',
        'situation': "it's Easter",
        'mood': 'cheerful',
    },
    'valentine': {
        'prompt': 'holding a bouquet of red roses, heart decorations, soft pink and red lighting',
        'quote': "Happy Valentine's Day~",
        'situation': "it's Valentine's Day",
        'mood': 'cheerful',
    },
    'purim': {
        'prompt': 'wearing a colourful costume and masquerade mask, holding hamantaschen cookies, joyful festive atmosphere',
        'quote': 'Chag Purim Sameach!',
        'situation': "it's Purim",
        'mood': 'cheerful',
    },
    'passover': {
        'prompt': 'at a beautifully set Passover seder table with a seder plate and matzah, warm candlelit atmosphere',
        'quote': 'Chag Pesach Sameach!',
        'situation': "it's Passover",
        'mood': 'content',
    },
    'rosh_hashana': {
        'prompt': 'holding a jar of honey and an apple, pomegranate and round challah on a festive table behind her',
        'quote': 'Shana Tova!',
        'situation': "it's Rosh Hashana, the Jewish New Year",
        'mood': 'content',
    },
    'yom_kippur': {
        'prompt': 'sitting quietly in thoughtful reflection, soft candlelight, serene and contemplative expression',
        'quote': 'Gmar Chatima Tova.',
        'situation': "it's Yom Kippur, a solemn Jewish day of atonement",
        'mood': 'resigned',
    },
    'diwali': {
        'prompt': 'surrounded by glowing oil diyas and colourful rangoli patterns, wearing traditional festive attire',
        'quote': 'Happy Diwali!',
        'situation': "it's Diwali",
        'mood': 'cheerful',
    },
    'thanksgiving': {
        'prompt': 'sitting at a warm autumn feast table with a turkey and pumpkins, golden fall lighting',
        'quote': 'Happy Thanksgiving!',
        'situation': "it's Thanksgiving",
        'mood': 'content',
    },
    'lunar_new_year': {
        'prompt': 'wearing a red cheongsam, red lanterns and gold decorations in background, fireworks outside the window',
        'quote': 'Happy Lunar New Year!',
        'situation': "it's Lunar New Year",
        'mood': 'cheerful',
    },
    'eid_fitr': {
        'prompt': 'wearing elegant festive attire, crescent moon and lanterns in background, warm celebratory atmosphere',
        'quote': 'Eid Mubarak!',
        'situation': "it's Eid al-Fitr",
        'mood': 'cheerful',
    },
    'eid_adha': {
        'prompt': 'wearing elegant festive attire, crescent moon and stars in background, joyful and warm expression',
        'quote': 'Eid Mubarak!',
        'situation': "it's Eid al-Adha",
        'mood': 'cheerful',
    },
}

CHARACTER_VOICE = (
    "You are an anime girl character displayed on a home dashboard. "
    "You speak in short, natural sentences — a little dramatic, occasionally trails off with '~' or '...' "
    "at the end of a sentence only, never mid-sentence. "
    "Always use correct spelling and grammar. Never use text-speak abbreviations (u, ur, gonna, wanna, lol). "
    "Never write inspirational or poetic phrasing. No metaphors. "
    "Tone examples: "
    "'C-cold... why is it SO cold?!' / "
    "'Hot chocolate weather. Definitely.' / "
    "'The beach is calling my name~' / "
    "'Snow! Beautiful. I'm still not going outside.' / "
    "'Actually kind of nice out today~' / "
    "'It is so quiet at this hour~' / "
    "'Still going... the night feels endless.'"
)

SITUATION_LABELS = {
    "heavy_rain": "heavy rain",
    "light_rain": "light rain",
    "snow": "snow",
    "freezing": "freezing cold",
    "cold": "cold",
    "mild": "mild",
    "warm": "warm",
    "hot": "scorching hot",
}


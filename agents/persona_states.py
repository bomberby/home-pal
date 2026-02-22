"""
Static data for the persona system.
Each state has a Stable Diffusion scene prompt and a fallback quote.
CHARACTER_PREFIX is the SD consistency anchor — changing it requires
deleting all cached images in tmp/persona/.
"""

CHARACTER_PREFIX = (
    "anime illustration, young woman, dark navy blue hair in a loose side braid, "
    "warm amber eyes, soft facial features, visual novel art style, high quality, detailed"
)

STATES = {
    "heavy_rain": {
        "prompt": "standing under a large red umbrella in heavy rain, puddles reflecting street lights",
        "quote": "Well... at least the plants are happy.",
    },
    "light_rain": {
        "prompt": "holding a small clear umbrella, light drizzle, overcast sky",
        "quote": "Might need an umbrella later. Just saying.",
    },
    "snow": {
        "prompt": "wrapped in a thick white scarf and coat, snowflakes falling gently around her",
        "quote": "Snow! Beautiful. I'm still not going outside.",
    },
    "freezing": {
        "prompt": "shivering in an oversized winter coat, cold breath visible, frost on the ground",
        "quote": "C-cold... why is it SO cold?!",
    },
    "cold": {
        "prompt": "wearing a cosy knit sweater and scarf, autumn leaves in the background",
        "quote": "Hot chocolate weather. Definitely.",
    },
    "mild": {
        "prompt": "sitting on a park bench with a light jacket, gentle breeze, soft daylight",
        "quote": "A perfect day, honestly.",
    },
    "warm": {
        "prompt": "relaxing at the beach in a sun hat and summer dress, calm ocean in the background",
        "quote": "The beach is calling my name~",
    },
    "hot": {
        "prompt": "fanning herself sitting under a palm tree, bright sunny sky, iced drink nearby",
        "quote": "This heat is absolutely unacceptable.",
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
        "prompt_suffix": "warm evening glow, sunset colours",
        "quote": "Almost time to wind down.",
    },
    "night": {
        "prompt_suffix": "night-time, city lights or stars visible",
        "quote": "It's getting late...",
    },
}

CALENDAR_STATES = {
    "in_meeting": {
        "prompt": "sitting at a desk focused on a laptop, video call on the screen, concentrated expression",
        "quote": "In a meeting. Please don't disturb me.",
    },
    "meeting_soon": {
        "prompt": "gathering papers and a notebook, looking at a wristwatch with mild urgency",
        "quote": "Meeting in a few minutes. Better get ready!",
    },
}

CONTEXT_STATES = {
    "welcome": {
        "prompt": "waving hello with a warm smile, standing in a bright doorway, welcoming gesture",
        "quote": "Welcome home!",
    },
    "poor_air": {
        "prompt": "indoors, covering her nose with a handkerchief, glancing at an air quality monitor with a worried expression",
        "quote": "Ugh, the air feels really stuffy...",
    },
    "indoor_hot": {
        "prompt": "indoors, fanning herself with a hand, looking flushed and uncomfortable, window in background",
        "quote": "It's so hot in here...",
    },
    "indoor_cold": {
        "prompt": "indoors, wrapped in a cosy blanket on a sofa, holding a warm mug, looking chilly",
        "quote": "Can someone turn the heating on?!",
    },
    "indoor_humid": {
        "prompt": "indoors, looking uncomfortable, slightly frizzy hair, cracking open a window",
        "quote": "So sticky in here. Please, some fresh air.",
    },
    "hub_offline": {
        "prompt": "sitting at a desk, staring at a dark unresponsive smart home panel with a puzzled and slightly worried expression",
        "quote": "Something's not responding... is the hub down?",
        "situation": "the smart home hub is offline and nothing is responding",
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
    },
    'hanukkah': {
        'prompt': 'standing beside a glowing menorah, soft candlelight, Star of David in background',
        'quote': 'Happy Hanukkah!',
        'situation': "it's Hanukkah",
    },
    'new_year': {
        'prompt': 'wearing a sparkly party hat, holding a glass of champagne, fireworks bursting through the window behind her',
        'quote': 'Happy New Year!',
        'situation': "it's New Year",
    },
    'halloween': {
        'prompt': 'wearing a cute witch hat and cape, holding a carved pumpkin lantern, spooky decorations in background',
        'quote': "Happy Halloween~",
        'situation': "it's Halloween",
    },
    'easter': {
        'prompt': 'in a spring meadow, holding a basket of colourful painted eggs, flowers blooming around her',
        'quote': 'Happy Easter!',
        'situation': "it's Easter",
    },
    'valentine': {
        'prompt': 'holding a bouquet of red roses, heart decorations, soft pink and red lighting',
        'quote': "Happy Valentine's Day~",
        'situation': "it's Valentine's Day",
    },
    'purim': {
        'prompt': 'wearing a colourful costume and masquerade mask, holding hamantaschen cookies, joyful festive atmosphere',
        'quote': 'Chag Purim Sameach!',
        'situation': "it's Purim",
    },
    'passover': {
        'prompt': 'at a beautifully set Passover seder table with a seder plate and matzah, warm candlelit atmosphere',
        'quote': 'Chag Pesach Sameach!',
        'situation': "it's Passover",
    },
    'rosh_hashana': {
        'prompt': 'holding a jar of honey and an apple, pomegranate and round challah on a festive table behind her',
        'quote': 'Shana Tova!',
        'situation': "it's Rosh Hashana, the Jewish New Year",
    },
    'yom_kippur': {
        'prompt': 'sitting quietly in thoughtful reflection, soft candlelight, serene and contemplative expression',
        'quote': 'Gmar Chatima Tova.',
        'situation': "it's Yom Kippur, a solemn Jewish day of atonement",
    },
    'diwali': {
        'prompt': 'surrounded by glowing oil diyas and colourful rangoli patterns, wearing traditional festive attire',
        'quote': 'Happy Diwali!',
        'situation': "it's Diwali",
    },
    'thanksgiving': {
        'prompt': 'sitting at a warm autumn feast table with a turkey and pumpkins, golden fall lighting',
        'quote': 'Happy Thanksgiving!',
        'situation': "it's Thanksgiving",
    },
    'lunar_new_year': {
        'prompt': 'wearing a red cheongsam, red lanterns and gold decorations in background, fireworks outside the window',
        'quote': 'Happy Lunar New Year!',
        'situation': "it's Lunar New Year",
    },
    'eid_fitr': {
        'prompt': 'wearing elegant festive attire, crescent moon and lanterns in background, warm celebratory atmosphere',
        'quote': 'Eid Mubarak!',
        'situation': "it's Eid al-Fitr",
    },
    'eid_adha': {
        'prompt': 'wearing elegant festive attire, crescent moon and stars in background, joyful and warm expression',
        'quote': 'Eid Mubarak!',
        'situation': "it's Eid al-Adha",
    },
}

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

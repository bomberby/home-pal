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

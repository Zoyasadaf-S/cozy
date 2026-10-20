"""Quick answers: time, maths, units, currency, definitions, weather, news, translation, briefing."""
from __future__ import annotations

import ast
import math
import operator
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from ..registry import Result, rule, tool
from ._util import q
from .web import open_url

WEATHER_API = "https://api.open-meteo.com/v1/forecast"
GEOCODE_API = "https://geocoding-api.open-meteo.com/v1/search"
CURRENCY_API = "https://api.frankfurter.dev/v1/latest"
DICTIONARY_API = "https://api.dictionaryapi.dev/api/v2/entries/en/"
NEWS_FEED = "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"

WMO = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy", 51: "light drizzle",
       53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
       75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "heavy showers", 95: "a thunderstorm", 96: "a thunderstorm with hail"}

LANGS = {"hindi": "hi", "kannada": "kn", "tamil": "ta", "telugu": "te", "marathi": "mr", "bengali": "bn", "gujarati": "gu",
         "punjabi": "pa", "urdu": "ur", "malayalam": "ml", "english": "en", "spanish": "es", "french": "fr", "german": "de",
         "japanese": "ja", "chinese": "zh-CN", "korean": "ko", "arabic": "ar", "russian": "ru", "italian": "it", "portuguese": "pt"}


@tool("tell_time", desc="say the current time")
def tell_time(ctx):
    return Result(True, "It's " + ctx.now().strftime("%I:%M %p").lstrip("0") + ".")


@tool("tell_date", desc="say today's date")
def tell_date(ctx):
    return Result(True, "Today is " + ctx.now().strftime("%A, %d %B %Y").replace(" 0", " ") + ".")


# ----------------------------------------------------------------------------- calculator (no eval)
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}


def safe_eval(expr: str) -> float:
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("exponent too large")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sqrt" and len(node.args) == 1:
            return math.sqrt(ev(node.args[0]))
        raise ValueError("unsupported expression")
    return ev(ast.parse(expr, mode="eval"))


def spoken_to_expr(text: str) -> str:
    t = text.lower().replace(",", "")
    t = re.sub(r"(\d+(?:\.\d+)?)\s*(?:percent|%)\s+of\s+(\d+(?:\.\d+)?)", r"(\1/100*\2)", t)
    t = re.sub(r"square root of\s+(\d+(?:\.\d+)?)", r"sqrt(\1)", t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s+squared", r"(\1**2)", t)
    for word, sym in (("to the power of", "**"), ("multiplied by", "*"), ("divided by", "/"), ("plus", "+"),
                      ("minus", "-"), ("times", "*"), ("over", "/"), ("mod", "%"), ("into", "*")):
        t = t.replace(word, sym)
    t = t.replace("^", "**").replace("×", "*").replace("÷", "/")
    t = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", t)
    return t.strip()


@tool("calculate", desc="do arithmetic", args='{"expr": "12 percent of 450"}')
def calculate(ctx, expr):
    try:
        value = safe_eval(spoken_to_expr(expr))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError):
        return Result(False, "I couldn't work that out. Try something like: calculate 12 times 7.")
    text = f"{value:.6g}" if isinstance(value, float) else str(value)
    return Result(True, f"{expr.strip()} is {text}.", speak=f"The answer is {text}.")


# ----------------------------------------------------------------------------- units and currency
_LINEAR = {  # to the base unit of each family
    "length": {"m": 1, "km": 1000, "cm": 0.01, "mm": 0.001, "mile": 1609.344, "yard": 0.9144, "foot": 0.3048, "inch": 0.0254},
    "mass": {"kg": 1, "g": 0.001, "mg": 1e-6, "pound": 0.45359237, "ounce": 0.0283495, "tonne": 1000},
    "volume": {"l": 1, "ml": 0.001, "gallon": 3.78541, "cup": 0.236588, "pint": 0.473176},
    "speed": {"mps": 1, "kmh": 1 / 3.6, "mph": 0.44704, "knot": 0.514444},
    "time": {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800},
}
_ALIASES = {"meter": "m", "metre": "m", "kilometer": "km", "kilometre": "km", "centimeter": "cm", "centimetre": "cm", "millimeter": "mm",
            "miles": "mile", "mi": "mile", "yards": "yard", "feet": "foot", "ft": "foot", "inches": "inch", "in": "inch",
            "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kilos": "kg", "gram": "g", "grams": "g", "lbs": "pound", "lb": "pound",
            "pounds": "pound", "oz": "ounce", "ounces": "ounce", "liter": "l", "litre": "l", "liters": "l", "litres": "l",
            "milliliter": "ml", "gallons": "gallon", "cups": "cup", "pints": "pint", "km/h": "kmh", "kph": "kmh", "kmph": "kmh",
            "m/s": "mps", "knots": "knot", "seconds": "second", "sec": "second", "minutes": "minute", "min": "minute",
            "hours": "hour", "hr": "hour", "days": "day", "weeks": "week", "celsius": "c", "fahrenheit": "f", "kelvin": "k",
            "°c": "c", "°f": "f", "tonnes": "tonne", "millimeters": "mm", "kilometers": "km", "meters": "m", "centimeters": "cm"}
_CURRENCY = {"usd": "USD", "dollar": "USD", "dollars": "USD", "inr": "INR", "rupee": "INR", "rupees": "INR", "eur": "EUR",
             "euro": "EUR", "euros": "EUR", "gbp": "GBP", "pound sterling": "GBP", "yen": "JPY", "jpy": "JPY", "aed": "AED",
             "dirham": "AED", "dirhams": "AED", "cad": "CAD", "aud": "AUD", "sgd": "SGD", "cny": "CNY", "yuan": "CNY"}


def _canon(unit: str) -> str:
    u = unit.strip().lower()
    return _ALIASES.get(u, u)


def _family(unit: str):
    for fam, table in _LINEAR.items():
        if unit in table:
            return fam
    return "temp" if unit in ("c", "f", "k") else None


def _temp(value: float, src: str, dst: str) -> float:
    c = value if src == "c" else (value - 32) * 5 / 9 if src == "f" else value - 273.15
    return c if dst == "c" else c * 9 / 5 + 32 if dst == "f" else c + 273.15


@tool("convert", desc="convert units or currencies", args='{"amount": 5, "src": "km", "dst": "miles"}')
def convert(ctx, amount, src, dst):
    amount = float(amount)
    s, d = _canon(src), _canon(dst)
    cs, cd = _CURRENCY.get(src.strip().lower()), _CURRENCY.get(dst.strip().lower())
    if cs and cd:
        data = ctx.http.get_json(f"{CURRENCY_API}?base={cs}&symbols={cd}", ttl=3600)
        rate = data["rates"][cd]
        return Result(True, f"{amount:g} {cs} is about {amount * rate:,.2f} {cd}.")
    fs, fd = _family(s), _family(d)
    if fs is None or fs != fd:
        return Result(False, f"I can't convert {src} to {dst}.")
    value = _temp(amount, s, d) if fs == "temp" else amount * _LINEAR[fs][s] / _LINEAR[fs][d]
    return Result(True, f"{amount:g} {src} is {value:.4g} {dst}.")


# ----------------------------------------------------------------------------- lookups (internet)
@tool("define_word", desc="define an English word", net=True, args='{"word": str}')
def define_word(ctx, word):
    word = word.strip().lower()
    try:
        data = ctx.http.get_json(DICTIONARY_API + quote(word), ttl=86400)
        meaning = data[0]["meanings"][0]
        text = f"{word}, {meaning['partOfSpeech']}: {meaning['definitions'][0]['definition']}"
    except (KeyError, IndexError, TypeError):
        return Result(False, f"I couldn't find a definition for {word}.")
    return Result(True, text)


def _weather_text(ctx, city: str) -> str:
    geo = ctx.http.get_json(f"{GEOCODE_API}?name={quote(city)}&count=1", ttl=86400)
    place = (geo.get("results") or [None])[0]
    if not place:
        raise KeyError(city)
    url = (f"{WEATHER_API}?latitude={place['latitude']}&longitude={place['longitude']}"
           "&current=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=auto&forecast_days=1")
    w = ctx.http.get_json(url, ttl=900)
    cur, day = w["current"], w["daily"]
    sky = WMO.get(cur.get("weather_code"), "mixed conditions")
    rain = day["precipitation_probability_max"][0]
    return (f"In {place['name']} it's {round(cur['temperature_2m'])}°C with {sky}. Today's range is "
            f"{round(day['temperature_2m_min'][0])} to {round(day['temperature_2m_max'][0])}°C, rain chance {rain}%.")


@tool("weather", desc="weather for a city", net=True, args='{"city": str}')
def weather(ctx, city=""):
    city = city.strip() or ctx.setting("city")
    if not city:
        return Result(False, "Which city? Say 'set my city to Bengaluru' once and I'll remember it.")
    try:
        return Result(True, _weather_text(ctx, city))
    except (KeyError, IndexError, TypeError):
        return Result(False, f"I couldn't find weather for {city}.")


def _headlines(ctx, n: int):
    root = ET.fromstring(ctx.http.get_bytes(NEWS_FEED, ttl=900))
    titles = [(i.findtext("title") or "").rsplit(" - ", 1)[0].strip() for i in root.iter("item")]
    return [t for t in titles if t][:n]


@tool("news", desc="read the top headlines", net=True)
def news(ctx):
    try:
        items = _headlines(ctx, 5)
    except ET.ParseError:
        return Result(False, "I couldn't read the news feed.")
    if not items:
        return Result(False, "I couldn't find any headlines.")
    return Result(True, "Top headlines: " + " ".join(f"{i}. {t}." for i, t in enumerate(items, 1)))


@tool("translate", desc="translate text into another language", args='{"text": str, "lang": "hindi"}')
def translate(ctx, text, lang):
    code = LANGS.get(lang.strip().lower())
    if not code:
        return Result(False, f"I don't know the language {lang}.")
    if ctx.llm is not None and ctx.llm.available():
        try:
            out = ctx.llm.chat([{"role": "system", "content": f"Translate to {lang}. Reply with only the translation."},
                                {"role": "user", "content": text}]).strip()
            return Result(True, out, speak=out)
        except Exception:
            pass
    if ctx.offline:
        return Result(False, "Offline mode is on and no local model is running, so I can't translate.")
    res = open_url(ctx, f"https://translate.google.com/?sl=auto&tl={code}&text={q(text)}&op=translate", label=f"Opening Google Translate for {lang}.")
    return res


# ----------------------------------------------------------------------------- morning briefing
@tool("good_morning", desc="daily briefing: date, weather, reminders, headlines")
def good_morning(ctx):
    now = ctx.now()
    hour = now.hour
    parts = [f"Good {'morning' if hour < 12 else 'afternoon' if hour < 17 else 'evening'}. It's {now.strftime('%A, %d %B').replace(' 0', ' ')}."]
    prof = ctx.profile()
    if prof.get("auto_defaults"):
        from .media import _apply_time_defaults
        _apply_time_defaults(ctx)
    city = ctx.setting("city")
    if city and not ctx.offline:
        try:
            parts.append(_weather_text(ctx, city))
        except Exception:
            pass
    today = [r for r in ctx.store.list_reminders() if r["kind"] == "reminder" and (r["due"] - ctx.clock()) < 86400]
    if today:
        parts.append(f"You have {len(today)} reminder{'s' if len(today) != 1 else ''} today, first: {today[0]['text']}.")
    if not ctx.offline:
        try:
            heads = _headlines(ctx, 3)
            if heads:
                parts.append("Headlines: " + " ".join(f"{t}." for t in heads))
        except Exception:
            pass
    return Result(True, " ".join(parts))


_E = r"[\d(.]"
rule(r"^(?:what(?:'s| is)\s+(?:the\s+)?(?:current\s+)?time(?:\s+(?:now|right now))?|what\s+time\s+is\s+it|time)$", "tell_time", priority=20)
rule(r"^(?:what(?:'s| is)\s+(?:the\s+|today'?s\s+)?(?:date|day)(?:\s+today)?|what\s+day\s+is\s+it)$", "tell_date", priority=20)
rule(r"^(?:what(?:'s| is)|how (?:much|many) is)?\s*(?P<amount>-?\d+(?:\.\d+)?)\s*(?P<src>[a-z°/]+(?:\s[a-z]+)?)\s+(?:to|in|into)\s+(?P<dst>[a-z°/]+(?:\s[a-z]+)?)$",
     "convert", conv={"amount": float}, priority=20)
rule(r"^convert\s+(?P<amount>-?\d+(?:\.\d+)?)\s*(?P<src>[a-z°/]+(?:\s[a-z]+)?)\s+(?:to|in|into)\s+(?P<dst>[a-z°/]+(?:\s[a-z]+)?)$",
     "convert", conv={"amount": float}, priority=20)
rule(r"^(?:calculate|compute|calc)\s+(?P<expr>.+)$", "calculate", priority=30)
rule(r"^(?:what(?:'s| is)|how much is)\s+(?P<expr>(?:\d|square root|\().*)$", "calculate", priority=35)
rule(r"^(?:define|meaning of|what(?:'s| is) the meaning of)\s+(?P<word>[a-z'-]+)$", "define_word", priority=30)
rule(r"^(?:what(?:'s| is)\s+the\s+)?weather(?:\s+(?:like\s+)?(?:today|now|right now))?(?:\s+in\s+(?P<city>.+))?$", "weather", priority=30)
rule(r"^how(?:'s| is)\s+the\s+weather(?:\s+in\s+(?P<city>.+))?$", "weather", priority=30)
rule(r"^(?:(?:what(?:'s| is)|give me|read)\s+(?:the\s+)?)?(?:latest\s+)?(?:news|headlines)(?:\s+(?:today|now))?$", "news", priority=30)
rule(r"^translate\s+(?P<text>.+?)\s+(?:to|into|in)\s+(?P<lang>[a-z]+)$", "translate", priority=20)
rule(r"^(?:good\s+morning|morning\s+briefing|daily\s+briefing|(?:give\s+me\s+)?(?:my|the)\s+(?:daily\s+)?briefing|brief\s+me)$", "good_morning", priority=20)

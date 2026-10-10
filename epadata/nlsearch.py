"""Turns an everyday sentence into search conditions.

Example: "Find coal-fired units in Kentucky in 2025 with annual CO2 emissions greater than
500,000 short tons" becomes
    fuel = coal, state = KY, year = 2025, CO2 mass > 500,000.

It uses plain pattern matching, not AI, so the same sentence always gives the same answer.
Whatever it does not recognise (for example a facility name) is left over as ordinary words
and matched by the full-text index. search.py turns the conditions into a Whoosh query.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

CURRENT_YEAR = datetime.now().year

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "PR": "Puerto Rico", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

# What can be measured: index field -> (label, words people use for it, unit shown to the user)
METRICS = {
    "co2_mass": ("CO₂", ["co2", "carbon dioxide"], "short tons"),
    "so2_mass": ("SO₂", ["so2", "sulfur dioxide", "sulphur dioxide"], "short tons"),
    "nox_mass": ("NOx", ["nox", "nitrogen oxides", "nitrogen oxide"], "short tons"),
    "heat_input": ("Heat input", ["heat input"], "MMBtu"),
    "gross_load": ("Gross load", ["gross load"], "MWh"),
    "steam_load": ("Steam load", ["steam load"], "1000 lb"),
    "operating_time": (
        "Operating hours",
        ["operating hours", "operating time", "hours of operation"],
        "hours",
    ),
}

# Fuel words -> the phrase looked up in the unit's primary fuel. Order matters: longest first.
_FUELS = [
    (r"(?:pipeline\s+)?natural[- ]gas|pipeline\s+gas", "natural gas"),
    (r"petroleum[- ]coke|pet[- ]coke", "petroleum coke"),
    (r"diesel(?:[- ]oil)?", "diesel"),
    (r"coal", "coal"),
    (r"oil", "oil"),
    (r"gas", "gas"),
    (r"wood", "wood"),
]

# Words that only make a sentence read naturally. They are ignored.
_STOP = set(
    "find show list get give display search me us all any every each the a an of in at on "
    "for with that which who have has having where whose what are is was were be been and "
    "to by from than units unit emissions emission annual annually year years during using "
    "burning fired fueled tons ton short per their its there please can you i want need "
    "looking look".split()
)

_MULTIPLIERS = {"thousand": 1e3, "k": 1e3, "million": 1e6, "billion": 1e9}

# ---- patterns -----------------------------------------------------------------------

_METRIC_WORDS = {w: f for f, (_, words, _) in METRICS.items() for w in words}
_METRIC = "|".join(sorted(map(re.escape, _METRIC_WORDS), key=len, reverse=True))

_FILLER = (
    r"(?:\s+(?:annual|annually|yearly|emissions?|emitted|emitting|mass|output|total|of|"
    r"per\s+year|a\s+year|is|are|was|were|being|level|levels|rate))*"
)
_CMP = (
    r"(?:(?P<gte>at\s+least|no\s+less\s+than|not\s+less\s+than|minimum\s+of|"
    r"greater\s+than\s+or\s+equal\s+to|at\s+or\s+above|>=|≥)"
    r"|(?P<lte>at\s+most|no\s+more\s+than|not\s+more\s+than|maximum\s+of|"
    r"less\s+than\s+or\s+equal\s+to|at\s+or\s+below|up\s+to|<=|≤)"
    r"|(?P<gt>greater\s+than|more\s+than|higher\s+than|larger\s+than|bigger\s+than|"
    r"\bover|\babove|\bexceeding|\bexceeds?|>)"
    r"|(?P<lt>less\s+than|fewer\s+than|lower\s+than|smaller\s+than|\bunder|\bbelow|<))"
)


def _number(suffix=""):
    return (
        rf"(?P<num{suffix}>\d[\d,]*(?:\.\d+)?)"
        rf"(?:\s*(?P<mult{suffix}>thousand|million|billion|k)\b)?"
    )


_UNIT = r"(?:\s*(?:short\s+tons?|metric\s+tons?|tonnes?|tons?|tpy|mmbtus?|mwh|hours?|hrs?)\b)?"

_METRIC_FIRST = re.compile(
    rf"\b(?P<metric>{_METRIC}){_FILLER}\s*{_CMP}\s*{_number()}{_UNIT}"
)
_CMP_FIRST = re.compile(
    rf"{_CMP}\s*{_number()}{_UNIT}(?:\s+of)?(?:\s+(?:annual|total))?\s+(?P<metric>{_METRIC})\b"
)
_BETWEEN = re.compile(
    rf"\b(?P<metric>{_METRIC}){_FILLER}\s*(?:is\s+)?between\s*{_number('_a')}\s*"
    rf"(?:and|to|-)\s*{_number('_b')}{_UNIT}"
)
_FUEL = re.compile(
    r"\b(?:(?:fueled|fired|powered|run|running)\s+(?:by|on|with)\s+|"
    r"(?:burning|burns?|using|uses?)\s+)?"
    rf"(?P<fuel>{'|'.join(f for f, _ in _FUELS)})(?:[- ](?:fired|burning|powered|fueled))?\b"
)
_STATE_NAME = re.compile(
    r"\b("
    + "|".join(sorted((re.escape(n.lower()) for n in STATE_NAMES.values()), key=len, reverse=True))
    + r")\b"
)
_NAME_TO_CODE = {name.lower(): code for code, name in STATE_NAMES.items()}
_CODE = re.compile(r"\b([A-Z]{2})\b")
_CODES_NOT_USED = {"IN", "OR", "ME", "HI", "ID"}  # too easy to mean the ordinary words "in" / "or"

_YEAR_RANGE = re.compile(
    r"\b(?:(?:from|between)\s+)?((?:19|20)\d{2})\s*(?:-|to|through|thru|until|and)\s*((?:19|20)\d{2})\b"
)
_YEAR_SINCE = re.compile(r"\b(since|after|before)\s+((?:19|20)\d{2})\b")
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")

# Text that already uses search syntax (quotes, field:value, OR, NOT, *, ~, [a TO b]) is
# passed straight to the full-text search instead of being interpreted as a sentence.
_ADVANCED = re.compile(r'["\[\]*~:]|\b(?:OR|AND|NOT)\b')


@dataclass
class Condition:
    """One numeric condition, e.g. CO2 mass > 500,000."""

    field: str
    lo: float | None = None
    hi: float | None = None
    lo_excl: bool = False
    hi_excl: bool = False

    def describe(self) -> str:
        label, _, unit = METRICS[self.field]
        if self.lo is not None and self.hi is not None:
            return f"{label} {_fmt(self.lo)} to {_fmt(self.hi)} {unit}"
        if self.lo is not None:
            return f"{label} {'>' if self.lo_excl else '≥'} {_fmt(self.lo)} {unit}"
        return f"{label} {'<' if self.hi_excl else '≤'} {_fmt(self.hi)} {unit}"


@dataclass
class Understanding:
    fuels: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    numbers: list[Condition] = field(default_factory=list)
    words: str = ""  # everything else, matched as ordinary words

    @property
    def found(self) -> bool:
        return bool(self.fuels or self.states or self.years or self.numbers)

    def chips(self) -> list[str]:
        """Short labels shown to the user so they can see how the sentence was read."""
        chips = [f"Fuel: {fuel}" for fuel in self.fuels]
        chips += [f"State: {STATE_NAMES[code]} ({code})" for code in self.states]
        if self.years:
            first, last = self.years[0], self.years[-1]
            consecutive = last - first + 1 == len(self.years)
            chips.append(
                f"Year: {first}–{last}" if consecutive and len(self.years) > 1
                else "Year: " + ", ".join(map(str, self.years))
            )
        chips += [c.describe() for c in self.numbers]
        if self.words:
            chips.append(f"Words: {self.words}")
        return chips


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _value(match, suffix="") -> float:
    number = float(match.group(f"num{suffix}").replace(",", "").rstrip("."))
    multiplier = match.group(f"mult{suffix}")
    return number * _MULTIPLIERS.get(multiplier, 1)


def _normalise(text: str) -> str:
    """Make CO₂ -> CO2, curly dashes -> '-', and squash odd spaces."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[‐-―−]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def _take_numbers(text: str):
    """Pull out conditions like 'CO2 emissions greater than 500,000 short tons'."""
    found = []

    def between(match):
        low, high = sorted((_value(match, "_a"), _value(match, "_b")))
        found.append(Condition(_METRIC_WORDS[match["metric"]], lo=low, hi=high))
        return " "

    def compare(match):
        value = _value(match)
        if match["gte"] or match["gt"]:
            cond = Condition(_METRIC_WORDS[match["metric"]], lo=value, lo_excl=bool(match["gt"]))
        else:
            cond = Condition(_METRIC_WORDS[match["metric"]], hi=value, hi_excl=bool(match["lt"]))
        found.append(cond)
        return " "

    for pattern, handler in ((_BETWEEN, between), (_METRIC_FIRST, compare), (_CMP_FIRST, compare)):
        text = pattern.sub(handler, text)
    return text, found


def _take_years(text: str):
    years: set[int] = set()

    def in_range(low, high):
        return [y for y in range(low, high + 1) if 1990 <= y <= CURRENT_YEAR + 1]

    def span(match):
        low, high = sorted((int(match.group(1)), int(match.group(2))))
        if high - low <= 60:
            years.update(in_range(low, high))
        return " "

    def since(match):
        word, year = match.group(1), int(match.group(2))
        if word == "since":
            years.update(in_range(year, CURRENT_YEAR))
        elif word == "after":
            years.update(in_range(year + 1, CURRENT_YEAR))
        else:
            years.update(in_range(1995, year - 1))
        return " "

    def single(match):
        years.update(in_range(int(match.group(1)), int(match.group(1))))
        return " "

    for pattern, handler in ((_YEAR_RANGE, span), (_YEAR_SINCE, since), (_YEAR, single)):
        text = pattern.sub(handler, text)
    return text, sorted(years)


def interpret(text: str) -> Understanding | None:
    """Read a sentence. Returns None if the text already uses search syntax."""
    if _ADVANCED.search(text):
        return None
    result = Understanding()
    text = _normalise(text)

    # States: two-letter codes must be written in capitals (KY); full names can be any case.
    def code(match):
        if match.group(1) in STATE_NAMES and match.group(1) not in _CODES_NOT_USED:
            result.states.append(match.group(1))
            return " "
        return match.group(0)

    text = _CODE.sub(code, text).lower()

    def state_name(match):
        result.states.append(_NAME_TO_CODE[match.group(1)])
        return " "

    text = _STATE_NAME.sub(state_name, text)

    text, result.numbers = _take_numbers(text)
    text, result.years = _take_years(text)

    def fuel(match):
        for pattern, phrase in _FUELS:
            if re.fullmatch(pattern, match.group("fuel")):
                if phrase not in result.fuels:
                    result.fuels.append(phrase)
                break
        return " "

    text = _FUEL.sub(fuel, text)

    result.states = list(dict.fromkeys(result.states))
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9'./-]*", text) if w not in _STOP]
    result.words = " ".join(words)
    return result
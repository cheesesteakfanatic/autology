"""Unit registry for §3.2 dimension inference.

Every unit is a contracts.UnitDef: (symbol, Dimension, affine conversion to the
canonical unit of that dimension). Canonical units: m (length), kg (mass),
s (time), m/s (speed), K (temperature), m3 (volume), count, % -> 1 (dimensionless
ratio). Currencies are dimension CURRENCY with scale 1.0 to *themselves* — FX rates
are time-varying market data, not static conversions, so cross-currency conversion
is explicitly out of scope here (ANVIL synthesizes rate-joined transforms instead).

ALIASES maps lowercase name/suffix tokens to unit symbols. Ambiguous tokens carry a
context requirement (other tokens that must appear in the column name) and a base
confidence < 1: 'f' alone is not Fahrenheit, but 'temp_f' is; bare 'min' is more
often "minimum" than "minutes". Aviation conventions are the default where corpora
collide ('nm' = nautical mile, not nanometer — the hero estate is aviation, §17.2.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ontoforge.contracts import (
    COUNT,
    CURRENCY,
    DIMENSIONLESS,
    LENGTH,
    MASS,
    SPEED,
    TEMPERATURE,
    TIME,
    Dimension,
    UnitDef,
    dim,
)

__all__ = ["UNITS", "ALIASES", "UnitAlias", "lookup_unit", "resolve_token", "units_in_dimension"]

VOLUME = dim(m=3)

_DEFS: tuple[UnitDef, ...] = (
    # length (canonical m)
    UnitDef("m", LENGTH, 1.0, 0.0, "m"),
    UnitDef("ft", LENGTH, 0.3048, 0.0, "m"),
    UnitDef("km", LENGTH, 1000.0, 0.0, "m"),
    UnitDef("mi", LENGTH, 1609.344, 0.0, "m"),
    UnitDef("nm", LENGTH, 1852.0, 0.0, "m"),          # nautical mile (aviation default)
    UnitDef("cm", LENGTH, 0.01, 0.0, "m"),
    UnitDef("in", LENGTH, 0.0254, 0.0, "m"),
    # speed (canonical m/s)
    UnitDef("m/s", SPEED, 1.0, 0.0, "m/s"),
    UnitDef("kt", SPEED, 0.514444, 0.0, "m/s"),
    UnitDef("mph", SPEED, 0.44704, 0.0, "m/s"),
    UnitDef("km/h", SPEED, 0.2777778, 0.0, "m/s"),
    UnitDef("fpm", SPEED, 0.00508, 0.0, "m/s"),        # feet per minute (climb rate)
    # mass (canonical kg)
    UnitDef("kg", MASS, 1.0, 0.0, "kg"),
    UnitDef("lb", MASS, 0.45359237, 0.0, "kg"),
    UnitDef("g", MASS, 0.001, 0.0, "kg"),
    UnitDef("t", MASS, 1000.0, 0.0, "kg"),
    UnitDef("oz", MASS, 0.028349523125, 0.0, "kg"),
    # temperature (canonical K) — affine
    UnitDef("K", TEMPERATURE, 1.0, 0.0, "K"),
    UnitDef("C", TEMPERATURE, 1.0, 273.15, "K"),
    UnitDef("F", TEMPERATURE, 5.0 / 9.0, 255.37222222222223, "K"),
    # time (canonical s)
    UnitDef("s", TIME, 1.0, 0.0, "s"),
    UnitDef("ms", TIME, 0.001, 0.0, "s"),
    UnitDef("min", TIME, 60.0, 0.0, "s"),
    UnitDef("h", TIME, 3600.0, 0.0, "s"),
    UnitDef("day", TIME, 86400.0, 0.0, "s"),
    # currency (no FX: canonical = self)
    UnitDef("USD", CURRENCY, 1.0, 0.0, "USD"),
    UnitDef("EUR", CURRENCY, 1.0, 0.0, "EUR"),
    UnitDef("GBP", CURRENCY, 1.0, 0.0, "GBP"),
    # volume (canonical m3)
    UnitDef("m3", VOLUME, 1.0, 0.0, "m3"),
    UnitDef("L", VOLUME, 0.001, 0.0, "m3"),
    UnitDef("gal", VOLUME, 0.003785411784, 0.0, "m3"),
    # pseudo-dimensions
    UnitDef("count", COUNT, 1.0, 0.0, "count"),
    UnitDef("%", DIMENSIONLESS, 0.01, 0.0, "1"),
)

UNITS: dict[str, UnitDef] = {u.symbol: u for u in _DEFS}


@dataclass(frozen=True, slots=True)
class UnitAlias:
    symbol: str                       # key into UNITS
    confidence: float = 0.9           # prior that this token really means the unit
    requires: tuple[str, ...] = ()    # context substrings that must appear elsewhere in the name


ALIASES: dict[str, UnitAlias] = {
    # length
    "ft": UnitAlias("ft"), "feet": UnitAlias("ft"), "foot": UnitAlias("ft"),
    "m": UnitAlias("m", 0.55), "meter": UnitAlias("m"), "meters": UnitAlias("m"),
    "metre": UnitAlias("m"), "metres": UnitAlias("m"),
    "km": UnitAlias("km"), "mi": UnitAlias("mi", 0.7), "mile": UnitAlias("mi"), "miles": UnitAlias("mi"),
    "nm": UnitAlias("nm", 0.7), "cm": UnitAlias("cm"),
    "in": UnitAlias("in", 0.4, ("length", "width", "height", "size", "diameter")),
    "inch": UnitAlias("in"), "inches": UnitAlias("in"),
    # speed
    "kt": UnitAlias("kt"), "kts": UnitAlias("kt"), "knot": UnitAlias("kt"), "knots": UnitAlias("kt"),
    "mph": UnitAlias("mph"), "mps": UnitAlias("m/s"), "m/s": UnitAlias("m/s"),
    "kph": UnitAlias("km/h"), "km/h": UnitAlias("km/h"), "fpm": UnitAlias("fpm"),
    # mass
    "kg": UnitAlias("kg"), "kgs": UnitAlias("kg"), "kilogram": UnitAlias("kg"), "kilograms": UnitAlias("kg"),
    "lb": UnitAlias("lb"), "lbs": UnitAlias("lb"), "pound": UnitAlias("lb"), "pounds": UnitAlias("lb"),
    "g": UnitAlias("g", 0.4, ("weight", "mass")), "oz": UnitAlias("oz", 0.7),
    # temperature (single letters demand a temperature-ish context)
    "f": UnitAlias("F", 0.85, ("temp", "temperature", "deg")),
    "fahrenheit": UnitAlias("F", 0.95), "degf": UnitAlias("F", 0.95), "°f": UnitAlias("F", 0.95),
    "c": UnitAlias("C", 0.85, ("temp", "temperature", "deg")),
    "celsius": UnitAlias("C", 0.95), "degc": UnitAlias("C", 0.95), "°c": UnitAlias("C", 0.95),
    "k": UnitAlias("K", 0.7, ("temp", "temperature", "kelvin")), "kelvin": UnitAlias("K", 0.95),
    # time
    "s": UnitAlias("s", 0.4, ("time", "duration", "elapsed", "sec")),
    "sec": UnitAlias("s", 0.8), "secs": UnitAlias("s", 0.8), "seconds": UnitAlias("s"),
    "ms": UnitAlias("ms", 0.6, ("time", "duration", "latency", "elapsed")),
    "min": UnitAlias("min", 0.5, ("time", "duration", "elapsed", "block")),
    "mins": UnitAlias("min", 0.8), "minutes": UnitAlias("min"),
    "h": UnitAlias("h", 0.4, ("time", "duration", "block", "flight")),
    "hr": UnitAlias("h", 0.85), "hrs": UnitAlias("h", 0.85), "hour": UnitAlias("h"), "hours": UnitAlias("h"),
    "day": UnitAlias("day", 0.7), "days": UnitAlias("day", 0.7),
    # currency
    "usd": UnitAlias("USD"), "dollar": UnitAlias("USD", 0.85), "dollars": UnitAlias("USD", 0.85),
    "$": UnitAlias("USD", 0.9),
    "eur": UnitAlias("EUR"), "euro": UnitAlias("EUR", 0.85), "euros": UnitAlias("EUR", 0.85),
    "€": UnitAlias("EUR", 0.9),
    "gbp": UnitAlias("GBP"), "£": UnitAlias("GBP", 0.9),
    # volume
    "l": UnitAlias("L", 0.5, ("fuel", "volume", "capacity")), "liter": UnitAlias("L"),
    "liters": UnitAlias("L"), "litre": UnitAlias("L"), "litres": UnitAlias("L"),
    "gal": UnitAlias("gal", 0.8), "gals": UnitAlias("gal", 0.8), "gallons": UnitAlias("gal"),
    # count / dimensionless
    "count": UnitAlias("count", 0.8), "cnt": UnitAlias("count", 0.7), "qty": UnitAlias("count", 0.8),
    "quantity": UnitAlias("count", 0.8), "num": UnitAlias("count", 0.5), "ea": UnitAlias("count", 0.6),
    "pct": UnitAlias("%", 0.85), "percent": UnitAlias("%", 0.9), "%": UnitAlias("%", 0.9),
}


def lookup_unit(symbol: str) -> Optional[UnitDef]:
    return UNITS.get(symbol)


def resolve_token(token: str, context: str = "") -> Optional[tuple[UnitDef, float]]:
    """Resolve one lowercase token to (UnitDef, confidence), honoring context gates."""
    alias = ALIASES.get(token.lower())
    if alias is None:
        return None
    if alias.requires and not any(req in context.lower() for req in alias.requires):
        return None
    return UNITS[alias.symbol], alias.confidence


def units_in_dimension(d: Dimension) -> list[UnitDef]:
    return [u for u in UNITS.values() if u.dimension == d]

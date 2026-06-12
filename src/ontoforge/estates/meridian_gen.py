"""MERIDIAN demo corpus generator — a 10-table cross-system synthetic
enterprise estate (ERP purchasing, contracts, quality, PLM/BOM, CRE leases,
logistics, HR, retail POS, CRM support) for the GENERIC engine demo.

Meridian Devices is a fictional consumer-electronics company. The corpus is
generated from ONE seeded RNG (``SEED = 7``) so the CSVs and the gold answers
are byte-reproducible. The entity graph (suppliers, facilities, products,
components) is built FIRST; every table is emitted from it, so cross-system
overlap is by construction, not by coincidence:

- ~226 canonical suppliers: 200 contracted (top 40 with amendments + expired
  predecessors), 20 PO-only tail vendors, 6 deliberate quality-only orphans;
- 120 active facilities (90 retail, 14 DCs, 6 offices, 4 labs, 6 data
  centers) + 3 3PL sites with headcount but NO lease (explainable orphans);
- ~70 product models x ~4-5 sellable variants, BOM components shared across
  models, serial-number prefixes encoding the model code.

Messy-data warts (each with a stated rate AND a deterministic recovery rule,
documented in ``gold/questions.yaml`` conventions):

- supplier-name spelling variants on PO lines (~9% of non-hero rows) and in
  quality notifications (3-5 variants per top supplier, name-only table);
- VENDOR_ID leading zeros dropped on the PO rows of 12 designated mid-tail
  vendors (recover: zero-pad to 10);
- kg/lb mixed weights (suffix + WEIGHT_UOM mirror), SF/SQM areas with ~5%
  blank AREA_UOM (recover: country convention), EU decimal-comma POS amounts
  with a clean NET_SALES_USD bridge, 'USD ' prefixes on ~30% of NET_VALUE_USD;
- date locales: ISO in masters, DD.MM.YYYY lease commencements on EU/CN rows,
  DD-MON-YY Oracle-style shipment dates, ISO-8601 mixed-offset ticket stamps;
- null-token zoo ('', 'NULL', 'N/A', '-', 'TBD') + 9999-12-31 sentinels;
- the SAP unit trap: ORDER_UOM=KPC (thousand pieces) and PRICE_UNIT semantics
  on PO lines (CMP-DSP-0451 mixes EA and KPC deliberately);
- double-entered events (re-keyed near-identical rows) in quality and
  shipments; exact duplicate rows in the keyless snapshot tables (POS,
  headcount); one mojibake city cell; ~2% dangling shipment PO references.

HERO-CLEANLINESS RULE: every entity a gold question references is wart-free on
its join/filter values, so the pinned gold answers are simultaneously the
TRUE answers and the answers a correct engine can compute. Warts concentrate
on non-hero rows; every wart class still ships a documented recovery rule.

Gold answers are computed with pandas from the EMITTED string frames (re-parsed
exactly like the conformance layer parses them) and pinned into
``gold/questions.yaml`` — never hardcoded.

This module lives inside the package (not in scripts/) so ``ontoforge demo
meridian`` can regenerate the corpus from an installed wheel; the repo keeps
``scripts/build_meridian_corpus.py`` as a thin shim.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from random import Random
from typing import Any, Mapping, Optional

import pandas as pd

from ontoforge.estates import yamlite

__all__ = [
    "SEED",
    "FIXTURE_FILES",
    "build_corpus",
    "build_frames",
    "compute_gold",
    "gold_questions",
    "main",
]

SEED = 7

FIXTURE_FILES = (
    "supplier_contracts.csv",
    "purchase_order_lines.csv",
    "quality_notifications.csv",
    "products.csv",
    "bom_components.csv",
    "leases.csv",
    "shipments.csv",
    "site_headcount.csv",
    "retail_pos_sales.csv",
    "support_tickets.csv",
)

MAX_TOTAL_BYTES = 8 * 1024 * 1024

# --------------------------------------------------------------------- pools

_SUPPLIER_W1 = [
    "Hailong", "Suzhou Brightway", "Kunshan", "Volta", "Taoyuan", "Nagoya",
    "Dongguan Rixin", "Hsinchu", "Pusan", "Hanoi Lotus", "Ningbo Anchor",
    "Chengdu Skylark", "Osaka Meiwa", "Gumi", "Da Nang", "Xiamen Harbor",
    "Wuxi Crane", "Tainan", "Yokohama Kiyo", "Incheon", "Qingdao Pearl",
    "Zhuhai Coral", "Kaohsiung", "Sendai", "Ulsan", "Haiphong", "Foshan Ridge",
    "Taichung", "Kyoto Hana", "Daegu", "Binh Duong", "Hefei Summit",
    "Hangzhou Lake", "Keelung", "Nara", "Suwon", "Can Tho", "Tianjin Gate",
    "Shaoxing", "Chiayi", "Kobe Mira", "Gwangju", "Vinh Phuc", "Zhongshan",
    "Changshu", "Miaoli", "Fukuoka", "Jeonju", "Quang Nam", "Dalian Crest",
    "Jiaxing", "Pingtung", "Sapporo", "Cheonan", "Long An", "Nantong",
    "Yilan",
]
_SUPPLIER_W2 = [
    "Precision", "Electronics", "Polymer", "Energy", "Optics", "Metalworks",
    "Microsystems", "Circuit", "Display", "Acoustics", "Tooling", "Components",
]
_SUPPLIER_COUNTRIES = ["CN", "TW", "JP", "KR", "VN", "CN", "TW", "US"]
_LEGAL_FORM = {
    "CN": "Industry Co., Ltd.",
    "TW": "Industrial Corp.",
    "JP": "Corporation",
    "KR": "Co., Ltd.",
    "VN": "Joint Stock Company",
    "US": "Inc.",
}
_GOVERNING_LAW = {
    "CN": "PRC", "TW": "Taiwan", "JP": "Japan", "KR": "South Korea",
    "VN": "Vietnam", "US": "New York",
}
_BUYER = ["Meridian Devices Inc.", "Meridian International B.V.", "Meridian Trading (Shanghai) Co."]
_PAYMENT_TERMS = ["NET30", "NET45", "NET60", "2/10NET30"]
_INCOTERMS = ["FOB", "FCA", "EXW", "CIF", "DDP", "DAP"]
_DOC_CURRENCY = {"CN": "CNY", "TW": "TWD", "JP": "JPY", "KR": "USD", "VN": "USD", "US": "USD"}
_FX_TO_USD = {"USD": 1.0, "CNY": 1.0 / 7.2, "TWD": 1.0 / 32.0, "JPY": 1.0 / 155.0, "EUR": 1.0 / 0.92}

_CATEGORIES = {
    "BATTERY": ("BAT", (6.0, 16.0)),
    "DISPLAY": ("DSP", (24.0, 85.0)),
    "PCBA": ("PCB", (30.0, 120.0)),
    "CAMERA": ("CAM", (8.0, 42.0)),
    "ENCLOSURE": ("ENC", (2.0, 12.0)),
    "FASTENER": ("FST", (0.02, 0.30)),
    "ADHESIVE": ("ADH", (0.8, 5.0)),
    "CABLE": ("CBL", (0.4, 3.5)),
}
_CAT_WORDS = {
    "BATTERY": "Li-ion battery pack",
    "DISPLAY": "OLED display module",
    "PCBA": "main logic board assembly",
    "CAMERA": "camera module",
    "ENCLOSURE": "machined housing",
    "FASTENER": "torx screw",
    "ADHESIVE": "structural adhesive",
    "CABLE": "flex ribbon cable",
}

_FAMILIES = ["PULSE", "AURA", "SLATE", "HALO", "CORE", "ACCESSORY"]
_MODEL_SPECS = [
    # (family, model_code, marketing name, has_storage)
    ("PULSE", "PLS7", "Pulse 7", True), ("PULSE", "PLS7P", "Pulse 7 Pro", True),
    ("PULSE", "PLS8", "Pulse 8", True), ("PULSE", "PLS8P", "Pulse 8 Pro", True),
    ("PULSE", "PLS8L", "Pulse 8 Lite", True), ("PULSE", "PLS9", "Pulse 9", True),
    ("PULSE", "PLS9P", "Pulse 9 Pro", True), ("PULSE", "PLS9U", "Pulse 9 Ultra", True),
    ("PULSE", "PLS9L", "Pulse 9 Lite", True), ("PULSE", "PLSXF", "Pulse X Fold", True),
    ("PULSE", "PLS6", "Pulse 6", True), ("PULSE", "PLS6P", "Pulse 6 Pro", True),
    ("AURA", "ARA13", "Aura 13", True), ("AURA", "ARA14", "Aura 14", True),
    ("AURA", "ARA15", "Aura 15", True), ("AURA", "ARA15P", "Aura 15 Pro", True),
    ("AURA", "ARA16", "Aura 16", True), ("AURA", "ARA16P", "Aura 16 Pro", True),
    ("AURA", "ARAST", "Aura Studio", True), ("AURA", "ARAGO", "Aura Go", True),
    ("AURA", "ARA13P", "Aura 13 Pro", True), ("AURA", "ARA14P", "Aura 14 Pro", True),
    ("SLATE", "SLT8", "Slate 8", True), ("SLATE", "SLT9", "Slate 9", True),
    ("SLATE", "SLT10", "Slate 10", True), ("SLATE", "SLT10P", "Slate 10 Pro", True),
    ("SLATE", "SLT11", "Slate 11", True), ("SLATE", "SLTMN", "Slate Mini", True),
    ("SLATE", "SLT11P", "Slate 11 Pro", True), ("SLATE", "SLTKD", "Slate Kids", True),
    ("HALO", "HLOB1", "Halo Buds", False), ("HALO", "HLOB2", "Halo Buds 2", False),
    ("HALO", "HLOB2P", "Halo Buds 2 Pro", False), ("HALO", "HLOMX", "Halo Max", False),
    ("HALO", "HLOMN", "Halo Mini", False), ("HALO", "HLOSB", "Halo Soundbar", False),
    ("HALO", "HLOST", "Halo Studio", False), ("HALO", "HLOGO", "Halo Go", False),
    ("HALO", "HLOA1", "Halo Arc", False), ("HALO", "HLOH1", "Halo Home", False),
    ("HALO", "HLOB3", "Halo Buds 3", False), ("HALO", "HLOFT", "Halo Fit", False),
    ("CORE", "CRW2", "Core Watch 2", False), ("CORE", "CRW3", "Core Watch 3", False),
    ("CORE", "CRW3P", "Core Watch 3 Pro", False), ("CORE", "CRW4", "Core Watch 4", False),
    ("CORE", "CRBND", "Core Band", False), ("CORE", "CRBN2", "Core Band 2", False),
    ("CORE", "CRRNG", "Core Ring", False), ("CORE", "CRW4P", "Core Watch 4 Pro", False),
    ("CORE", "CRKID", "Core Kids", False), ("CORE", "CRSNS", "Core Sense", False),
    ("ACCESSORY", "ACCHG", "PowerHub Charger", False), ("ACCESSORY", "ACDCK", "HaloPad Charging Dock", False),
    ("ACCESSORY", "ACCSE", "Pulse Folio Case", False), ("ACCESSORY", "ACKBD", "Slate Keyboard", False),
    ("ACCESSORY", "ACPEN", "Slate Pen", False), ("ACCESSORY", "ACCBL", "DuraLink Cable", False),
    ("ACCESSORY", "ACADP", "Travel Adapter", False), ("ACCESSORY", "ACSTR", "Watch Strap", False),
    ("ACCESSORY", "ACMNT", "Car Mount", False), ("ACCESSORY", "ACPWB", "PowerBank 10K", False),
    ("ACCESSORY", "ACSLV", "Aura Sleeve", False), ("ACCESSORY", "ACSCR", "Screen Shield", False),
    ("ACCESSORY", "ACHUB", "USB-C Hub", False), ("ACCESSORY", "ACBAG", "Commuter Bag", False),
    ("ACCESSORY", "ACDST", "Desk Stand", False), ("ACCESSORY", "ACEAR", "Halo Ear Tips", False),
    ("ACCESSORY", "ACLNS", "Lens Protector", False), ("ACCESSORY", "ACWCH", "Watch Charger", False),
]
# BOM rows exist for hardware families; 4 phantom next-gen programs keep the
# parent-model domain from being a perfect subset of the product master.
_PHANTOM_MODELS = ["NOVA1", "NOVA2", "VEGA1", "VEGA2"]

_COLORS = [("BLK", "Black"), ("SLV", "Silver"), ("BLU", "Blue"), ("GRN", "Green"), ("WHT", "White")]
_STORAGES = ["128", "256", "512"]

_RETAIL_COUNTRIES = ["US", "US", "US", "CA", "GB", "DE", "FR", "NL", "CN", "JP", "AU", "US"]
_CITY_BY_COUNTRY = {
    "US": ["Austin", "Denver", "Seattle", "Chicago", "Boston", "Miami", "Portland", "San Diego"],
    "CA": ["Toronto", "Vancouver"],
    "GB": ["London", "Manchester"],
    "DE": ["Berlin", "Munich"],
    "FR": ["Paris", "Lyon"],
    "NL": ["Amsterdam", "Tilburg"],
    "CN": ["Shanghai", "Shenzhen"],
    "JP": ["Tokyo", "Osaka"],
    "AU": ["Sydney", "Melbourne"],
    "CH": ["Zurich"],
    "MX": ["Monterrey"],
    "SG": ["Singapore"],
}
_LANDLORDS = [
    "Brookline Property Trust", "Carraway REIT LP", "Hudson Gate Partners",
    "Stellar Urban Holdings", "Meridian Plaza Owners LLC", "Cornerstone CRE Fund II",
    "Pacific Crown Estates", "Galleria Retail Trust", "Northbridge Logistics Park",
    "Eurolog Property B.V.", "Sakura Estate KK", "Harbour City Holdings",
]
_PORTS = ["CNSHA", "CNYTN", "TWKHH", "VNSGN", "KRPUS", "JPNGO", "CNXMN", "TWTPE"]
_SCAC = ["MAEU", "EGLV", "ONEY", "FDXG", "HLCU", "CMDU"]
_HS = ["851770", "850760", "852990", "847330", "853400", "392690"]
_DEPARTMENTS = ["RETAIL_OPS", "SUPPLY_CHAIN", "ENGINEERING", "CUSTOMER_SUPPORT", "G&A"]
_SNAPSHOTS = ["2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01", "2026-01-01", "2026-04-01"]
_QUARTERS = ["FY2025-Q3", "FY2025-Q4", "FY2026-Q1", "FY2026-Q2"]

_MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

_TICKET_CHANNELS = ["PHONE", "CHAT", "EMAIL", "STORE"]
_TICKET_ISSUES = ["BATTERY", "DISPLAY", "AUDIO", "CONNECTIVITY", "SOFTWARE", "SHIPPING_DAMAGE", "OTHER"]
_TICKET_STATUS = ["NEW", "IN_PROGRESS", "RESOLVED", "CLOSED"]
_RESOLUTIONS = ["REPLACED", "REPAIRED", "REFUND", "NO_FAULT_FOUND"]
_TZ = ["Z", "+08:00", "-07:00", "+01:00", "+09:00", "-05:00"]

#: the planted text bridge: the EXACT phrase the flagship text question greps
SWELLING_PHRASE = "battery swelling"

# ------------------------------------------------------------------- helpers


def _money(x: float) -> str:
    return f"{x:,.2f}"


def _gtin14(body12: str) -> str:
    """GTIN-14 from a 13-digit body (we use '0' + 12 digits) with a valid
    GS1 mod-10 check digit."""
    digits = [int(c) for c in "0" + body12]
    assert len(digits) == 13
    total = sum(d * (3 if (i % 2 == 0) else 1) for i, d in enumerate(digits))
    return "".join(str(d) for d in digits) + str((10 - total % 10) % 10)


_ISO6346_LETTER = {
    c: v
    for c, v in zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38],
    )
}


def _container(owner: str, serial: int) -> str:
    """Valid ISO 6346 container number: 4-letter owner code + 6 digits + check."""
    body = f"{owner}{serial:06d}"
    total = sum(
        (_ISO6346_LETTER[ch] if ch.isalpha() else int(ch)) * (2**i)
        for i, ch in enumerate(body)
    )
    return body + str((total % 11) % 10)


def _dmonyy(d: date) -> str:
    return f"{d.day:02d}-{_MON[d.month - 1]}-{d.year % 100:02d}"


def _ddmmyyyy(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def _eu_money(x: float) -> str:
    """'1.234.567,89' — EU thousands-dot / decimal-comma."""
    return _money(x).replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _parse_num(s: str) -> Optional[float]:
    """Parse the corpus's clean numeric string forms exactly like the
    conformance layer would: bare, comma-grouped, or 'USD '-prefixed."""
    t = str(s).strip()
    if not t or t.upper() in {"NULL", "N/A", "-", "TBD"}:
        return None
    if t.upper().startswith("USD "):
        t = t[4:]
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


_KG_PER_LB = 0.45359237


def _parse_weight_kg(s: str) -> Optional[float]:
    t = str(s).strip().lower()
    if t.endswith("kg"):
        return float(t[:-2].strip().replace(",", ""))
    if t.endswith("lb"):
        return float(t[:-2].strip().replace(",", "")) * _KG_PER_LB
    return None


# -------------------------------------------------------------- entity graph


#: quality-only orphan suppliers get first words NO contracted supplier uses,
#: so the orphans are genuinely unresolvable (deliberate, explainable) without
#: colliding with a real entity's name neighborhood
_ORPHAN_W1 = ["Quanzhou Apex", "Bac Ninh Crown", "Toyohashi Gear",
              "Changwon Delta", "Zhangzhou Reed", "Hue Amber"]


def _suppliers() -> list[dict[str, Any]]:
    out = []
    for i in range(226):
        w1 = _ORPHAN_W1[i - 220] if 220 <= i < 226 else _SUPPLIER_W1[i % len(_SUPPLIER_W1)]
        w2 = _SUPPLIER_W2[i % len(_SUPPLIER_W2)]
        country = _SUPPLIER_COUNTRIES[i % len(_SUPPLIER_COUNTRIES)]
        name = f"{w1} {w2} {_LEGAL_FORM[country]}"
        out.append(
            {
                "idx": i,
                "sid": f"{104732 + 137 * i:010d}",
                "name": name,
                "core": f"{w1} {w2}",
                "country": country,
                "duns": f"{604411000 + 977 * i % 99999999:09d}",
                "contracted": i < 200,
                "top": i < 40,
                "dormant": 178 <= i < 198,        # contracted, never ordered from
                "po_only": 200 <= i < 220,        # tail vendors with no contract
                "orphan": 220 <= i < 226,         # quality-only deliberate orphans
                "zero_strip": 41 <= i < 53,       # PO rows may drop leading zeros
            }
        )
    return out


def _supplier_variant(rng: Random, s: dict[str, Any], *, wild: bool) -> str:
    """A spelling variant. ``wild=False`` variants stay within relax-2 folding
    (case / punctuation / legal-suffix tokens); ``wild=True`` adds the
    quality-table forms (truncation, '(Shenzhen)' tags) that need real ER."""
    name, core = s["name"], s["core"]
    choices = [
        name.upper(),
        name.replace("Co., Ltd.", "Co Ltd"),
        name.replace(", Ltd.", " Ltd").replace("Corp.", "Corp"),
        f"{core} {_LEGAL_FORM[s['country']].replace('.', '')}",
    ]
    if wild:
        choices += [
            core.upper(),
            f"{core} ({_CITY_BY_COUNTRY.get(s['country'], ['Metro'])[0]})",
            core,
        ]
    v = choices[rng.randrange(len(choices))]
    if rng.random() < 0.06:
        v = v.replace(" ", "  ", 1)              # double internal space wart
    elif rng.random() < 0.04:
        v = v + " "                              # trailing whitespace wart
    return v


def _parts(rng: Random, suppliers: list[dict]) -> list[dict[str, Any]]:
    """~420 BOM components + 30 MRO materials, each with a home vendor."""
    vendor_pool = [s for s in suppliers if (s["contracted"] and not s["dormant"]) or s["po_only"]]
    cats = list(_CATEGORIES)
    out = [
        {"pn": "CMP-DSP-0451", "category": "DISPLAY",
         "description": "6.7-inch OLED display module", "vendor": suppliers[0],
         "unit_usd": 41.80},
        {"pn": "CMP-BAT-0118", "category": "BATTERY",
         "description": "Li-ion battery pack 4500mAh", "vendor": suppliers[3],
         "unit_usd": 9.40},
    ]
    used = {p["pn"] for p in out}
    n = 100
    while len(out) < 420:
        cat = cats[len(out) % len(cats)]
        tag, (lo, hi) = _CATEGORIES[cat]
        pn = f"CMP-{tag}-{n:04d}"
        n += 1
        if pn in used:
            continue
        used.add(pn)
        # every 9th part reuses the previous same-category description: real
        # part masters carry duplicate descriptions under distinct numbers
        if len(out) % 9 == 5:
            prev = next((p for p in reversed(out) if p["category"] == cat), None)
            description = prev["description"] if prev else f"{_CAT_WORDS[cat]} {n - 100:03d}"
        else:
            description = f"{_CAT_WORDS[cat]} {n - 100:03d}"
        out.append(
            {
                "pn": pn,
                "category": cat,
                "description": description,
                "vendor": vendor_pool[(len(out) * 13) % len(vendor_pool)],
                "unit_usd": round(rng.uniform(lo, hi), 2),
            }
        )
    for k in range(30):
        out.append(
            {
                "pn": f"MRO-GEN-{700 + k:04d}",
                "category": "MRO",
                "description": f"maintenance supply item {k:02d}",
                "vendor": vendor_pool[(k * 29) % len(vendor_pool)],
                "unit_usd": round(rng.uniform(10, 500), 2),
            }
        )
    return out


def _facilities(rng: Random) -> list[dict[str, Any]]:
    out = []
    for i in range(90):
        cc = _RETAIL_COUNTRIES[i % len(_RETAIL_COUNTRIES)]
        city = _CITY_BY_COUNTRY[cc][i % len(_CITY_BY_COUNTRY[cc])]
        out.append(
            {
                "code": f"MER-RTL-{cc}-{i + 1:04d}",
                "ptype": "RETAIL",
                "name": f"{city} {['Flagship', 'Mall', 'Center', 'Galleria'][i % 4]} Store",
                "site_name": f"Meridian Store {city} {i + 1:02d}",
                "country": cc,
                "city": city,
                "dept": "RETAIL_OPS",
                "sqft": 2200 + 100 * (i % 28),
            }
        )
    dc_spec = [
        ("US", 1), ("US", 2), ("US", 3), ("US", 4), ("US", 5), ("US", 6),
        ("NL", 1), ("DE", 1), ("CN", 1), ("CN", 2), ("JP", 1), ("GB", 1),
        ("MX", 1), ("SG", 1),
    ]
    for cc, k in dc_spec:
        city = _CITY_BY_COUNTRY.get(cc, ["Metro"])[(k - 1) % len(_CITY_BY_COUNTRY.get(cc, ["Metro"]))]
        code = f"MER-DC-{cc}-{k:02d}"
        out.append(
            {
                "code": code, "ptype": "DISTRIBUTION_CENTER",
                "name": f"{city} DC Bldg {chr(64 + k)}",
                "site_name": f"Meridian {city} Distribution Centre",
                "country": cc, "city": city, "dept": "SUPPLY_CHAIN",
                "sqft": 180000 + 15000 * k,
            }
        )
    for j, cc in enumerate(["US", "US", "GB", "NL", "JP", "CH"]):
        city = _CITY_BY_COUNTRY[cc][j % len(_CITY_BY_COUNTRY[cc])]
        out.append(
            {
                "code": f"MER-OFF-{cc}-{j + 1:02d}", "ptype": "OFFICE",
                "name": f"{city} Office Tower {j + 1}",
                "site_name": f"Meridian {city} Office",
                "country": cc, "city": city, "dept": "G&A",
                # the first office is a small satellite whose footprint
                # coincides with a retail store's (FD-collision by design)
                "sqft": 2700 if j == 0 else 30000 + 4000 * j,
            }
        )
    for j, cc in enumerate(["US", "TW", "CN", "KR"]):
        city = _CITY_BY_COUNTRY.get(cc, ["Hsinchu"])[0]
        out.append(
            {
                "code": f"MER-LAB-{cc}-{j + 1:02d}", "ptype": "R&D_LAB",
                "name": f"{city} Research Lab {j + 1}",
                "site_name": f"Meridian {city} R&D Lab",
                "country": cc, "city": city, "dept": "ENGINEERING",
                "sqft": 42000 + 6000 * j,
            }
        )
    for j, cc in enumerate(["US", "US", "IE", "NL", "SG", "JP"]):
        city = _CITY_BY_COUNTRY.get(cc, ["Dublin"])[j % max(1, len(_CITY_BY_COUNTRY.get(cc, ["Dublin"])))]
        out.append(
            {
                "code": f"MER-DCN-{cc}-{j + 1:02d}", "ptype": "DATA_CENTER",
                "name": f"{city} Data Center {j + 1}",
                "site_name": f"Meridian {city} Data Center",
                "country": cc, "city": city, "dept": "ENGINEERING",
                "sqft": 60000 + 9000 * j,
            }
        )
    # 3PL-operated sites: headcount + shipments but NO lease (explainable orphans)
    for j, cc in enumerate(["US", "PL", "MY"]):
        out.append(
            {
                "code": f"MER-3PL-{cc}-{j + 1:02d}", "ptype": "3PL",
                "name": f"3PL Site {cc} {j + 1}",
                "site_name": f"Meridian 3PL {cc} {j + 1}",
                "country": cc, "city": _CITY_BY_COUNTRY.get(cc, ["Krakow"])[0],
                "dept": "SUPPLY_CHAIN", "sqft": 90000,
            }
        )
    return out


def _products(rng: Random) -> list[dict[str, Any]]:
    out = []
    gt = 0
    for fam, code, label, has_storage in _MODEL_SPECS:
        launch_year = 2021 + (sum(ord(c) for c in code) % 5)
        launch = f"{launch_year}-{3 + (gt % 7):02d}-15"
        variants: list[tuple[Optional[str], str]]
        if fam == "ACCESSORY":
            variants = [(None, c) for c in (["BLK", "WHT"] if gt % 2 == 0 else ["BLK"])]
        elif has_storage:
            n_st = 2 + gt % 2
            n_co = 2 + (gt + 1) % 2
            variants = [(s, c) for s in _STORAGES[:n_st] for (c, _) in _COLORS[:n_co]]
        else:
            variants = [(None, c) for (c, _) in _COLORS[: 3 + gt % 2]]
        base = {
            "PULSE": 699, "AURA": 999, "SLATE": 449, "HALO": 129,
            "CORE": 199, "ACCESSORY": 29,
        }[fam] + 50 * (sum(ord(c) for c in code) % 7)
        for storage, color in variants:
            sku = f"MER-{code}-{storage or 'STD'}-{color}"
            color_name = dict(_COLORS)[color]
            name = f"Meridian {label}" + (f" {storage}GB" if storage else "") + (
                f" {color_name}" if fam != "ACCESSORY" else ""
            )
            msrp = base + (100 * _STORAGES.index(storage) if storage else 0)
            eol = gt % 9 == 0 and fam != "ACCESSORY"
            announced = gt % 27 == 13 and fam != "ACCESSORY"  # announced EOS, still ACTIVE
            out.append(
                {
                    "sku": sku,
                    "gtin": _gtin14(f"81935800{gt:04d}"),
                    "name": name,
                    "model": code,
                    "family": fam,
                    "launch": launch,
                    "eos": f"{launch_year + 3}-09-30" if (eol or announced) else "9999-12-31",
                    "msrp": float(msrp) - 1 + 0.99 if fam != "ACCESSORY" else float(msrp) - 0.01,
                    "storage": storage,
                    "color": color_name,
                    "status": "EOL" if eol else "ACTIVE",
                    "origin": ["CN", "VN", "CN", "TW"][gt % 4],
                }
            )
            gt += 1
    # hero variant pricing pin (gold question MQ-07)
    for p in out:
        if p["sku"] == "MER-PLS9P-256-BLK":
            p["msrp"] = 1099.00
    return out


# ------------------------------------------------------------------- tables


def _contracts_frame(rng: Random, suppliers: list[dict]) -> pd.DataFrame:
    rows = []
    seq = 100

    def add(s, ctype, year, status, committed, *, current=True):
        nonlocal seq
        seq += 1
        number = f"{'MSA' if ctype == 'MSA' else ('PA' if ctype == 'PRICING_AGREEMENT' else ('SOW' if ctype == 'SOW' else 'AMD'))}-{year}-{seq:04d}"
        eff = date(year, 1 + seq % 12, 1 + seq % 27)
        exp = date(year + (3 if ctype == "MSA" else 2), eff.month, eff.day)
        rows.append(
            {
                "CONTRACT_NUMBER": number,
                "SUPPLIER_ID": s["sid"],
                "SUPPLIER_LEGAL_NAME": s["name"],
                "DUNS_NUMBER": s["duns"],
                "CONTRACT_TYPE": ctype,
                "EFFECTIVE_DATE": eff.isoformat(),
                "EXPIRATION_DATE": exp.isoformat(),
                "AUTO_RENEWAL_FLAG": ["Y", "N", ""][seq % 3],
                "PAYMENT_TERMS": _PAYMENT_TERMS[s["idx"] % 4],
                "DEFAULT_INCOTERMS": _INCOTERMS[s["idx"] % 6],
                "INCOTERMS_NAMED_PLACE": (
                    "TBD" if seq % 17 == 0 else f"{_PORTS[(s['idx'] + seq) % len(_PORTS)]} port of loading"
                ),
                "ANNUAL_COMMITTED_SPEND": (
                    _money(float(round((200 + 13 * (s['idx'] % 40)) * 25000, -3)))[:-3]
                    if ctype in ("MSA", "PRICING_AGREEMENT") and s["top"]
                    else (_money(float(round(rng.uniform(50, 900), 0) * 1000))[:-3] if ctype == "MSA" else "")
                ),
                "COMMITMENT_CURRENCY": (
                    ("EUR" if (seq % 11 == 0 and s["idx"] != 0) else "USD") if ctype != "SOW" else ""
                ),
                "REBATE_PCT": ["0.0", "1.5", "2.0", "2.5", ""][seq % 5],
                "SUPPLIER_COUNTRY": s["country"],
                # negotiated governing-law overrides keep law from being a
                # bijection of country (a coincidental-FD killer, and realistic)
                "GOVERNING_LAW": "New York" if seq % 13 == 0 else _GOVERNING_LAW[s["country"]],
                "BUYER_ENTITY": _BUYER[(s["idx"] // 6 + seq) % 3],
                "STATUS": status,
                "_committed_num": None,
            }
        )
        return rows[-1]

    for s in [x for x in suppliers if x["contracted"]]:
        if s["top"]:
            add(s, "MSA", 2019, "EXPIRED", True)          # expired predecessor
            add(s, "MSA", 2024, "ACTIVE", True)
            add(s, "AMENDMENT", 2025, "ACTIVE", False)
            if s["idx"] % 3 == 0:
                add(s, "SOW", 2025, "ACTIVE", False)
        else:
            status = "ACTIVE" if s["idx"] % 9 else "TERMINATED"
            add(s, "PRICING_AGREEMENT" if s["idx"] % 3 == 0 else "MSA", 2023 + s["idx"] % 3, status, True)
    # a few DRAFT rows keep the status vocabulary a non-subset of lease statuses
    for s in suppliers[150:153]:
        add(s, "MSA", 2026, "DRAFT", True)

    df = pd.DataFrame(rows).drop(columns=["_committed_num"])
    # the hero contract the gold questions cite (clean, USD, ACTIVE)
    hero = df.index[(df["SUPPLIER_ID"] == suppliers[0]["sid"]) & (df["CONTRACT_TYPE"] == "MSA") & (df["STATUS"] == "ACTIVE")][0]
    df.loc[hero, "CONTRACT_NUMBER"] = "MSA-2024-0117"
    df.loc[hero, "ANNUAL_COMMITTED_SPEND"] = "8,500,000"
    df.loc[hero, "COMMITMENT_CURRENCY"] = "USD"
    df.loc[hero, "INCOTERMS_NAMED_PLACE"] = "Shenzhen Yantian port"
    return df


def _po_frame(rng: Random, suppliers, parts, facilities) -> pd.DataFrame:
    vendors = [s for s in suppliers if (s["contracted"] and not s["dormant"]) or s["po_only"]]
    weights = [1.0 / (i + 6) for i in range(len(vendors))]
    parts_by_vendor: dict[int, list[dict]] = {}
    for p in parts:
        parts_by_vendor.setdefault(p["vendor"]["idx"], []).append(p)
    plants = [f["code"] for f in facilities if f["ptype"] in ("DISTRIBUTION_CENTER", "3PL")]
    rows: list[dict[str, Any]] = []
    reuse_pool: dict[int, list[dict]] = {}
    po_num = 4500087000
    start = date(2024, 6, 3)

    def pick_vendor(k: int) -> dict:
        x = (k * 0.6180339887498949) % 1.0 * sum(weights)
        acc = 0.0
        for v, w in zip(vendors, weights):
            acc += w
            if x <= acc:
                return v
        return vendors[-1]

    k = 0
    while len(rows) < 1500:
        k += 1
        v = pick_vendor(k)
        pool = parts_by_vendor.get(v["idx"]) or [parts[(k * 7) % len(parts)]]
        po_num += rng.randrange(2, 9)
        po_date = start + timedelta(days=(k * 5) % 730)
        n_lines = 1 + rng.randrange(4)
        strip = v["zero_strip"] and rng.random() < 0.7
        variant = (not v["top"]) and (not strip) and rng.random() < 0.12
        # ~10% of POs are export-priced in USD regardless of the vendor's
        # document currency (kills coincidental vendor/material -> currency FDs)
        po_currency = "USD" if rng.random() < 0.10 else None
        po_incoterm = _INCOTERMS[(v["idx"] + k) % 6]
        po_location = _PORTS[(v["idx"] + 3 * k) % len(_PORTS)]
        po_group = f"P{100 + (v['idx'] + k // 5) % 9}"
        for line in range(1, n_lines + 1):
            part = pool[(k + line) % len(pool)]
            prior = reuse_pool.get(v["idx"], [])
            if prior and rng.random() < 0.12:
                econ = prior[rng.randrange(len(prior))]
            else:
                uom = "EA"
                if part["category"] in ("DISPLAY", "FASTENER", "ADHESIVE") and rng.random() < 0.4:
                    uom = "KPC"
                elif rng.random() < 0.15:
                    uom = "PC"
                qty = rng.randrange(2, 60) if uom == "KPC" else rng.randrange(500, 20000)
                if qty % 5 == 0:
                    qty += 1   # never a multiple of 5: keeps the order-quantity
                    # value domain disjoint from inspection/reject tallies
                qty_each = qty * 1000 if uom == "KPC" else qty
                pu = [1, 10, 100, 1000][rng.randrange(4)]
                cur = po_currency or _DOC_CURRENCY[part["vendor"]["country"]]
                unit_usd = part["unit_usd"]
                net_price = round(unit_usd / _FX_TO_USD[cur] * pu, 2)
                econ = {
                    "uom": uom, "qty": qty, "pu": pu, "cur": cur,
                    "net_price": net_price,
                    "net_value_usd": round(qty_each * unit_usd, 2),
                    "part": part,
                }
                reuse_pool.setdefault(v["idx"], []).append(econ)
            part = econ["part"]
            usd_prefixed = rng.random() < 0.30
            short_text = part["description"]
            if rng.random() < 0.12:
                short_text = f"{short_text} - expedite"   # buyer-edited PO text
            rows.append(
                {
                    "PO_NUMBER": str(po_num),
                    "PO_LINE_ITEM": f"{line * 10:05d}",
                    "DOC_TYPE": "NB" if k % 7 else "ZNB",
                    "PO_DATE": po_date.isoformat(),
                    "VENDOR_ID": v["sid"].lstrip("0") if strip else v["sid"],
                    "SUPPLIER_NAME": _supplier_variant(rng, v, wild=False) if variant else v["name"],
                    "MATERIAL_NUMBER": part["pn"],
                    "SHORT_TEXT": short_text,
                    "ORDER_QTY": str(econ["qty"]),
                    "ORDER_UOM": econ["uom"],
                    "NET_PRICE": _money(econ["net_price"]),
                    "PRICE_UNIT": str(econ["pu"]),
                    "CURRENCY": econ["cur"],
                    "NET_VALUE_USD": (f"USD {_money(econ['net_value_usd'])}" if usd_prefixed else _money(econ["net_value_usd"])),
                    "INCOTERMS": po_incoterm,
                    "INCOTERMS_LOCATION": po_location,
                    "PLANT_CODE": plants[(k + line) % len(plants)],
                    "DELIVERY_DATE": (po_date + timedelta(days=30 + (k % 40))).isoformat(),
                    "PURCH_GROUP": po_group,
                    "PO_STATUS": ["CLOSED", "CLOSED", "OPEN", "CANCELLED"][rng.randrange(4)] if po_date < date(2026, 3, 1) else "OPEN",
                    "_vendor_idx": v["idx"],
                }
            )
            if len(rows) >= 1500:
                break
    df = pd.DataFrame(rows[:1500])
    # three POs carry two economically identical lines (kills every
    # (PO_NUMBER, X != line item) accidental composite key)
    base_rows = df.index[df["PO_LINE_ITEM"] == "00010"][:3]
    for bi in base_rows:
        twin = df.loc[bi].copy()
        twin["PO_LINE_ITEM"] = "00099"
        df.loc[len(df)] = twin
    # the trick-unit part: guarantee EA and KPC lines for CMP-DSP-0451 in 2026Q1
    hero = suppliers[0]
    extra = []
    for j, (uom, qty, pu) in enumerate([("EA", 4003, 100), ("KPC", 12, 1000), ("EA", 2501, 1), ("KPC", 8, 1000)]):
        qty_each = qty * 1000 if uom == "KPC" else qty
        extra.append(
            {
                "PO_NUMBER": str(4500099900 + j), "PO_LINE_ITEM": "00010",
                "DOC_TYPE": "NB", "PO_DATE": f"2026-0{1 + j % 3}-1{j}",
                "VENDOR_ID": hero["sid"], "SUPPLIER_NAME": hero["name"],
                "MATERIAL_NUMBER": "CMP-DSP-0451",
                "SHORT_TEXT": "6.7-inch OLED display module",
                "ORDER_QTY": str(qty), "ORDER_UOM": uom,
                "NET_PRICE": _money(41.80 / _FX_TO_USD["CNY"] * pu),
                "PRICE_UNIT": str(pu), "CURRENCY": "CNY",
                "NET_VALUE_USD": _money(round(qty_each * 41.80, 2)),
                "INCOTERMS": "FOB", "INCOTERMS_LOCATION": "CNSHA",
                "PLANT_CODE": "MER-DC-NL-01",
                "DELIVERY_DATE": f"2026-0{2 + j % 3}-2{j}",
                "PURCH_GROUP": "P100", "PO_STATUS": "OPEN",
                "_vendor_idx": hero["idx"],
            }
        )
    # a round price point recurring across currencies (kills the coincidental
    # NET_PRICE -> CURRENCY value-level FD; '100.00' is a real price, twice)
    for j, cur in enumerate(["USD", "CNY"]):
        extra.append(
            {
                **extra[0],
                "PO_NUMBER": str(4500099950 + j), "PO_DATE": "2025-11-03",
                "MATERIAL_NUMBER": "MRO-GEN-0700",
                "SHORT_TEXT": "maintenance supply item 00",
                "ORDER_QTY": "40", "ORDER_UOM": "EA", "NET_PRICE": "100.00",
                "PRICE_UNIT": "1", "CURRENCY": cur,
                "NET_VALUE_USD": _money(4000.0 * (1.0 if cur == "USD" else _FX_TO_USD["CNY"])),
                "PO_STATUS": "CLOSED",
            }
        )
    df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
    return df


def _qn_frame(rng: Random, suppliers, parts, po: pd.DataFrame, facilities) -> pd.DataFrame:
    vendors = [s for s in suppliers if (s["contracted"] and not s["dormant"] and s["idx"] < 74) or s["orphan"]]
    parts_by_vendor: dict[int, list[dict]] = {}
    for p in parts:
        parts_by_vendor.setdefault(p["vendor"]["idx"], []).append(p)
    po_by_vendor: dict[int, list[str]] = {}
    for _, r in po.iterrows():
        po_by_vendor.setdefault(int(r["_vendor_idx"]), []).append(r["PO_NUMBER"])
    sites = [f["code"] for f in facilities if f["ptype"] in ("DISTRIBUTION_CENTER", "3PL")]
    defects = [
        ("DEF-SOLDER-01", "cold solder joint on connector"),
        ("DEF-COSM-02", "cosmetic scratch beyond spec"),
        ("DEF-DIM-03", "dimension out of tolerance"),
        ("DEF-ELEC-04", "fails electrical continuity test"),
        ("DEF-PKG-05", "packaging damaged in transit"),
        ("DEF-SWELL-06", "cell pouch deformation observed"),
        ("DEF-COSM-07", "cosmetic scratch beyond spec"),   # duplicate catalog text
    ]
    rows = []
    start = date(2025, 1, 6)
    for i in range(780):
        v = vendors[(i * 11) % len(vendors)]
        pool = parts_by_vendor.get(v["idx"]) or [parts[(i * 3) % len(parts)]]
        part = pool[i % len(pool)]
        created = start + timedelta(days=(i * 7) % 520)
        insp = 50 * rng.randrange(12, 400)            # inspection lots come in round sizes
        sev = ["MINOR", "MINOR", "MAJOR", "MAJOR", "CRITICAL"][rng.randrange(5)]
        rej_rate = {"MINOR": 0.002, "MAJOR": 0.01, "CRITICAL": 0.03}[sev]
        # reject counts are tallied in multiples of 5 and capped per the SCAR
        # process (also keeps this value domain disjoint from order quantities)
        rejected = 5 * max(0, min(96, int(insp * rej_rate * rng.uniform(0.2, 2.2) / 5)))
        is_open = created > date(2026, 2, 1) and rng.random() < 0.6
        d = defects[rng.randrange(len(defects))]
        wild_name = _supplier_variant(rng, v, wild=True) if (v["idx"] < 25 or v["orphan"]) and i % 3 else v["name"]
        refs = po_by_vendor.get(v["idx"], [])
        rows.append(
            {
                "NOTIFICATION_ID": f"QN-{created.year % 100:03d}-{4000 + i:05d}",
                "NOTIF_TYPE": ["INCOMING_INSPECTION", "INCOMING_INSPECTION", "SUPPLIER_COMPLAINT", "SCAR"][i % 4],
                "CREATED_DATE": created.isoformat(),
                "SUPPLIER": wild_name,
                "PART_NUMBER": part["pn"],
                "PART_DESCRIPTION": (
                    part["description"].upper() if rng.random() < 0.08 else part["description"]
                ),
                "LOT_NUMBER": f"LOT-{created.year}{created.month:02d}-{(i * 17) % 90 + 10:02d}",
                "PO_REFERENCE": refs[i % len(refs)] if refs and i % 5 < 2 else "",
                "QTY_INSPECTED": str(insp),
                "QTY_REJECTED": str(rejected),
                "DEFECT_CODE": d[0],
                "DEFECT_DESCRIPTION": d[1],
                "SEVERITY": sev,
                "DISPOSITION": ["USE_AS_IS", "REWORK", "RETURN_TO_VENDOR", "SCRAP"][rng.randrange(4)],
                "INSPECTION_SITE": sites[i % len(sites)],
                "STATUS": "OPEN" if is_open else "CLOSED",
                # a few CLOSED rows lost their close date (entry gap): the
                # blank token spans both statuses, killing the value-level FD
                "CLOSED_DATE": ("" if i % 3 == 0 else ["NULL", "N/A"][i % 2]) if is_open
                else ("" if i % 97 == 0 else (created + timedelta(days=12 + i % 30)).isoformat()),
            }
        )
    # planted battery-supplier cluster: OPEN critical notifications on the hero
    # battery part against supplier idx 3 (the text-to-supply-chain bridge)
    volta = suppliers[3]
    for j in range(7):
        created = date(2026, 4, 2) + timedelta(days=8 * j)
        rows.append(
            {
                "NOTIFICATION_ID": f"QN-026-{4900 + j:05d}",
                "NOTIF_TYPE": "SCAR" if j % 2 else "SUPPLIER_COMPLAINT",
                "CREATED_DATE": created.isoformat(),
                "SUPPLIER": [volta["name"], volta["name"].upper(), f"{volta['core']} Co Ltd"][j % 3],
                "PART_NUMBER": "CMP-BAT-0118",
                "PART_DESCRIPTION": "Li-ion battery pack 4500mAh",
                "LOT_NUMBER": f"LOT-2026{2 + j % 3:02d}-77",
                "PO_REFERENCE": "",
                "QTY_INSPECTED": str(1200 + 50 * j),
                "QTY_REJECTED": str(140 + 10 * j),
                "DEFECT_CODE": "DEF-SWELL-06",
                "DEFECT_DESCRIPTION": "cell pouch deformation observed",
                "SEVERITY": "CRITICAL",
                "DISPOSITION": "RETURN_TO_VENDOR",
                "INSPECTION_SITE": "MER-DC-NL-01",
                "STATUS": "OPEN",
                "CLOSED_DATE": "NULL",
            }
        )
    # ~1% double-entered events: identical rows re-keyed under a new id, plus
    # near-dupes (same lot, reworded defect) — the documented quality warts
    df = pd.DataFrame(rows)
    closed_idx = [int(x) for x in df.index[df["STATUS"] == "CLOSED"][:8]]
    twins = df.loc[closed_idx].copy()
    twins["NOTIFICATION_ID"] = [f"QN-026-{4970 + t:05d}" for t in range(len(twins))]
    near = df.loc[closed_idx[:4]].copy()
    near["NOTIFICATION_ID"] = [f"QN-026-{4990 + t:05d}" for t in range(len(near))]
    near["DEFECT_DESCRIPTION"] = near["DEFECT_DESCRIPTION"].map(lambda s: f"rework review: {s}")
    return pd.concat([df, twins, near], ignore_index=True)


def _products_frame(products) -> pd.DataFrame:
    rows = []
    for i, p in enumerate(products):
        rows.append(
            {
                "SKU": p["sku"],
                "GTIN": p["gtin"],
                "PRODUCT_NAME": p["name"],
                "MODEL_CODE": p["model"],
                "PRODUCT_FAMILY": p["family"],
                "LAUNCH_DATE": p["launch"],
                "END_OF_SALE_DATE": p["eos"],
                "MSRP_USD": _money(p["msrp"]),
                "COLOR": p["color"],
                "STORAGE_GB": (p["storage"] or ("" if i % 2 else "N/A")),
                "LIFECYCLE_STATUS": p["status"],
                "COUNTRY_OF_ORIGIN": p["origin"],
            }
        )
    return pd.DataFrame(rows)


def _bom_frame(rng: Random, products, parts) -> pd.DataFrame:
    hardware_models = []
    seen = set()
    for p in products:
        if p["family"] != "ACCESSORY" and p["model"] not in seen:
            seen.add(p["model"])
            hardware_models.append(p)
    bom_parts = [p for p in parts if p["category"] != "MRO"]
    rows = []
    bid = 50000
    for mi, m in enumerate(hardware_models + [
        {"model": ph, "name": f"Program {ph}", "family": "PULSE", "launch": "2026-09-01"}
        for ph in _PHANTOM_MODELS
    ]):
        n_comp = 12 + (mi % 7)
        chosen = [bom_parts[(mi * 17 + j * 5) % len(bom_parts)] for j in range(n_comp)]
        # shared platform battery across three PULSE phones (incl. the hero)
        if m["model"] in ("PLS9P", "PLS9", "PLS9U"):
            chosen[0] = next(p for p in parts if p["pn"] == "CMP-BAT-0118")
            chosen[1] = next(p for p in parts if p["pn"] == "CMP-DSP-0451")
        for j, part in enumerate(chosen):
            bid += 1
            level = "1" if j < n_comp - 3 else ["2", "2", "3"][j % 3]
            uom = "EA" if part["category"] not in ("ADHESIVE",) else ["GR", "ML"][rng.randrange(2)]
            appr = part["vendor"]
            appr_id = appr["sid"] if appr["contracted"] or j % 4 else ""
            # a few approved-but-uncontracted suppliers keep the approved-id
            # domain a non-subset of the contract supplier ids
            if j % 9 == 0 and not appr["contracted"]:
                appr_id = appr["sid"]
            rows.append(
                {
                    "BOM_ID": f"BOM-{bid:05d}",
                    "PARENT_MODEL_CODE": m["model"],
                    "PARENT_DESCRIPTION": (m["name"] if not str(m["name"]).startswith("Program") else m["name"]),
                    "BOM_LEVEL": level,
                    "COMPONENT_PART_NUMBER": part["pn"],
                    "COMPONENT_DESCRIPTION": part["description"],
                    "COMPONENT_CATEGORY": part["category"],
                    "QTY_PER_ASSEMBLY": str(
                        [1, 1, 2, 4, 8][rng.randrange(5)] if part["category"] != "FASTENER"
                        else 6 + rng.randrange(9)
                    ),
                    "UOM": uom,
                    "REF_DESIGNATOR": f"{part['category'][:1]}{j + 1:02d}",
                    "APPROVED_SUPPLIER_ID": appr_id,
                    "EFFECTIVE_FROM_DATE": m["launch"],
                    "EFFECTIVE_TO_DATE": "9999-12-31" if rng.random() > 0.08 else "2026-12-31",
                    "ECO_NUMBER": f"ECO-{2023 + mi % 4}-{100 + (mi * 7 + j // 6) % 800:04d}",
                }
            )
    return pd.DataFrame(rows)


def _lease_frames(rng: Random, facilities) -> pd.DataFrame:
    rows = []
    lease_n = 1000
    eu_cn = {"GB", "DE", "FR", "NL", "CN", "CH"}

    def fmt_commence(d: date, country: str) -> str:
        return _ddmmyyyy(d) if country in eu_cn else d.isoformat()

    leased = [f for f in facilities if f["ptype"] != "3PL"]
    retail_seq = 0
    for fi, f in enumerate(leased):
        lease_n += 1
        lease_id = f"LSE-{lease_n:04d}"
        is_retail = f["ptype"] == "RETAIL"
        if is_retail:
            retail_seq += 1
        commence = date(2016 + fi % 8, 1 + fi % 12, 1 + fi % 27)
        term = [60, 120, 84, 144][rng.randrange(4)]
        expire = commence + timedelta(days=int(term * 30.44))
        # 17 retail leases expire inside (2026-06-12, 2027-06-12] — the gold
        # as-of window for MQ-06 (engineered with a clear margin)
        if is_retail and retail_seq <= 17:
            expire = date(2026, 7, 1) + timedelta(days=17 * retail_seq)
        elif is_retail:
            expire = date(2027, 8, 1) + timedelta(days=23 * retail_seq)
        sqm = f["country"] in eu_cn or f["country"] in ("JP", "AU", "SG", "MY", "KR", "TW", "MX", "IE", "PL")
        area = int(round(f["sqft"] * 0.092903, 0)) if sqm else f["sqft"]
        rent = float(round(f["sqft"] * rng.uniform(24, 32) * (1.0 if is_retail else 0.4), -3))
        # landlords reuse marketing names for buildings across cities and
        # property types (real estates collide; coincidental name -> attribute
        # FDs must not survive)
        generic_pool = ["Riverside Galleria", "Gateway Plaza", "Harbor Point Center"]
        if is_retail and fi % 31 == 4:
            prop_name = generic_pool[fi % 3]
        elif f["code"] in ("MER-OFF-US-01", "MER-OFF-GB-03", "MER-LAB-TW-02", "MER-DC-US-05"):
            prop_name = generic_pool[(fi + 1) % 3]
        else:
            prop_name = f["name"]
        common = {
            "FACILITY_CODE": f["code"],
            "PROPERTY_NAME": prop_name + ("  " if fi % 41 == 0 else ""),
            "PROPERTY_TYPE": f["ptype"],
            "ADDRESS_LINE_1": f"{100 + (fi % 12) * 25} {['Main St', 'Market Ave', 'Harbor Rd', 'Station Blvd'][(fi // 3) % 4]}",
            "CITY": "ZÃ¼rich" if f["country"] == "CH" else f["city"],
            "STATE_PROVINCE": {"US": ["TX", "CO", "WA", "IL", "MA", "FL", "OR", "CA"][fi % 8]}.get(f["country"], ""),
            "COUNTRY": f["country"],
            "LANDLORD_NAME": _LANDLORDS[rng.randrange(len(_LANDLORDS))],
            "LEASE_TYPE": ["NNN", "Gross", "Modified Gross"][rng.randrange(3)],
            "LEASE_TERM_MONTHS": str(term),
            "RENT_CURRENCY": {"GB": "GBP", "DE": "EUR", "FR": "EUR", "NL": "EUR", "CN": "CNY", "JP": "JPY", "CH": "CHF"}.get(f["country"], "USD"),
            "ESCALATION_RATE_PCT": ["2.5", "3.0", "2.0"][rng.randrange(3)],
            "RENEWAL_OPTIONS": ["2 x 5 yr at FMV", "1 x 5 yr at 95% FMV", "none", "3 x 3 yr fixed"][rng.randrange(4)],
            "SQUARE_FOOTAGE": _money(float(area))[:-3],
            "AREA_UOM": ("" if fi % 19 == 0 else ("SQM" if sqm else "SF")),
            "SECURITY_DEPOSIT": (
                "10,000" if f["sqft"] == 2700 or (is_retail and fi % 30 == 0)
                else _money(float(round(rent * rng.uniform(0.10, 0.22), -3)))[:-3]
            ),
        }
        # expired predecessor lease for ~55% of facilities
        if fi % 9 < 5:
            old_commence = commence - timedelta(days=int(term * 30.44))
            rows.append(
                {
                    "LEASE_ID": f"LSE-{lease_n + 4000:04d}",
                    "AMENDMENT_NO": "0",
                    **common,
                    "LEASE_COMMENCEMENT_DATE": fmt_commence(old_commence, f["country"]),
                    "EXPIRATION_DATE": commence.isoformat(),
                    "BASE_RENT_ANNUAL": _money(rent * 0.8)[:-3],
                    "LEASE_STATUS": "EXPIRED" if fi % 7 else "TERMINATED",
                }
            )
        # the gold expiry-window leases are fresh, never-amended leases: their
        # row grain IS the lease grain (no superseded duplicates in the window)
        n_amend = 0 if (is_retail and retail_seq <= 17) else [0, 1, 1, 2][fi % 4]
        for a in range(n_amend):
            rows.append(
                {
                    "LEASE_ID": lease_id,
                    "AMENDMENT_NO": str(a),
                    **common,
                    "LEASE_COMMENCEMENT_DATE": fmt_commence(commence, f["country"]),
                    "EXPIRATION_DATE": expire.isoformat(),
                    "BASE_RENT_ANNUAL": _money(rent * (0.94 if a == 0 and fi % 2 else 1.0))[:-3],
                    "LEASE_STATUS": "SUPERSEDED",
                }
            )
        rows.append(
            {
                "LEASE_ID": lease_id,
                "AMENDMENT_NO": str(n_amend),
                **common,
                "LEASE_COMMENCEMENT_DATE": fmt_commence(commence, f["country"]),
                "EXPIRATION_DATE": expire.isoformat(),
                "BASE_RENT_ANNUAL": _money(rent)[:-3],
                "LEASE_STATUS": "HOLDOVER" if (not is_retail and fi % 31 == 5) else "ACTIVE",
            }
        )
    # one administrative re-papering: two amendment rows identical but for the
    # amendment number (kills accidental (LEASE_ID, X) composite keys)
    df = pd.DataFrame(rows)
    src = df.index[df["AMENDMENT_NO"] == "1"][1]
    twin = df.loc[src].copy()
    twin["AMENDMENT_NO"] = "9"
    df.loc[len(df)] = twin
    return df


def _shipments_frame(rng: Random, po: pd.DataFrame, facilities) -> pd.DataFrame:
    dcs = [f for f in facilities if f["ptype"] in ("DISTRIBUTION_CENTER", "3PL")]
    name_variant = {
        "MER-DC-NL-01": ["Tilburg DC", "MER DC NL01"],
        "MER-DC-US-01": ["Austin DC", "MER DC US01"],
        "MER-DC-DE-01": ["Berlin DC Bldg A", "MER DC DE01"],
        "MER-DC-CN-01": ["Shanghai DC"],
    }
    po_numbers = sorted(set(po["PO_NUMBER"].tolist()))
    rows = []
    start = date(2025, 1, 6)
    for i in range(1190):
        d = start + timedelta(days=(i * 4) % 520)
        if i % 37 == 0:
            d -= timedelta(days=200 + (i % 5) * 13)   # old backlog legs
        # hero DC: every row carries the exact facility code (clean join);
        # variants live on other facilities
        if i % 17 == 0:
            dest_f = next(f for f in dcs if f["code"] == "MER-DC-NL-01")
            dest = dest_f["code"]
        else:
            dest_f = dcs[(i * 5) % len(dcs)]
            dest = dest_f["code"]
            if dest in name_variant and dest != "MER-DC-NL-01" and i % 4 == 0:
                dest = name_variant[dest][i % len(name_variant[dest])]
        mode = ["OCEAN", "OCEAN", "OCEAN", "AIR", "TRUCK", "RAIL"][i % 6]
        status_pool = ["DELIVERED"] * 7 + ["IN_TRANSIT", "IN_TRANSIT", "EXCEPTION"]
        status = status_pool[i % len(status_pool)]
        if mode == "OCEAN" and i % 31 == 3:
            status = "CUSTOMS_HOLD"
        eta = d + timedelta(days=(27 + i % 3 if mode == "OCEAN" else 5 + i % 3))
        arrived = eta + timedelta(days=(i % 9) - 3)
        kg = float(rng.randrange(18, 1950) * 10)
        use_lb = i % 9 in (2, 5, 7)
        weight = f"{_money(round(kg / _KG_PER_LB, 1))[:-3]} lb" if use_lb else f"{_money(kg)[:-3]} kg"
        if i % 50 == 7:
            po_ref = str(4500900000 + i)          # dangling reference (~2%)
        elif i % 10 < 7:
            po_ref = po_numbers[(i * 13) % len(po_numbers)]
        else:
            po_ref = ""                            # inter-DC transfer
        rows.append(
            {
                "SHIPMENT_ID": f"SHP-{210000 + i:06d}",
                "PO_NUMBER": po_ref,
                "ORIGIN_PORT": _PORTS[i % len(_PORTS)],
                "DESTINATION": dest,
                "CARRIER_SCAC": _SCAC[rng.randrange(len(_SCAC))],
                "TRANSPORT_MODE": mode,
                "SHIP_DATE": _dmonyy(d),
                "ETA_DATE": _dmonyy(eta),
                "ACTUAL_ARRIVAL_DATE": _dmonyy(arrived) if status == "DELIVERED" else "N/A",
                "CONTAINER_NUMBER": _container(
                    ["MSKU", "MAEU", "EGHU", "ONEU"][(i // 2) % 4], 100000 + ((i // 2) * 37) % 800000
                ),
                "GROSS_WEIGHT": weight,
                "WEIGHT_UOM": "LB" if use_lb else "KG",
                "FREIGHT_COST": _money(float(rng.randrange(8, 240) * 100)),
                "FREIGHT_CURRENCY": ["USD", "USD", "EUR", "CNY"][rng.randrange(4)],
                "INCOTERMS": _INCOTERMS[(i + (1 if i % 20 == 0 else 0)) % 6],  # ~5% conflict vs PO
                "SHIPMENT_STATUS": status,
                "HS_CODE": _HS[rng.randrange(len(_HS))],
            }
        )
    df = pd.DataFrame(rows)
    # double-entered legs: identical rows re-keyed under a fresh shipment id
    src = df.index[(df["SHIPMENT_STATUS"] == "DELIVERED") & (df["DESTINATION"] != "MER-DC-NL-01")][:10]
    twins = df.loc[src].copy()
    twins["SHIPMENT_ID"] = [f"SHP-{219800 + t:06d}" for t in range(len(twins))]
    return pd.concat([df, twins], ignore_index=True)


def _headcount_frame(rng: Random, facilities) -> pd.DataFrame:
    rows = []
    for f in facilities:
        base = {
            "RETAIL": 14, "DISTRIBUTION_CENTER": 160, "OFFICE": 220,
            "R&D_LAB": 90, "DATA_CENTER": 24, "3PL": 35,
        }[f["ptype"]]
        depts = [f["dept"]]
        if f["ptype"] == "OFFICE":
            depts = ["G&A", "ENGINEERING"]
            if f["code"].endswith("01"):
                depts.append("CUSTOMER_SUPPORT")
        for dept in depts:
            for si, snap in enumerate(_SNAPSHOTS):
                fte = int(base * (0.92 + 0.03 * si) * (0.8 + (sum(ord(c) for c in f["code"]) % 9) / 20))
                rows.append(
                    {
                        "SNAPSHOT_DATE": snap,
                        "FACILITY_CODE": f["code"],
                        "SITE_NAME": f["site_name"] + (" " if si == 3 and rng.random() < 0.1 else ""),
                        "COUNTRY": f["country"],
                        "DEPARTMENT": dept,
                        "HEADCOUNT_FTE": str(fte),
                        "CONTRACTOR_COUNT": "-" if (si + len(f["code"])) % 10 == 0 else str(max(0, fte // 8 - si)),
                        "OPEN_REQUISITIONS": str((si + sum(ord(c) for c in f["code"])) % 7),
                    }
                )
    df = pd.DataFrame(rows)
    dup = df.iloc[:6].copy()                        # exact duplicate snapshot rows
    return pd.concat([df, dup], ignore_index=True)


def _pos_frame(rng: Random, facilities) -> pd.DataFrame:
    stores = [f for f in facilities if f["ptype"] == "RETAIL"]
    stocked = ["PULSE", "AURA", "SLATE", "HALO", "CORE"]
    eu = {"GB", "DE", "FR", "NL"}
    cur = {"US": "USD", "CA": "CAD", "GB": "GBP", "DE": "EUR", "FR": "EUR", "NL": "EUR", "CN": "CNY", "JP": "JPY", "AU": "AUD"}
    rows = []
    for si, st in enumerate(stores):
        size = 1.0 / (1 + si * 0.18)               # Zipf store sizes
        fams = [f for j, f in enumerate(stocked) if j != si % 5][:4]
        for qi, q in enumerate(_QUARTERS):
            for fam in fams:
                fam_w = {"PULSE": 1.0, "AURA": 0.7, "SLATE": 0.4, "HALO": 0.3, "CORE": 0.35}[fam]
                units = max(8, int(4200 * size * fam_w * (0.9 + 0.08 * qi)))
                asp = {"PULSE": 840.0, "AURA": 1240.0, "SLATE": 520.0, "HALO": 170.0, "CORE": 260.0}[fam]
                usd = round(units * asp * (0.95 + (si + qi) % 5 * 0.02), -1)
                local = usd / {"USD": 1.0, "CAD": 0.74, "GBP": 1.27, "EUR": 1.09, "CNY": 0.14, "JPY": 0.0065, "AUD": 0.66}[cur.get(st["country"], "USD")]
                if st["country"] in eu:
                    amount = _eu_money(round(local, 2))
                elif st["country"] == "US" and si % 11 == 0:
                    amount = f"US${_money(round(local, 2))}"
                else:
                    amount = _money(round(local, 2))
                rows.append(
                    {
                        "FISCAL_QUARTER": q,
                        "STORE_CODE": st["code"],
                        "STORE_NAME": st["site_name"] if si % 6 else st["name"],
                        "PRODUCT_FAMILY": fam,
                        "UNITS_SOLD": str(units),
                        "RETURNS_UNITS": str(units // 40 + rng.randrange(4)),
                        "NET_SALES_AMOUNT": amount,
                        "CURRENCY_CODE": cur.get(st["country"], "USD"),
                        "NET_SALES_USD": _money(usd),
                    }
                )
    df = pd.DataFrame(rows)
    dup = df.iloc[10:20].copy()                     # exact duplicate rows wart
    return pd.concat([df, dup], ignore_index=True)


_TICKET_TEMPLATES = [
    "My {product} stopped {symptom} after the last update. I bought it at {store} and it is only {age} months old.",
    "The {product} I purchased keeps {symptom}. Please advise on warranty options.",
    "Customer reports {symptom} on a {product}. The {component} seems to be the cause.",
    "Hello, my {product} has been {symptom} since last week. Very disappointed.",
    "Device {symptom}. Serial sticker is worn but it is a {product}.",
    "{product} {symptom} during normal use. Requesting a replacement unit.",
    "I went back to {store} but they redirected me here. The {product} is {symptom}.",
    "After charging overnight the {product} started {symptom}. The {component} feels warm.",
    "Our IT team sees the {product} {symptom} on multiple units we deployed.",
    "Gift for my daughter — the {product} arrived {symptom}. Box was intact.",
]
_SYMPTOMS = {
    "BATTERY": ["draining within two hours", "not holding a charge", "shutting down at 30%"],
    "DISPLAY": ["showing green lines", "flickering at low brightness", "going black randomly"],
    "AUDIO": ["crackling at high volume", "losing the left channel", "producing static"],
    "CONNECTIVITY": ["dropping wifi", "failing to pair", "losing 5G signal"],
    "SOFTWARE": ["freezing on the lock screen", "rebooting in a loop", "stuck on the update screen"],
    "SHIPPING_DAMAGE": ["dented on arrival", "delivered with a cracked corner", "missing accessories"],
    "OTHER": ["behaving oddly", "getting warm in standby", "rattling when shaken"],
}
_SWELLING_TEMPLATES = [
    "I noticed battery swelling on my {product} — the back cover is lifting near the camera.",
    "There is visible battery swelling and the screen is being pushed out of the frame on my {product}.",
    "My {product} shows clear battery swelling after about a year of use. The case no longer closes flat.",
    "Reporting battery swelling: the {product} bulges in the middle and feels spongy.",
]


def _tickets_frame(rng: Random, products, facilities) -> pd.DataFrame:
    sellable = [p for p in products if p["family"] != "ACCESSORY"]
    stores = [f for f in facilities if f["ptype"] == "RETAIL"]
    hero = next(p for p in sellable if p["sku"] == "MER-PLS9P-256-BLK")
    rows = []
    start = date(2025, 6, 15)

    def raw_desc(p, i) -> str:
        forms = [
            p["name"].replace("Meridian ", "").lower(),
            p["name"],
            p["name"].replace("Meridian ", "").replace(" ", "") + (f" {p['storage']}" if p["storage"] else ""),
            "my meridian " + {"PULSE": "phone", "AURA": "laptop", "SLATE": "tablet", "HALO": "speaker", "CORE": "watch"}[p["family"]],
            p["name"].replace("Meridian ", "").lower().replace("ul", "ull", 1),  # misspelling
        ]
        return forms[i % len(forms)]

    def serial(p, i) -> str:
        prefix = (p["model"] + "00000")[:5]
        return f"{prefix}{(7919 * i) % 100000:05d}{'ABCDEFGHKMNPQRSTUVWXYZ'[i % 22]}"

    for i in range(1419):
        p = sellable[(i * 13) % len(sellable)]
        ts_src = i if i % 23 or i == 0 else i - 1  # ~4% shared timestamps wart
        created = start + timedelta(days=(ts_src * 3) % 362)
        issue = _TICKET_ISSUES[rng.randrange(len(_TICKET_ISSUES))]
        store = stores[(i * 11) % len(stores)]
        tmpl = _TICKET_TEMPLATES[rng.randrange(len(_TICKET_TEMPLATES))]
        symptom = _SYMPTOMS[issue][rng.randrange(len(_SYMPTOMS[issue]))]
        component = {"BATTERY": "battery", "DISPLAY": "display panel", "AUDIO": "speaker", "CONNECTIVITY": "antenna", "SOFTWARE": "firmware", "SHIPPING_DAMAGE": "packaging", "OTHER": "unit"}[issue]
        mention_product = i % 20 < 17                # ~85% product-mention rate
        desc = tmpl.format(
            product=(raw_desc(p, i) if mention_product else "device"),
            symptom=symptom,
            component=component if i % 10 < 3 else "unit",   # ~30% component mention
            store=store["site_name"] if i % 7 < 1 else "the store",  # ~15%
            age=1 + i % 23,
        )
        resolved = i % 5 != 1
        rows.append(
            {
                "TICKET_ID": f"TCK-{300000 + i:06d}",
                "CREATED_TS": f"{created.isoformat()}T{8 + ts_src % 11:02d}:{(ts_src * 7) % 60:02d}:00{_TZ[ts_src % len(_TZ)]}",
                "CHANNEL": _TICKET_CHANNELS[rng.randrange(4)],
                "CUSTOMER_COUNTRY": store["country"],
                "PRODUCT_DESCRIPTION_RAW": "" if i % 13 == 0 else raw_desc(p, i + 1),
                "SERIAL_NUMBER": "" if i % 12 == 0 else serial(p, i),
                "PURCHASE_STORE": store["site_name"] if i % 7 < 1 else "",
                "ISSUE_CATEGORY": issue,
                "SUBJECT": f"{issue.title().replace('_', ' ')} issue with {p['name'].replace('Meridian ', '')}",
                "DESCRIPTION": desc,
                "STATUS": _TICKET_STATUS[rng.randrange(4)],
                "RESOLUTION_CODE": _RESOLUTIONS[rng.randrange(4)] if resolved else "",
                "RESOLVED_DATE": (created + timedelta(days=2 + i % 9)).isoformat() if resolved else ["NULL", "", "N/A"][i % 3],
                "CSAT_SCORE": "" if i % 6 == 0 else ("NULL" if i % 17 == 0 else str(1 + (i * 3) % 5)),
            }
        )
    # the planted battery-swelling cluster: 31 tickets in the last 90 days,
    # all on the hero model, all containing the exact SWELLING_PHRASE
    for j in range(31):
        created = date(2026, 3, 20) + timedelta(days=(j * 8) % 80)
        rows.append(
            {
                "TICKET_ID": f"TCK-{309000 + j:06d}",
                "CREATED_TS": f"{created.isoformat()}T{9 + j % 9:02d}:{(j * 11) % 60:02d}:00{_TZ[j % len(_TZ)]}",
                "CHANNEL": _TICKET_CHANNELS[j % 4],
                "CUSTOMER_COUNTRY": ["US", "DE", "NL", "JP", "GB"][j % 5],
                "PRODUCT_DESCRIPTION_RAW": ["pulse 9 pro", "Pulse9Pro 256 black", "my meridian phone", "Pulse 9 Pro"][j % 4],
                "SERIAL_NUMBER": serial(hero, 600 + j),
                "PURCHASE_STORE": "",
                "ISSUE_CATEGORY": "BATTERY",
                "SUBJECT": "Back cover lifting / battery concern on Pulse 9 Pro",
                "DESCRIPTION": _SWELLING_TEMPLATES[j % len(_SWELLING_TEMPLATES)].format(
                    product=["Pulse 9 Pro", "pulse 9 pro", "Meridian Pulse 9 Pro"][j % 3]
                ),
                "STATUS": ["NEW", "IN_PROGRESS"][j % 2],
                "RESOLUTION_CODE": "",
                "RESOLVED_DATE": ["NULL", "", "N/A"][j % 3],
                "CSAT_SCORE": ["1", "2", "", "1"][j % 4],
            }
        )
    return pd.DataFrame(rows)


# ------------------------------------------------------------ frame assembly


def build_frames(seed: int = SEED) -> dict[str, pd.DataFrame]:
    """The full corpus as string DataFrames keyed by table name (file stem)."""
    rng = Random(seed)
    suppliers = _suppliers()
    parts = _parts(rng, suppliers)
    facilities = _facilities(rng)
    products = _products(rng)

    contracts = _contracts_frame(rng, suppliers)
    po = _po_frame(rng, suppliers, parts, facilities)
    qn = _qn_frame(rng, suppliers, parts, po, facilities)
    po_public = po.drop(columns=["_vendor_idx"])
    frames = {
        "supplier_contracts": contracts,
        "purchase_order_lines": po_public,
        "quality_notifications": qn,
        "products": _products_frame(products),
        "bom_components": _bom_frame(rng, products, parts),
        "leases": _lease_frames(rng, facilities),
        "shipments": _shipments_frame(rng, po_public, facilities),
        "site_headcount": _headcount_frame(rng, facilities),
        "retail_pos_sales": _pos_frame(rng, facilities),
        "support_tickets": _tickets_frame(rng, products, facilities),
    }
    for name, df in frames.items():
        frames[name] = df.astype(str)
    _assert_key_discipline(frames)
    return frames


_INTENDED_KEYS = {
    "supplier_contracts": ("CONTRACT_NUMBER",),
    "purchase_order_lines": ("PO_NUMBER", "PO_LINE_ITEM"),
    "quality_notifications": ("NOTIFICATION_ID",),
    "products": ("SKU", "GTIN"),
    "bom_components": ("BOM_ID",),
    "leases": ("LEASE_ID", "AMENDMENT_NO"),
    "shipments": ("SHIPMENT_ID",),
    "site_headcount": (),     # 3-column grain: deliberately keyless (<=2-col cap)
    "retail_pos_sales": (),   # 3-column grain: deliberately keyless
    "support_tickets": ("TICKET_ID",),
}


def _assert_key_discipline(frames: Mapping[str, pd.DataFrame]) -> None:
    """No accidental near-unique non-key column may compete with the intended
    key (the profiler picks minimal-arity keys; a stray unique measure column
    would hijack row identity)."""
    for table, df in frames.items():
        intended = set(_INTENDED_KEYS[table])
        n = len(df)
        for col in df.columns:
            if col in intended or (table == "products" and col == "PRODUCT_NAME"):
                continue
            nu = df[col].nunique()
            assert nu < 0.98 * n, f"{table}.{col} is accidentally unique ({nu}/{n})"
        for key in _INTENDED_KEYS[table]:
            assert key in df.columns
        if len(intended) == 1:
            (key,) = intended
            assert df[key].nunique() == n, f"{table}.{key} must be unique"


# ----------------------------------------------------------------- gold spec


def compute_gold(tables: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    """Gold answers computed with pandas from the (possibly subsampled) string
    frames, re-parsing values exactly the way the conformance layer does."""
    po = tables["purchase_order_lines"]
    qn = tables["quality_notifications"]
    leases = tables["leases"]
    ships = tables["shipments"]
    tickets = tables["support_tickets"]
    products = tables["products"]
    contracts = tables["supplier_contracts"]

    hero_supplier = "Hailong Precision Industry Co., Ltd."

    g: dict[str, Any] = {}
    g["MQ-01"] = int(
        tickets["DESCRIPTION"].str.casefold().str.contains(SWELLING_PHRASE, regex=False).sum()
    )
    row = contracts.loc[contracts["CONTRACT_NUMBER"] == "MSA-2024-0117"]
    g["MQ-02"] = _parse_num(row["ANNUAL_COMMITTED_SPEND"].iloc[0]) if len(row) else None
    hero_rows = po.loc[po["SUPPLIER_NAME"] == hero_supplier, "NET_VALUE_USD"]
    g["MQ-03"] = round(sum(v for v in (_parse_num(x) for x in hero_rows) if v is not None), 2)
    g["MQ-04"] = int(
        ((ships["TRANSPORT_MODE"] == "OCEAN") & (ships["SHIPMENT_STATUS"] == "CUSTOMS_HOLD")).sum()
    )
    nl = ships.loc[ships["DESTINATION"].str.strip().str.upper() == "MER-DC-NL-01", "GROSS_WEIGHT"]
    g["MQ-05"] = round(sum(v for v in (_parse_weight_kg(x) for x in nl) if v is not None), 4)
    g["MQ-06"] = int(
        (
            (leases["PROPERTY_TYPE"] == "RETAIL")
            & (leases["EXPIRATION_DATE"] > "2026-06-12")
            & (leases["EXPIRATION_DATE"] < "2027-06-12")
        ).sum()
    )
    prow = products.loc[products["SKU"] == "MER-PLS9P-256-BLK"]
    g["MQ-07"] = _parse_num(prow["MSRP_USD"].iloc[0]) if len(prow) else None
    csat = [
        v
        for v in (
            _parse_num(x)
            for x in tickets.loc[tickets["ISSUE_CATEGORY"] == "BATTERY", "CSAT_SCORE"]
        )
        if v is not None
    ]
    g["MQ-08"] = round(sum(csat) / len(csat), 6) if csat else None
    rejected = qn["QTY_REJECTED"].map(_parse_num)
    g["MQ-09"] = int(((qn["STATUS"] == "OPEN") & (rejected > 100)).sum())
    g["MQ-10"] = None
    g["MQ-11"] = None
    g["MQ-12"] = None
    return g


def gold_questions(tables: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    """The full gold artifact (dict ready for yamlite emission)."""
    g = compute_gold(tables)
    naive_dsp = _naive_display_prices(tables["purchase_order_lines"])
    questions = [
        {
            "id": "MQ-01",
            "kinds": ["text_mention", "aggregation"],
            "pattern": "G5 text mention -> supply chain trace (count leg)",
            "question": "How many support tickets describe battery swelling?",
            "answerable": True,
            "expected_behavior": "answer",
            "answer": g["MQ-01"],
            "evidence_tables": ["support_tickets"],
            "notes": "planted cluster: every qualifying DESCRIPTION contains the exact "
                     "phrase 'battery swelling'; distractor battery tickets use other "
                     "wordings (swollen / bulging). All cluster tickets sit on model "
                     "PLS9P whose battery part CMP-BAT-0118 has OPEN quality "
                     "notifications against Volta Energy (the full G5 trace).",
        },
        {
            "id": "MQ-02",
            "kinds": ["lookup"],
            "pattern": "G9 contract compliance (commitment leg)",
            "question": "What is the annual committed spend on contract MSA-2024-0117?",
            "answerable": True,
            "expected_behavior": "answer",
            "answer": g["MQ-02"],
            "evidence_tables": ["supplier_contracts"],
            "notes": "USD-denominated commitment; comma-grouped source string.",
        },
        {
            "id": "MQ-03",
            "kinds": ["aggregation", "name_variants"],
            "pattern": "G2 quality-weighted spend (spend leg)",
            "question": "What is the total net value USD of purchase order lines with "
                        "supplier name 'Hailong Precision Industry Co., Ltd.'?",
            "answerable": True,
            "expected_behavior": "answer",
            "answer": g["MQ-03"],
            "evidence_tables": ["purchase_order_lines", "supplier_contracts"],
            "notes": "NET_VALUE_USD is the in-corpus FX bridge (~30% of rows carry a "
                     "'USD ' prefix). Hero supplier PO rows are wart-free by design; "
                     "tail-vendor rows carry spelling variants and stripped vendor ids "
                     "(recover: zero-pad VENDOR_ID to 10 digits).",
        },
        {
            "id": "MQ-04",
            "kinds": ["aggregation"],
            "pattern": "G6 logistics x purchasing (status leg)",
            "question": "How many shipments with transport mode 'OCEAN' are in shipment "
                        "status 'CUSTOMS_HOLD'?",
            "answerable": True,
            "expected_behavior": "answer",
            "answer": g["MQ-04"],
            "evidence_tables": ["shipments"],
            "notes": "DD-MON-YY Oracle-style dates and ~5% PO/shipment incoterm "
                     "conflicts live on this table; the count itself is clean.",
        },
        {
            "id": "MQ-05",
            "kinds": ["aggregation", "unit_mix"],
            "pattern": "G8 facility throughput (unit-normalized weight leg)",
            "question": "What is the total gross weight in kg of shipments with "
                        "destination MER-DC-NL-01?",
            "answerable": True,
            "expected_behavior": "answer",
            "answer": g["MQ-05"],
            "evidence_tables": ["shipments", "leases"],
            "notes": "GROSS_WEIGHT mixes 'kg' and 'lb' lexical forms (WEIGHT_UOM "
                     "mirrors them); 1 lb = 0.45359237 kg. Hero DC rows always carry "
                     "the exact facility code in DESTINATION; other DCs carry name "
                     "variants ('Tilburg DC', 'MER DC US01') — the documented "
                     "facility-resolution wart.",
        },
        {
            "id": "MQ-06",
            "kinds": ["temporal_as_of", "aggregation"],
            "pattern": "G3 lease expiry exposure (lease leg)",
            "question": "How many leases with property type 'RETAIL' are expiring "
                        "before 2027-06-12 and after 2026-06-12?",
            "answerable": True,
            "expected_behavior": "answer",
            "answer": g["MQ-06"],
            "evidence_tables": ["leases"],
            "notes": "as-of discipline: expired predecessor leases fall out of the "
                     "window on the left edge; superseded amendment rows are explicitly "
                     "LEASE_STATUS=SUPERSEDED and the in-window leases are unamended, "
                     "so lease grain equals row grain inside the window. EU/CN rows "
                     "write LEASE_COMMENCEMENT_DATE as DD.MM.YYYY (recover: day-first "
                     "locale); EXPIRATION_DATE stays ISO.",
        },
        {
            "id": "MQ-07",
            "kinds": ["lookup"],
            "pattern": "G10 economics (catalog leg)",
            "question": "What is the MSRP USD of the product with SKU MER-PLS9P-256-BLK?",
            "answerable": True,
            "expected_behavior": "answer",
            "answer": g["MQ-07"],
            "evidence_tables": ["products"],
            "notes": "GTIN-14 column carries valid GS1 check digits; STORAGE_GB mixes "
                     "'' and 'N/A' null tokens on non-storage products.",
        },
        {
            "id": "MQ-08",
            "kinds": ["aggregation", "null_tokens"],
            "pattern": "G5 affected-product follow-up (satisfaction leg)",
            "question": "What is the average csat score of support tickets with issue "
                        "category 'BATTERY'?",
            "answerable": True,
            "expected_behavior": "answer",
            "answer": g["MQ-08"],
            "evidence_tables": ["support_tickets"],
            "notes": "CSAT_SCORE mixes '', 'NULL' null tokens with 1-5 scores; the mean "
                     "is over non-null scores only (blank-vs-zero discipline).",
        },
        {
            "id": "MQ-09",
            "kinds": ["aggregation", "comparison"],
            "pattern": "G2 worst-supplier quality (reject-volume leg)",
            "question": "How many quality notifications with status OPEN have quantity "
                        "rejected above 100?",
            "answerable": True,
            "expected_behavior": "answer",
            "answer": g["MQ-09"],
            "evidence_tables": ["quality_notifications"],
            "notes": "SUPPLIER is name-only on this table (3-5 spelling variants per "
                     "top supplier; resolvable via PO_REFERENCE on ~40% of rows or "
                     "fuzzy match to SUPPLIER_LEGAL_NAME); the planted battery cluster "
                     "is part of the qualifying set.",
        },
        {
            "id": "MQ-10",
            "kinds": ["unanswerable"],
            "pattern": "G11 customer churn by region",
            "question": "What is our customer churn rate by region this year?",
            "answerable": False,
            "expected_behavior": "abstain",
            "answer": None,
            "evidence_tables": [],
            "notes": "no customer registry, activation data, or installed-base "
                     "denominator exists anywhere in the corpus; SUPPORT_TICKETS are "
                     "anonymous events. Any numeric answer scores zero.",
        },
        {
            "id": "MQ-11",
            "kinds": ["unanswerable"],
            "pattern": "G12 owned-factory book value",
            "question": "Which of our factories do we own outright, and what is their "
                        "combined net book value?",
            "answerable": False,
            "expected_behavior": "abstain",
            "answer": None,
            "evidence_tables": [],
            "notes": "manufacturing is outsourced to supplier plants; the only "
                     "real-estate system is LEASES (leased premises only) and no "
                     "fixed-asset register or ownership flag exists in any table.",
        },
        {
            "id": "MQ-12",
            "kinds": ["trick_unit"],
            "pattern": "G7 trick-unit: display module pricing",
            "question": "What is the total net price in dollars across purchase order "
                        "lines for material CMP-DSP-0451?",
            "answerable": False,
            "expected_behavior": "reject_unit_mismatch",
            "answer": None,
            "evidence_tables": ["purchase_order_lines"],
            "notes": "NET_PRICE is a multi-currency document-currency amount quoted per "
                     "PRICE_UNIT (1|10|100|1000) with ORDER_UOM mixing EA and KPC "
                     "(thousand pieces): no declared unit exists, so expressing its sum "
                     "'in dollars' must be rejected, not coerced. The naive sums a "
                     "coercing engine would produce are pinned below for grading; the "
                     "correct per-unit economics use NET_VALUE_USD / qty_each with "
                     "qty_each = ORDER_QTY*1000 when ORDER_UOM=KPC.",
            "naive_wrong_values": naive_dsp,
        },
    ]
    return {
        "estate": "meridian",
        "version": 1,
        "generator_seed": SEED,
        "as_of_date": "2026-06-12",
        "conventions": {
            "engine": "GENERIC ONLY: ontoforge init -p <dir> --source fixtures/meridian "
                      "(no estate module, no gold ontology)",
            "row_key": "values of the table's candidate-key columns joined with '|'; "
                       "keyless snapshot tables (site_headcount, retail_pos_sales) use "
                       "the CDC content-addressed fallback",
            "vendor_id_recovery": "VENDOR_ID with dropped leading zeros zero-pads to 10 "
                                  "digits (affects ~6% of PO rows on 12 designated "
                                  "mid-tail vendors; never on gold-question suppliers)",
            "area_uom_recovery": "blank AREA_UOM resolves by country: US/CA=SF, "
                                 "everywhere else=SQM (1 SQM = 10.7639 SF)",
            "weight_uom_recovery": "GROSS_WEIGHT carries its lexical unit; 1 lb = "
                                   "0.45359237 kg; WEIGHT_UOM mirrors the suffix",
            "date_locales": "masters ISO 8601; lease commencements DD.MM.YYYY on EU/CN "
                            "rows; shipments DD-MON-YY; tickets ISO 8601 with mixed "
                            "timezone offsets",
            "null_tokens": ["", "NULL", "N/A", "-", "TBD", "9999-12-31"],
            "kpc_trap": "ORDER_UOM=KPC means thousand pieces; NET_PRICE is per "
                        "PRICE_UNIT pieces in document currency; NET_VALUE_USD is the "
                        "group-currency bridge",
            "fiscal_calendar": "Meridian fiscal year starts in October: FY2026-Q1 = "
                               "Oct-Dec 2025",
            "supplier_resolution": "quality SUPPLIER strings resolve via PO_REFERENCE "
                                   "-> VENDOR_ID first, fuzzy legal-name match second; "
                                   "6 quality-only suppliers are deliberate orphans",
            "float_matching": "numeric answers compare at relative tolerance 1e-6",
        },
        "questions": questions,
    }


def _naive_display_prices(po: pd.DataFrame) -> list[float]:
    """The top naive WRONG values for the trick-unit question (grading aid)."""
    rows = po.loc[po["MATERIAL_NUMBER"] == "CMP-DSP-0451"]
    prices = [v for v in (_parse_num(x) for x in rows["NET_PRICE"]) if v is not None]
    if not prices:
        return []
    naive_avg = round(sum(prices) / len(prices), 2)
    naive_sum = round(sum(prices), 2)
    return [naive_avg, naive_sum]


# ------------------------------------------------------------------- emission


def build_corpus(out_dir: str | Path, seed: int = SEED) -> dict[str, Any]:
    """Generate the corpus into ``out_dir`` (CSVs + gold/questions.yaml +
    README.md). Returns a manifest with file sizes and pinned answers."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "gold").mkdir(exist_ok=True)
    frames = build_frames(seed)
    sizes: dict[str, int] = {}
    for name, df in frames.items():
        path = out / f"{name}.csv"
        df.to_csv(path, index=False, lineterminator="\n", encoding="utf-8")
        sizes[path.name] = path.stat().st_size
    gold = gold_questions(frames)
    (out / "gold" / "questions.yaml").write_text(yamlite.dumps(gold), encoding="utf-8")
    readme = _readme(frames, sizes, gold)
    (out / "README.md").write_text(readme, encoding="utf-8")
    total = sum(sizes.values())
    assert total < MAX_TOTAL_BYTES, f"corpus too large: {total} bytes"
    return {
        "seed": seed,
        "files": sizes,
        "total_bytes": total,
        "rows": {name: len(df) for name, df in frames.items()},
        "answers": {q["id"]: q["answer"] for q in gold["questions"]},
    }


def _readme(frames, sizes, gold) -> str:
    lines = [
        "# Meridian demo corpus",
        "",
        "Synthetic 10-table enterprise estate for the OntoForge GENERIC engine",
        "demo (`ontoforge demo meridian <project_dir>`). Generated by",
        "`ontoforge.estates.meridian_gen` with seed "
        f"{gold['generator_seed']} — byte-reproducible; do not hand-edit.",
        "",
        "| table | rows | bytes |",
        "|---|---|---|",
    ]
    for name, df in frames.items():
        lines.append(f"| {name} | {len(df)} | {sizes[f'{name}.csv']} |")
    lines += [
        "",
        "Gold: `gold/questions.yaml` — 12 CEO-grade questions (9 answerable, 2",
        "unanswerable, 1 trick-unit) with pandas-computed pinned answers and the",
        "documented wart-recovery conventions.",
        "",
        "Regenerate: `python scripts/build_meridian_corpus.py` (or",
        "`python -m ontoforge.estates.meridian_gen <out_dir>`).",
        "",
    ]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Generate the Meridian demo corpus.")
    parser.add_argument("out_dir", nargs="?", default="fixtures/meridian")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    manifest = build_corpus(args.out_dir, seed=args.seed)
    print(json.dumps(manifest, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

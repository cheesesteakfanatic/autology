#!/usr/bin/env python3
"""Deterministic builder for the aviation hero-estate fixtures.

Whitepaper §12.4, §17.2.1, §17.4 Tier-2; AMD-0006 (schema-faithful generated
fixtures where live downloads are blocked).

Outputs (all under ``fixtures/aviation/``, total < 5 MB):

- ``faa_master.csv``    — FAA ReleasableAircraft MASTER layout (~2500 rows),
  fixed-width trailing-space padding warts, manufacturer-name variants via the
  ACFTREF join, blank permissible fields, and N-numbers REUSED across different
  airframes with non-overlapping registration windows (temporal-identity trap).
- ``faa_acftref.csv``   — ACFTREF aircraft-reference layout (~120 rows).
- ``asrs_reports.csv``  — ASRS-shaped incident reports (~350 rows) with
  synthesized narratives mentioning registry tail numbers, operators, airports
  and event types; a wart slice of altitudes is recorded in METERS ('m' suffix).
- ``ntsb_events.csv``   — NTSB-shaped events (~200 rows); some registration
  numbers have the leading 'N' dropped.
- ``maintenance_erp.csv`` — synthetic ERP work orders (~600 rows); COST mixes
  'USD 1,234.56' and '1234.56' lexical forms (ANVIL bait).
- ``gold/er_gold_pairs.csv``        — true cross-source same-entity pairs,
  emitted AS the data is generated (so they are correct by construction).
- ``gold/mini_ontology.json``       — frozen gold mini-ontology (17 classes),
  loadable via ``ontoforge.estates.gold.load_gold_ontology``.
- ``gold/competency_questions.yaml``— 18 questions with generator-computed gold
  answers and per-answer source-cell citations (table, row_key, column).

Determinism: a single ``random.Random(42)`` drives every stochastic choice in a
fixed order; no wall-clock, no set iteration, csv lineterminator '\\n'. Two runs
are byte-identical (tested).

Real-data seeds: OpenFlights ``airports.dat``/``planes.dat`` downloads succeeded
and are pinned (trimmed) under ``fixtures/aviation/_seed/`` (see MANIFEST.json).
The FAA registry returns 403 to non-browser clients and the NTSB bulk avall.zip
is a ~95 MB Access database — both out of budget, hence generation per AMD-0006.
``--refresh-seeds`` re-downloads and re-trims the seeds (network; never in CI).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from ontoforge.estates import yamlite  # noqa: E402

SEED = 42
FT_PER_M = 3.28084

DEFAULT_OUT = REPO / "fixtures" / "aviation"
DEFAULT_SEED_DIR = DEFAULT_OUT / "_seed"

# --------------------------------------------------------------------------
# vocabularies
# --------------------------------------------------------------------------

# manufacturer key -> spellings used in ACFTREF (the FAA's own name-variant
# wart: the same maker appears under multiple spellings, per §17.2.1).
MANUFACTURERS: dict[str, dict] = {
    "AIRBUS": {"acftref": ["AIRBUS", "AIRBUS INDUSTRIE"], "ntsb": ["AIRBUS", "Airbus"], "title": "Airbus"},
    "BEECH": {"acftref": ["BEECH"], "ntsb": ["BEECH", "Beechcraft"], "title": "Beechcraft"},
    "BELL": {"acftref": ["BELL", "BELL HELICOPTER TEXTRON"], "ntsb": ["BELL"], "title": "Bell"},
    "BOEING": {"acftref": ["BOEING", "THE BOEING COMPANY"], "ntsb": ["BOEING", "Boeing"], "title": "Boeing"},
    "BOMBARDIER": {"acftref": ["BOMBARDIER INC"], "ntsb": ["BOMBARDIER"], "title": "Bombardier"},
    "CESSNA": {"acftref": ["CESSNA", "CESSNA AIRCRAFT CO"], "ntsb": ["CESSNA", "Cessna"], "title": "Cessna"},
    "CIRRUS": {"acftref": ["CIRRUS DESIGN CORP"], "ntsb": ["CIRRUS"], "title": "Cirrus"},
    "DEHAVILLAND": {"acftref": ["DE HAVILLAND", "DEHAVILLAND"], "ntsb": ["DE HAVILLAND"], "title": "De Havilland"},
    "DIAMOND": {"acftref": ["DIAMOND AIRCRAFT IND INC"], "ntsb": ["DIAMOND"], "title": "Diamond"},
    "EMBRAER": {"acftref": ["EMBRAER", "EMBRAER S A"], "ntsb": ["EMBRAER"], "title": "Embraer"},
    "GULFSTREAM": {"acftref": ["GULFSTREAM AEROSPACE"], "ntsb": ["GULFSTREAM"], "title": "Gulfstream"},
    "MAULE": {"acftref": ["MAULE"], "ntsb": ["MAULE"], "title": "Maule"},
    "MOONEY": {"acftref": ["MOONEY AIRCRAFT CORP"], "ntsb": ["MOONEY"], "title": "Mooney"},
    "PIPER": {"acftref": ["PIPER", "PIPER AIRCRAFT INC"], "ntsb": ["PIPER", "Piper"], "title": "Piper"},
    "ROBINSON": {"acftref": ["ROBINSON HELICOPTER CO"], "ntsb": ["ROBINSON"], "title": "Robinson"},
    "ROCKWELL": {"acftref": ["ROCKWELL INTERNATIONAL CORP", "ROCKWELL INTL"], "ntsb": ["ROCKWELL"], "title": "Rockwell"},
}

# fleet mix (rough GA-heavy registry shape)
MFR_WEIGHTS = {
    "CESSNA": 26, "PIPER": 18, "BEECH": 9, "BOEING": 7, "CIRRUS": 6,
    "AIRBUS": 5, "MOONEY": 4, "ROBINSON": 4, "DIAMOND": 4, "EMBRAER": 4,
    "BELL": 3, "BOMBARDIER": 3, "GULFSTREAM": 2, "DEHAVILLAND": 2,
    "MAULE": 2, "ROCKWELL": 1,
}

# embedded model supplement: (mfr_key, MODEL, kind)
# kinds: ga (single recip), ga_twin, turboprop, jet, bizjet, heli
EMBEDDED_MODELS: list[tuple[str, str, str]] = [
    ("CESSNA", "152", "ga"), ("CESSNA", "172N", "ga"), ("CESSNA", "172S", "ga"),
    ("CESSNA", "182T", "ga"), ("CESSNA", "210L", "ga"), ("CESSNA", "310R", "ga_twin"),
    ("CESSNA", "208B", "turboprop"), ("CESSNA", "421C", "ga_twin"),
    ("PIPER", "PA-28-181", "ga"), ("PIPER", "PA-28-161", "ga"), ("PIPER", "PA-18-150", "ga"),
    ("PIPER", "PA-32R-301", "ga"), ("PIPER", "PA-34-220T", "ga_twin"),
    ("PIPER", "PA-31-350", "ga_twin"), ("PIPER", "PA-46-500TP", "turboprop"),
    ("BEECH", "A36", "ga"), ("BEECH", "V35B", "ga"), ("BEECH", "58", "ga_twin"),
    ("BEECH", "B200", "turboprop"), ("BEECH", "1900D", "turboprop"),
    ("CIRRUS", "SR20", "ga"), ("CIRRUS", "SR22", "ga"), ("CIRRUS", "SR22T", "ga"),
    ("MOONEY", "M20J", "ga"), ("MOONEY", "M20K", "ga"), ("MOONEY", "M20R", "ga"),
    ("DIAMOND", "DA20-C1", "ga"), ("DIAMOND", "DA40", "ga"), ("DIAMOND", "DA42", "ga_twin"),
    ("MAULE", "M-7-235", "ga"), ("MAULE", "MX-7-180", "ga"),
    ("ROBINSON", "R22 BETA", "heli"), ("ROBINSON", "R44 II", "heli"), ("ROBINSON", "R66", "heli"),
    ("BELL", "206B", "heli"), ("BELL", "407", "heli"), ("BELL", "429", "heli"),
    ("ROCKWELL", "COMMANDER 112A", "ga"), ("ROCKWELL", "COMMANDER 114", "ga"),
    ("ROCKWELL", "COMMANDER 690B", "turboprop"), ("ROCKWELL", "SABRELINER 65", "bizjet"),
    ("ROCKWELL", "S-2R", "ga"), ("ROCKWELL", "COMMANDER 685", "ga_twin"),
    ("GULFSTREAM", "G-IV", "bizjet"), ("GULFSTREAM", "G550", "bizjet"), ("GULFSTREAM", "G650ER", "bizjet"),
    ("EMBRAER", "PHENOM 300", "bizjet"), ("EMBRAER", "EMB-505", "bizjet"),
    ("BOMBARDIER", "CL-600-2B16", "bizjet"), ("BOMBARDIER", "BD-700-1A10", "bizjet"),
    ("BOMBARDIER", "LEARJET 45", "bizjet"),
    ("DEHAVILLAND", "DHC-2 MK I", "ga"), ("DEHAVILLAND", "DHC-6-300", "turboprop"),
    ("DEHAVILLAND", "DHC-8-402", "turboprop"),
    ("BOEING", "737-7H4", "jet"), ("BOEING", "737-8H4", "jet"), ("BOEING", "757-232", "jet"),
    ("BOEING", "767-332", "jet"), ("BOEING", "777-232", "jet"),
    ("AIRBUS", "A319-114", "jet"), ("AIRBUS", "A320-214", "jet"), ("AIRBUS", "A321-211", "jet"),
    ("EMBRAER", "ERJ 170-200 LR", "jet"), ("EMBRAER", "ERJ 190-100 IGW", "jet"),
    ("BOMBARDIER", "CL-600-2C10", "jet"),
]

# OpenFlights planes.dat name prefixes -> manufacturer key (longest first)
SEED_PLANE_PREFIXES: list[tuple[str, str]] = [
    ("De Havilland Canada ", "DEHAVILLAND"),
    ("Boeing ", "BOEING"),
    ("Airbus ", "AIRBUS"),
    ("Embraer ", "EMBRAER"),
    ("Cessna ", "CESSNA"),
    ("Piper ", "PIPER"),
    ("Gulfstream ", "GULFSTREAM"),
    ("Bombardier ", "BOMBARDIER"),
    ("Canadair ", "BOMBARDIER"),
    ("Beechcraft ", "BEECH"),
    ("Bell ", "BELL"),
]
SEED_PLANE_CAPS = {
    "BOEING": 10, "AIRBUS": 8, "EMBRAER": 5, "CESSNA": 8, "PIPER": 4,
    "GULFSTREAM": 4, "BOMBARDIER": 6, "BEECH": 3, "DEHAVILLAND": 6, "BELL": 3,
}

# ENG MFR MDL: (code, maker, model, TYPE-ENGINE code) — 1 recip, 2 turboprop,
# 3 turboshaft, 5 turbofan
ENGINES = {
    "ga": [("17003", "LYCOMING", "O-320 SERIES", "1"), ("17027", "LYCOMING", "IO-360 SER", "1"),
           ("30010", "CONT MOTOR", "O-200 SERIES", "1"), ("30062", "CONT MOTOR", "IO-550 SER", "1")],
    "ga_twin": [("17041", "LYCOMING", "TIO-540 SER", "1"), ("30062", "CONT MOTOR", "IO-550 SER", "1")],
    "turboprop": [("41514", "P&W CANADA", "PT6A SERIES", "2"), ("41538", "P&W CANADA", "PT6A-67D", "2")],
    "jet": [("67000", "CFM INTL", "CFM56 SERIES", "5"), ("52001", "GE", "CF34 SERIES", "5"),
            ("41060", "P&W", "PW2037", "5")],
    "bizjet": [("52040", "GE", "CF700-2D", "5"), ("41545", "P&W CANADA", "PW306C", "5"),
               ("00410", "BMW ROLLS", "BR700-710A1-10", "5")],
    "heli": [("17050", "LYCOMING", "O-320-B2C", "1"), ("60012", "ALLISON", "250-C20B", "3"),
             ("60515", "ROLLS-ROYC", "250-C47B", "3")],
}

# operators: registry/asrs/ntsb/erp spellings (first registry spelling is the
# canonical representative). ASRS lists include deliberate misspellings.
OPERATORS: list[dict] = [
    {"id": "OP-001", "kind": "airline", "hq": ("ATL", "GA"),
     "registry": ["DELTA AIR LINES INC", "DELTA AIR LINES, INC."],
     "asrs": ["Delta Air Lines", "Delta Airlines", "Detla Air Lines"],
     "ntsb": ["DELTA AIR LINES INC", "DELTA AIRLINES"],
     "erp": ["Delta Air Lines", "DELTA AIR LINES INC.", "Delta Airlines Inc"]},
    {"id": "OP-002", "kind": "airline", "hq": ("ORD", "IL"),
     "registry": ["UNITED AIRLINES INC", "UNITED AIR LINES INC"],
     "asrs": ["United Airlines", "Untied Airlines"],
     "ntsb": ["UNITED AIRLINES INC", "UNITED AIR LINES"],
     "erp": ["United Airlines", "UNITED AIRLINES, INC."]},
    {"id": "OP-003", "kind": "airline", "hq": ("DAL", "TX"),
     "registry": ["SOUTHWEST AIRLINES CO"],
     "asrs": ["Southwest Airlines", "South West Airlines"],
     "ntsb": ["SOUTHWEST AIRLINES CO", "SOUTHWEST AIRLINES"],
     "erp": ["Southwest Airlines Co", "Southwest Airlines"]},
    {"id": "OP-004", "kind": "airline", "hq": ("DFW", "TX"),
     "registry": ["AMERICAN AIRLINES INC"],
     "asrs": ["American Airlines", "Americian Airlines"],
     "ntsb": ["AMERICAN AIRLINES INC"],
     "erp": ["American Airlines", "AMERICAN AIRLINES INC."]},
    {"id": "OP-005", "kind": "cargo", "hq": ("MEM", "TN"),
     "registry": ["FEDERAL EXPRESS CORP"],
     "asrs": ["FedEx Express", "Federal Express"],
     "ntsb": ["FEDERAL EXPRESS CORP", "FEDEX EXPRESS"],
     "erp": ["FedEx Express", "Federal Express Corp"]},
    {"id": "OP-006", "kind": "cargo", "hq": ("SDF", "KY"),
     "registry": ["UNITED PARCEL SERVICE CO"],
     "asrs": ["UPS Airlines", "United Parcel Service"],
     "ntsb": ["UNITED PARCEL SERVICE CO"],
     "erp": ["UPS Airlines", "United Parcel Service Co"]},
    {"id": "OP-007", "kind": "regional", "hq": ("SLC", "UT"),
     "registry": ["SKYWEST AIRLINES INC"],
     "asrs": ["SkyWest Airlines", "Sky West Airlines"],
     "ntsb": ["SKYWEST AIRLINES INC"],
     "erp": ["SkyWest Airlines", "SKYWEST AIRLINES INC"]},
    {"id": "OP-008", "kind": "regional", "hq": ("IND", "IN"),
     "registry": ["REPUBLIC AIRWAYS INC"],
     "asrs": ["Republic Airways", "Republic Airways Inc"],
     "ntsb": ["REPUBLIC AIRWAYS INC"],
     "erp": ["Republic Airways", "Republic Airways Holdings"]},
    {"id": "OP-009", "kind": "charter", "hq": ("BUR", "CA"),
     "registry": ["AMERIFLIGHT LLC", "AMERIFLIGHT L L C"],
     "asrs": ["Ameriflight", "Ameriflite"],
     "ntsb": ["AMERIFLIGHT LLC"],
     "erp": ["Ameriflight LLC", "AmeriFlight L.L.C."]},
    {"id": "OP-010", "kind": "cargo", "hq": ("JFK", "NY"),
     "registry": ["ATLAS AIR INC"],
     "asrs": ["Atlas Air"],
     "ntsb": ["ATLAS AIR INC"],
     "erp": ["Atlas Air Inc", "Atlas Air"]},
    {"id": "OP-011", "kind": "school", "hq": ("JAX", "FL"),
     "registry": ["ATP FLIGHT SCHOOL LLC"],
     "asrs": ["ATP Flight School"],
     "ntsb": ["ATP FLIGHT SCHOOL LLC"],
     "erp": ["ATP Flight School", "ATP Flight School LLC"]},
    {"id": "OP-012", "kind": "school", "hq": ("DAB", "FL"),
     "registry": ["EMBRY RIDDLE AERONAUTICAL UNIVERSITY INC"],
     "asrs": ["Embry-Riddle Aeronautical University", "Embry Riddle University"],
     "ntsb": ["EMBRY RIDDLE AERONAUTICAL UNIVERSITY"],
     "erp": ["Embry-Riddle Aeronautical Univ", "Embry Riddle Aeronautical University Inc"]},
    {"id": "OP-013", "kind": "trustee", "hq": ("SLC", "UT"),
     "registry": ["BANK OF UTAH TRUSTEE"],
     "asrs": ["Bank of Utah Trustee"],
     "ntsb": ["BANK OF UTAH TRUSTEE"],
     "erp": ["Bank of Utah (Trustee)", "Bank of Utah Trustee"]},
    {"id": "OP-014", "kind": "trustee", "hq": ("CRW", "WV"),
     "registry": ["WELLS FARGO TRUST CO NA TRUSTEE", "WELLS FARGO BANK NA TRUSTEE"],
     "asrs": ["Wells Fargo Trust Co Trustee"],
     "ntsb": ["WELLS FARGO TRUST CO NA"],
     "erp": ["Wells Fargo Trust Company NA", "Wells Fargo Trust Co., N.A., Trustee"]},
    {"id": "OP-015", "kind": "corporate", "hq": ("ICT", "KS"),
     "registry": ["TEXTRON AVIATION INC"],
     "asrs": ["Textron Aviation"],
     "ntsb": ["TEXTRON AVIATION INC"],
     "erp": ["Textron Aviation", "Textron Aviation Inc."]},
    {"id": "OP-016", "kind": "corporate", "hq": ("HOU", "TX"),
     "registry": ["PINNACLE ENERGY PARTNERS LLC"],
     "asrs": ["Pinnacle Energy Partners"],
     "ntsb": ["PINNACLE ENERGY PARTNERS LLC"],
     "erp": ["Pinnacle Energy Partners", "Pinnacle Energy Partners, LLC"]},
    {"id": "OP-017", "kind": "heli", "hq": ("SAV", "GA"),
     "registry": ["BLUE RIDGE HELICOPTERS LLC"],
     "asrs": ["Blue Ridge Helicopters", "Blueridge Helicopters"],
     "ntsb": ["BLUE RIDGE HELICOPTERS LLC"],
     "erp": ["Blue Ridge Helicopters", "Blue Ridge Helicopters LLC"]},
    {"id": "OP-018", "kind": "heli", "hq": ("ANC", "AK"),
     "registry": ["NORTHERN LIGHTS HELI SERVICES INC"],
     "asrs": ["Northern Lights Heli Services"],
     "ntsb": ["NORTHERN LIGHTS HELI SERVICES INC"],
     "erp": ["Northern Lights Heli Services", "Northern Lights Helicopter Services"]},
    {"id": "OP-019", "kind": "corporate", "hq": ("RSW", "FL"),
     "registry": ["GULF COAST AERIAL SURVEY INC"],
     "asrs": ["Gulf Coast Aerial Survey", "Gulf Coast Areal Survey"],
     "ntsb": ["GULF COAST AERIAL SURVEY INC"],
     "erp": ["Gulf Coast Aerial Survey", "Gulf Coast Aerial Survey, Inc."]},
    {"id": "OP-020", "kind": "corporate", "hq": ("BIL", "MT"),
     "registry": ["HIGH PLAINS CROP DUSTING INC"],
     "asrs": ["High Plains Crop Dusting"],
     "ntsb": ["HIGH PLAINS CROP DUSTING INC"],
     "erp": ["High Plains Crop Dusting", "High Plains Cropdusting Inc"]},
    {"id": "OP-021", "kind": "government", "hq": ("ANC", "AK"),
     "registry": ["STATE OF ALASKA"],
     "asrs": ["State of Alaska"],
     "ntsb": ["STATE OF ALASKA"],
     "erp": ["State of Alaska", "State of Alaska DOT"]},
    {"id": "OP-022", "kind": "government", "hq": ("BOI", "ID"),
     "registry": ["US DEPT OF INTERIOR"],
     "asrs": ["US Department of the Interior"],
     "ntsb": ["US DEPT OF INTERIOR"],
     "erp": ["US Dept of Interior", "U.S. Department of the Interior"]},
    {"id": "OP-023", "kind": "charter", "hq": ("TEB", "NJ"),
     "registry": ["MERIDIAN AIR CHARTER LLC"],
     "asrs": ["Meridian Air Charter", "Meridian Air Charters"],
     "ntsb": ["MERIDIAN AIR CHARTER LLC"],
     "erp": ["Meridian Air Charter", "Meridian Air Charter, LLC"]},
    {"id": "OP-024", "kind": "corporate", "hq": ("DEN", "CO"),
     "registry": ["REDWOOD MEDFLIGHT LLC"],
     "asrs": ["Redwood Medflight", "Redwood Med Flight"],
     "ntsb": ["REDWOOD MEDFLIGHT LLC"],
     "erp": ["Redwood MedFlight", "Redwood Medflight LLC"]},
]

LAST_NAMES = ["SMITH", "JOHNSON", "WILLIAMS", "BROWN", "JONES", "GARCIA", "MILLER", "DAVIS",
              "RODRIGUEZ", "MARTINEZ", "ANDERSON", "TAYLOR", "THOMAS", "MOORE", "JACKSON",
              "WHITE", "HARRIS", "CLARK", "LEWIS", "WALKER", "HALL", "YOUNG", "KING", "WRIGHT",
              "TORRES", "NGUYEN", "HILL", "ADAMS", "BAKER", "NELSON", "CARTER", "MITCHELL",
              "PEREZ", "ROBERTS", "TURNER", "PHILLIPS", "CAMPBELL", "PARKER", "EVANS", "EDWARDS"]
FIRST_NAMES = ["JOHN", "ROBERT", "MICHAEL", "DAVID", "JAMES", "MARY", "PATRICIA", "LINDA",
               "WILLIAM", "RICHARD", "SUSAN", "KAREN", "NANCY", "DANIEL", "PAUL", "MARK",
               "GEORGE", "STEVEN", "EDWARD", "BARBARA", "LISA", "DONALD", "KENNETH", "CAROL"]
STREET_NAMES = ["OAK ST", "MAPLE AVE", "AIRPORT RD", "HANGAR LN", "COUNTY ROAD 12", "MAIN ST",
                "AVIATION WAY", "TAXIWAY DR", "RIDGE RD", "LAKEVIEW DR", "CESSNA BLVD",
                "MERIDIAN PKWY", "INDUSTRIAL DR", "SKYWAY CT", "PROSPECT AVE"]

COMPONENTS: list[tuple[str, str]] = [
    ("LANDING GEAR", "32"), ("BRAKE ASSEMBLY", "32"), ("NOSE WHEEL STEERING", "32"),
    ("ELECTRICAL POWER", "24"), ("BATTERY", "24"), ("GENERATOR", "24"),
    ("FLIGHT CONTROLS", "27"), ("AILERON CABLE", "27"), ("ELEVATOR TRIM", "27"),
    ("FUEL SYSTEM", "28"), ("FUEL PUMP", "28"), ("FUEL QUANTITY INDICATOR", "28"),
    ("HYDRAULIC SYSTEM", "29"), ("PITOT STATIC SYSTEM", "34"), ("TRANSPONDER", "34"),
    ("NAVIGATION RADIO", "34"), ("VACUUM PUMP", "37"), ("ENGINE", "71"),
    ("ENGINE MOUNT", "71"), ("PROPELLER", "61"), ("MAGNETO", "74"), ("EXHAUST STACK", "78"),
    ("OIL COOLER", "79"), ("CABIN DOOR SEAL", "52"), ("WINDSHIELD", "56"),
    ("AIR CONDITIONING PACK", "21"), ("OXYGEN SYSTEM", "35"), ("MAIN ROTOR BLADE", "62"),
]
ACTIONS = ["INSPECTED", "REPLACED", "REPAIRED", "OVERHAULED", "ADJUSTED", "TESTED"]

ASRS_EVENTS = [
    "an altitude deviation", "a near midair collision", "a runway incursion",
    "a TCAS resolution advisory", "a loss of separation", "an unstabilized approach",
    "a gear indication anomaly", "a partial loss of engine power", "smoke in the cockpit",
    "a fuel imbalance", "a pitot static anomaly", "a flap asymmetry indication",
    "an airspace incursion", "a wake turbulence encounter",
]
ASRS_DETAILS = [
    "ATC issued a traffic alert and the crew initiated an immediate climb.",
    "The first officer noticed the discrepancy during the checklist flow.",
    "Weather at the field was marginal VFR with gusting crosswinds.",
    "The autopilot was disengaged and the aircraft was hand flown.",
    "Company maintenance was contacted and a logbook entry was made.",
    "The crew reviewed the chart and identified the published restriction.",
    "A flight attendant reported an unusual odor in the aft cabin.",
    "The controller advised of opposite direction traffic at the same altitude.",
]
ASRS_OUTCOMES = [
    "The aircraft landed without further incident.",
    "The flight continued to the destination and landed uneventfully.",
    "The crew returned to the departure airport as a precaution.",
    "The aircraft diverted and maintenance inspected the system.",
]
FLIGHT_PHASES = ["Takeoff", "Initial Climb", "Climb", "Cruise", "Descent",
                 "Initial Approach", "Final Approach", "Landing", "Taxi"]
TIME_BUCKETS = ["0001-0600", "0601-1200", "1201-1800", "1801-2400"]

NTSB_FAILURES = [
    "failure to maintain directional control", "improper flare", "inadequate preflight inspection",
    "failure to maintain airspeed", "improper fuel management", "delayed go-around decision",
    "inadequate weather evaluation", "failure to maintain clearance from terrain",
]
NTSB_RESULTS = [
    "a runway excursion", "a hard landing", "a nose-over", "an aerodynamic stall",
    "a collision with terrain", "a gear collapse", "a ground loop",
]
NTSB_CONTRIB = [
    "the gusting crosswind", "the dark night conditions", "the contaminated runway surface",
    "the pilot's lack of recent experience in type", "inadequate maintenance inspection",
    "the obscured horizon reference",
]
NON_REGISTRY_MAKES = [("AERONCA", "7AC"), ("TAYLORCRAFT", "BC12-D"), ("CHAMPION", "7ECA"),
                      ("LUSCOMBE", "8A"), ("STINSON", "108-3"), ("ERCOUPE", "415-C")]

# IATA -> state for US airports (joined against the OpenFlights seed for real
# names/cities). Curated; documents the estate's Place gold.
US_AIRPORT_STATES = {
    "ATL": "GA", "LAX": "CA", "ORD": "IL", "DFW": "TX", "DEN": "CO", "JFK": "NY",
    "SFO": "CA", "SEA": "WA", "LAS": "NV", "MCO": "FL", "MIA": "FL", "PHX": "AZ",
    "IAH": "TX", "BOS": "MA", "MSP": "MN", "DTW": "MI", "FLL": "FL", "EWR": "NJ",
    "CLT": "NC", "LGA": "NY", "SLC": "UT", "BWI": "MD", "IAD": "VA", "DCA": "VA",
    "MDW": "IL", "SAN": "CA", "TPA": "FL", "PDX": "OR", "STL": "MO", "HNL": "HI",
    "AUS": "TX", "MSY": "LA", "RDU": "NC", "MCI": "MO", "SJC": "CA", "SMF": "CA",
    "SNA": "CA", "DAL": "TX", "HOU": "TX", "BNA": "TN", "OAK": "CA", "CLE": "OH",
    "MEM": "TN", "OKC": "OK", "TUL": "OK", "ABQ": "NM", "ANC": "AK", "FAI": "AK",
    "BOI": "ID", "GEG": "WA", "TUS": "AZ", "ELP": "TX", "ICT": "KS", "OMA": "NE",
    "DSM": "IA", "FAR": "ND", "FSD": "SD", "BIL": "MT", "JAC": "WY", "LIT": "AR",
    "JAN": "MS", "BHM": "AL", "SAV": "GA", "JAX": "FL", "PBI": "FL", "RSW": "FL",
    "CHS": "SC", "CAE": "SC", "ORF": "VA", "RIC": "VA", "PIT": "PA", "PHL": "PA",
    "BUF": "NY", "ROC": "NY", "SYR": "NY", "ALB": "NY", "BDL": "CT", "PVD": "RI",
    "BTV": "VT", "MHT": "NH", "PWM": "ME", "CRW": "WV", "SDF": "KY", "CVG": "KY",
    "CMH": "OH", "IND": "IN", "MKE": "WI", "MSN": "WI", "GRR": "MI", "LNK": "NE",
    "COS": "CO", "RNO": "NV", "OGG": "HI", "DAB": "FL", "BUR": "CA", "TEB": "NJ",
}
FALLBACK_AIRPORTS = [
    ("ATL", "Hartsfield Jackson Atlanta International Airport", "Atlanta"),
    ("ORD", "Chicago O'Hare International Airport", "Chicago"),
    ("DFW", "Dallas Fort Worth International Airport", "Dallas-Fort Worth"),
    ("DEN", "Denver International Airport", "Denver"),
    ("LAX", "Los Angeles International Airport", "Los Angeles"),
    ("JFK", "John F Kennedy International Airport", "New York"),
    ("SEA", "Seattle Tacoma International Airport", "Seattle"),
    ("MEM", "Memphis International Airport", "Memphis"),
    ("SLC", "Salt Lake City International Airport", "Salt Lake City"),
    ("ANC", "Ted Stevens Anchorage International Airport", "Anchorage"),
    ("ICT", "Wichita Eisenhower National Airport", "Wichita"),
    ("BOI", "Boise Air Terminal/Gowen Field", "Boise"),
    ("JAX", "Jacksonville International Airport", "Jacksonville"),
    ("BIL", "Billings Logan International Airport", "Billings"),
    ("SAV", "Savannah Hilton Head International Airport", "Savannah"),
    ("CRW", "Yeager Airport", "Charleston"),
    ("SDF", "Louisville International Standiford Field", "Louisville"),
    ("IND", "Indianapolis International Airport", "Indianapolis"),
    ("DAL", "Dallas Love Field", "Dallas"),
    ("HOU", "William P Hobby Airport", "Houston"),
    ("RSW", "Southwest Florida International Airport", "Fort Myers"),
    ("DAB", "Daytona Beach International Airport", "Daytona Beach"),
    ("BUR", "Bob Hope Airport", "Burbank"),
    ("TEB", "Teterboro Airport", "Teterboro"),
]

# FAA region code by state (approximation of FAA regional offices; documented
# in the module README).
STATE_REGION = {}
for _st in ["ME", "NH", "VT", "MA", "RI", "CT", "NY", "NJ", "PA", "MD", "DE", "VA", "WV", "DC"]:
    STATE_REGION[_st] = "1"
for _st in ["TX", "NM", "OK", "AR", "LA"]:
    STATE_REGION[_st] = "2"
for _st in ["IL", "IN", "MI", "MN", "ND", "OH", "SD", "WI"]:
    STATE_REGION[_st] = "3"
for _st in ["IA", "KS", "MO", "NE"]:
    STATE_REGION[_st] = "C"
for _st in ["AL", "FL", "GA", "KY", "MS", "NC", "SC", "TN"]:
    STATE_REGION[_st] = "S"
for _st in ["CO", "ID", "MT", "OR", "UT", "WA", "WY"]:
    STATE_REGION[_st] = "5"
for _st in ["CA", "NV", "AZ", "HI"]:
    STATE_REGION[_st] = "7"
STATE_REGION["AK"] = "A"

# FAA MASTER fixed-width padding (trailing-space wart, faithful to the real
# ReleasableAircraft layout where every field is space-padded to its width).
MASTER_WIDTHS = {
    "N-NUMBER": 5, "SERIAL NUMBER": 30, "MFR MDL CODE": 7, "ENG MFR MDL": 5,
    "YEAR MFR": 4, "REGISTRANT NAME": 50, "STREET": 33, "CITY": 18, "STATE": 2,
    "ZIP": 10, "REGION": 1, "COUNTY": 3, "COUNTRY": 2, "LAST ACTION DATE": 8,
    "CERT ISSUE DATE": 8, "CERTIFICATION": 10, "TYPE AIRCRAFT": 1, "TYPE ENGINE": 2,
    "STATUS CODE": 2, "MODE S CODE": 8, "FRACT OWNER": 1, "AIR WORTH DATE": 8,
    "EXPIRATION DATE": 8,
}
MASTER_COLUMNS = list(MASTER_WIDTHS)
ACFTREF_COLUMNS = ["CODE", "MFR", "MODEL", "TYPE-ACFT", "TYPE-ENG", "AC-CAT",
                   "NO-ENG", "NO-SEATS", "AC-WEIGHT", "SPEED"]
ASRS_COLUMNS = ["ACN", "DATE", "LOCAL TIME OF DAY", "PLACE.LOCALE REFERENCE",
                "STATE REFERENCE", "AIRCRAFT 1 MAKE MODEL", "AIRCRAFT 1 OPERATOR",
                "FLIGHT PHASE", "ALTITUDE.AGL.SINGLE VALUE", "NARRATIVE", "SYNOPSIS"]
NTSB_COLUMNS = ["EV_ID", "NTSB_NO", "EV_DATE", "EV_CITY", "EV_STATE", "EV_TYPE",
                "ACFT_REGIST_NMBR", "ACFT_MAKE", "ACFT_MODEL", "OPERATOR",
                "INJ_TOT_F", "DAMAGE", "NARR_CAUSE"]
ERP_COLUMNS = ["WORK_ORDER_ID", "TAIL_NUMBER", "COMPONENT", "ATA_CHAPTER", "ACTION",
               "MECHANIC_ID", "LABOR_HOURS", "COST", "OPEN_DATE", "CLOSE_DATE",
               "OPERATOR_NAME"]
GOLD_PAIR_COLUMNS = ["ENTITY_TYPE", "ENTITY_ID", "LEFT_TABLE", "LEFT_KEY",
                     "RIGHT_TABLE", "RIGHT_KEY", "NOTE"]

N_AIRCRAFT_SINGLE = 2484   # uniquely-tailed airframes
N_REUSE_PAIRS = 8          # tails used by two airframes in disjoint windows
N_ASRS = 350
N_NTSB = 200
N_ERP = 600

BIRD_ROWS = {0, 1, 5, 10, 15}      # ASRS bird-strike anchors (all registry-linked)
ASRS_REUSE_ROWS = {20, 21}         # ASRS rows pinned to old-window reuse airframes


def pad(value: str, width: int) -> str:
    s = str(value)
    return s + " " * (width - len(s)) if len(s) < width else s


def ymd(d: date) -> str:
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"


def iso(d: date) -> str:
    return d.isoformat()


def rand_date(rng: random.Random, y0: int, y1: int) -> date:
    start = date(y0, 1, 1).toordinal()
    end = date(y1, 12, 31).toordinal()
    return date.fromordinal(rng.randint(start, end))


def rand_date_between(rng: random.Random, lo: date, hi: date) -> date:
    if hi < lo:
        raise ValueError(f"empty date window {lo}..{hi}")
    return date.fromordinal(rng.randint(lo.toordinal(), hi.toordinal()))


# --------------------------------------------------------------------------
# seed loading (pinned real downloads; embedded fallback per AMD-0006)
# --------------------------------------------------------------------------

def load_airports(seed_dir: Path) -> list[dict]:
    """[{iata, name, city, state}] limited to curated US_AIRPORT_STATES."""
    path = seed_dir / "airports_us.csv"
    rows: list[dict] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            for rec in csv.DictReader(f):
                st = US_AIRPORT_STATES.get(rec["IATA"])
                if st:
                    rows.append({"iata": rec["IATA"], "name": rec["NAME"],
                                 "city": rec["CITY"], "state": st})
    else:
        for iata, name, city in FALLBACK_AIRPORTS:
            st = US_AIRPORT_STATES.get(iata)
            if st:
                rows.append({"iata": iata, "name": name, "city": city, "state": st})
    rows.sort(key=lambda r: r["iata"])
    return rows


def load_seed_models(seed_dir: Path) -> list[tuple[str, str]]:
    """(mfr_key, MODEL) pairs derived from the pinned OpenFlights planes seed."""
    path = seed_dir / "planes.csv"
    if not path.exists():
        return []
    per_mfr: dict[str, list[str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for rec in csv.DictReader(f):
            name = rec["NAME"]
            for prefix, key in SEED_PLANE_PREFIXES:
                if name.startswith(prefix):
                    model = " ".join(name[len(prefix):].upper().split())
                    if model and len(model) <= 20:
                        per_mfr.setdefault(key, []).append(model)
                    break
    out: list[tuple[str, str]] = []
    for key in sorted(per_mfr):
        models = sorted(set(per_mfr[key]))[: SEED_PLANE_CAPS.get(key, 4)]
        out.extend((key, m) for m in models)
    return out


def classify_kind(mfr: str, model: str) -> str:
    m = model.upper()
    if mfr in ("ROBINSON",) or (mfr == "BELL" and not m.startswith("BOEING")):
        return "heli"
    if mfr == "BOEING" and (m.startswith("7") or m.startswith("MD")):
        return "jet"
    if mfr == "AIRBUS":
        return "jet"
    if mfr == "EMBRAER" and ("PHENOM" in m or "LEGACY" in m or "EMB-505" in m):
        return "bizjet"
    if mfr == "EMBRAER" and (m.startswith("EMB 1") or m.startswith("EMB-1") or "120" in m):
        return "turboprop"
    if mfr == "EMBRAER":
        return "jet"
    if mfr == "CESSNA" and "CITATION" in m:
        return "bizjet"
    if mfr == "GULFSTREAM":
        return "bizjet"
    if mfr == "BOMBARDIER":
        if "CRJ" in m or "CL-600-2C" in m or "CL-600-2D" in m:
            return "jet"
        return "bizjet"
    if mfr == "DEHAVILLAND":
        return "turboprop" if any(t in m for t in ("DHC-6", "DHC-7", "DHC-8", "DASH")) else "ga"
    if mfr == "CESSNA" and ("208" in m or "CARAVAN" in m):
        return "turboprop"
    if mfr == "CESSNA" and any(t in m for t in ("310", "337", "414", "421", "401", "402")):
        return "ga_twin"
    if mfr == "BEECH" and ("KING" in m or "1900" in m or "B200" in m):
        return "turboprop"
    if mfr == "BEECH" and ("58" in m or "BARON" in m or "18" == m):
        return "ga_twin"
    if mfr == "PIPER" and any(t in m for t in ("PA-23", "PA-31", "PA-34", "PA-44")):
        return "ga_twin"
    if mfr == "PIPER" and "PA-46-500" in m:
        return "turboprop"
    if mfr == "ROCKWELL" and "SABRE" in m:
        return "bizjet"
    if mfr == "ROCKWELL" and "690" in m:
        return "turboprop"
    if mfr == "ROCKWELL" and "685" in m:
        return "ga_twin"
    return "ga"


def build_models(rng: random.Random, seed_dir: Path) -> list[dict]:
    """ACFTREF model rows + per-model metadata used by fleet generation."""
    raw: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for mfr, model, kind in EMBEDDED_MODELS:
        if (mfr, model) not in seen:
            seen.add((mfr, model))
            raw.append((mfr, model, kind))
    for mfr, model in load_seed_models(seed_dir):
        if (mfr, model) not in seen:
            seen.add((mfr, model))
            raw.append((mfr, model, classify_kind(mfr, model)))
    raw.sort(key=lambda t: (t[0], t[1]))

    mfr_codes = {key: 120 + 11 * i for i, key in enumerate(sorted(MANUFACTURERS))}
    counters: dict[str, int] = {}
    models: list[dict] = []
    variant_toggle: dict[str, int] = {}
    for mfr, model, kind in raw:
        idx = counters.get(mfr, 0)
        counters[mfr] = idx + 1
        code = f"{mfr_codes[mfr]:03d}{idx:02d}{rng.randint(0, 9):01d}{rng.randint(0, 9):01d}"
        spellings = MANUFACTURERS[mfr]["acftref"]
        if len(spellings) > 1:
            # guarantee both spellings appear: alternate for the first two
            # models, then random (weighted toward the primary).
            t = variant_toggle.get(mfr, 0)
            variant_toggle[mfr] = t + 1
            if t == 0:
                name = spellings[0]
            elif t == 1:
                name = spellings[1]
            else:
                name = spellings[0] if rng.random() < 0.65 else spellings[1]
        else:
            name = spellings[0]
        if kind == "jet":
            seats, speed, eng_n, t_acft, weight = rng.randint(90, 230), 0, 2, "5", "CLASS 3"
        elif kind == "bizjet":
            seats, speed, eng_n, t_acft, weight = rng.randint(6, 19), 0, 2, "5", "CLASS 2"
        elif kind == "turboprop":
            seats, speed, eng_n = rng.randint(6, 78), rng.randint(160, 320), rng.choice([1, 2])
            t_acft, weight = ("5" if eng_n == 2 else "4"), "CLASS 2"
        elif kind == "ga_twin":
            seats, speed, eng_n, t_acft, weight = rng.randint(4, 8), rng.randint(160, 240), 2, "5", "CLASS 1"
        elif kind == "heli":
            seats, speed, eng_n, t_acft, weight = rng.randint(2, 7), rng.randint(90, 150), rng.choice([1, 1, 2]), "6", "CLASS 1"
        else:  # ga
            seats, speed, eng_n, t_acft, weight = rng.randint(2, 6), rng.randint(105, 175), 1, "4", "CLASS 1"
        models.append({
            "code": code, "mfr_key": mfr, "mfr_name": name, "model": model,
            "kind": kind, "type_acft": t_acft, "no_eng": eng_n, "no_seats": seats,
            "ac_weight": weight, "speed": speed,
            "asrs_name": f"{MANUFACTURERS[mfr]['title']} {model.title() if model.islower() else model}",
        })
    return models


def acftref_rows(models: list[dict]) -> list[dict]:
    rows = []
    for m in sorted(models, key=lambda x: x["code"]):
        type_eng = {"ga": "1", "ga_twin": "1", "turboprop": "2", "jet": "5",
                    "bizjet": "5", "heli": "1" if m["no_eng"] == 1 else "3"}[m["kind"]]
        rows.append({
            "CODE": m["code"], "MFR": m["mfr_name"], "MODEL": m["model"],
            "TYPE-ACFT": m["type_acft"], "TYPE-ENG": type_eng, "AC-CAT": "1",
            "NO-ENG": str(m["no_eng"]), "NO-SEATS": str(m["no_seats"]),
            "AC-WEIGHT": m["ac_weight"], "SPEED": str(m["speed"]),
        })
    return rows


# --------------------------------------------------------------------------
# fleet (FAA MASTER)
# --------------------------------------------------------------------------

TAIL_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I, O per FAA rules


def gen_tail(rng: random.Random, used: set[str]) -> str:
    while True:
        r = rng.random()
        if r < 0.40:
            t = f"N{rng.randint(100, 999)}{rng.choice(TAIL_LETTERS)}{rng.choice(TAIL_LETTERS)}"
        elif r < 0.65:
            t = f"N{rng.randint(1000, 9999)}{rng.choice(TAIL_LETTERS)}"
        elif r < 0.85:
            t = f"N{rng.randint(10000, 99999)}"
        else:
            t = f"N{rng.randint(10, 99)}{rng.choice(TAIL_LETTERS)}{rng.choice(TAIL_LETTERS)}"
        if t not in used:
            used.add(t)
            return t


def gen_serial(rng: random.Random, mfr: str, used: set[str]) -> str:
    while True:
        if mfr == "CESSNA":
            s = f"172{rng.randint(10000, 79999)}"
        elif mfr == "PIPER":
            s = f"28-{rng.randint(1965, 2024)}{rng.randint(100, 999)}"
        elif mfr == "BOEING":
            s = str(rng.randint(20000, 67999))
        elif mfr == "AIRBUS":
            s = str(rng.randint(800, 11999))
        elif mfr == "ROBINSON":
            s = str(rng.randint(100, 14999))
        elif mfr == "CIRRUS":
            s = str(rng.randint(1000, 8999))
        else:
            s = f"{rng.choice('ABCDEFGHJKLM')}{rng.randint(100, 9999)}-{rng.randint(10, 99)}"
        if s not in used:
            used.add(s)
            return s


def build_individuals(rng: random.Random, airports: list[dict]) -> list[dict]:
    out = []
    seen = set()
    while len(out) < 70:
        name = f"{rng.choice(LAST_NAMES)} {rng.choice(FIRST_NAMES)} {rng.choice('ABCDEFGHJKLMRSTW')}"
        if name in seen:
            continue
        seen.add(name)
        ap = rng.choice(airports)
        out.append({"name": name, "city": ap["city"].upper(), "state": ap["state"],
                    "street": f"{rng.randint(10, 9999)} {rng.choice(STREET_NAMES)}",
                    "zip": f"{rng.randint(10000, 99799)}"})
    return out


def build_operator_addresses(rng: random.Random, airports: list[dict]) -> None:
    by_iata = {a["iata"]: a for a in airports}
    for op in OPERATORS:
        iata, st = op["hq"]
        ap = by_iata.get(iata)
        city = (ap["city"].upper() if ap else iata)
        op["addr"] = {"city": city, "state": st,
                      "street": f"{rng.randint(100, 9999)} {rng.choice(STREET_NAMES)}",
                      "zip": f"{rng.randint(10000, 99799)}"}


def pick_operator(rng: random.Random, kind: str) -> dict | None:
    pools = {
        "jet": ["airline", "airline", "airline", "cargo", "regional"],
        "bizjet": ["trustee", "corporate", "charter"],
        "turboprop": ["cargo", "regional", "charter", "school", "corporate", "government"],
        "heli": ["heli", "heli", "government", "corporate"],
    }
    if kind in pools:
        want = rng.choice(pools[kind])
        cands = [op for op in OPERATORS if op["kind"] == want]
        return rng.choice(cands) if cands else None
    # ga / ga_twin: mostly individuals
    r = rng.random()
    if r < 0.62:
        return None  # individual
    want = rng.choice(["school", "corporate", "trustee", "charter"])
    cands = [op for op in OPERATORS if op["kind"] == want]
    return rng.choice(cands) if cands else None


def build_fleet(rng: random.Random, models: list[dict], airports: list[dict]) -> dict:
    """Aircraft entities + FAA MASTER rows (incl. the N-number reuse trap)."""
    individuals = build_individuals(rng, airports)
    build_operator_addresses(rng, airports)
    by_mfr: dict[str, list[dict]] = {}
    for m in models:
        by_mfr.setdefault(m["mfr_key"], []).append(m)
    mfr_keys = sorted(MFR_WEIGHTS)
    weights = [MFR_WEIGHTS[k] for k in mfr_keys]

    used_tails: set[str] = set()
    used_serials: set[str] = set()
    op_fleet_count: dict[str, int] = {}
    aircraft: list[dict] = []
    mode_s_base = 0xA00001

    def make_aircraft(uid: int, tail: str, model: dict, old_window: bool,
                      new_reuse: bool = False) -> dict:
        serial = gen_serial(rng, model["mfr_key"], used_serials)
        kind = model["kind"]
        if old_window:
            cert = rand_date(rng, 1972, 1984)
            exp = rand_date(rng, 1992, 1998)
            status = "D"
            year = str(max(1955, cert.year - rng.randint(0, 6)))
        else:
            # a reused tail's NEW registration must start strictly after every
            # old window ends (old expirations are <= 1998)
            cert = rand_date(rng, 2003, 2015) if new_reuse else rand_date(rng, 1965, 2022)
            exp = rand_date(rng, 2026, 2031)
            status = "V"
            year = str(max(1955, cert.year - rng.randint(0, 3)))
        op = pick_operator(rng, kind)
        if op is not None:
            n_prior = op_fleet_count.get(op["id"], 0)
            op_fleet_count[op["id"]] = n_prior + 1
            if n_prior == 0 or rng.random() < 0.85:
                reg_name = op["registry"][0]
                variant_spelling = False
            else:
                reg_name = rng.choice(op["registry"])
                variant_spelling = reg_name != op["registry"][0]
            addr = op["addr"]
            registrant = {"op": op, "name": reg_name, "variant": variant_spelling, **addr}
        else:
            ind = rng.choice(individuals)
            registrant = {"op": None, "name": ind["name"], "variant": False,
                          "city": ind["city"], "state": ind["state"],
                          "street": ind["street"], "zip": ind["zip"]}
        eng = rng.choice(ENGINES[kind])
        blank_year = rng.random() < 0.04
        blank_aw = rng.random() < 0.08
        blank_street = rng.random() < 0.03
        air_worth = "" if blank_aw else ymd(rand_date_between(rng, cert, min(exp, date(2025, 5, 31))))
        cert_code = {"jet": "1T", "bizjet": rng.choice(["1T", "1N"]),
                     "turboprop": rng.choice(["1N", "1T"]),
                     "heli": "1N"}.get(kind, rng.choice(["1N", "1N", "1N", "1U", "1A"]))
        fract = "Y" if (op is not None and op["kind"] == "trustee" and rng.random() < 0.3) else ""
        return {
            "uid": f"AC-{uid:04d}", "tail": tail, "serial": serial, "model": model,
            "eng": eng, "year": "" if blank_year else year, "registrant": registrant,
            "cert_issue": cert, "expiration": exp, "status": status,
            "last_action": rand_date_between(rng, cert, min(exp, date(2025, 5, 31))),
            "air_worth": air_worth, "street": "" if blank_street else registrant["street"],
            "cert_code": cert_code, "fract": fract,
            "mode_s": f"{mode_s_base + 17 * uid:o}",
        }

    uid = 0
    for _ in range(N_AIRCRAFT_SINGLE):
        mfr = rng.choices(mfr_keys, weights=weights, k=1)[0]
        model = rng.choice(by_mfr[mfr])
        tail = gen_tail(rng, used_tails)
        aircraft.append(make_aircraft(uid, tail, model, old_window=False))
        uid += 1

    reuse_tails: list[str] = []
    for _ in range(N_REUSE_PAIRS):
        tail = gen_tail(rng, used_tails)
        reuse_tails.append(tail)
        old_model = rng.choice(by_mfr[rng.choice(["CESSNA", "PIPER", "BEECH", "MOONEY"])])
        new_model = rng.choice(by_mfr[rng.choices(mfr_keys, weights=weights, k=1)[0]])
        aircraft.append(make_aircraft(uid, tail, old_model, old_window=True))
        uid += 1
        aircraft.append(make_aircraft(uid, tail, new_model, old_window=False, new_reuse=True))
        uid += 1

    rows = []
    for ac in aircraft:
        reg = ac["registrant"]
        row = {
            "N-NUMBER": ac["tail"][1:],  # real MASTER stores the number sans 'N'
            "SERIAL NUMBER": ac["serial"],
            "MFR MDL CODE": ac["model"]["code"],
            "ENG MFR MDL": ac["eng"][0],
            "YEAR MFR": ac["year"],
            "REGISTRANT NAME": reg["name"],
            "STREET": ac["street"],
            "CITY": reg["city"],
            "STATE": reg["state"],
            "ZIP": reg["zip"],
            "REGION": STATE_REGION.get(reg["state"], "1"),
            "COUNTY": f"{rng.randint(1, 199):03d}",
            "COUNTRY": "US",
            "LAST ACTION DATE": ymd(ac["last_action"]),
            "CERT ISSUE DATE": ymd(ac["cert_issue"]),
            "CERTIFICATION": ac["cert_code"],
            "TYPE AIRCRAFT": ac["model"]["type_acft"],
            "TYPE ENGINE": ac["eng"][3],
            "STATUS CODE": ac["status"],
            "MODE S CODE": ac["mode_s"],
            "FRACT OWNER": ac["fract"],
            "AIR WORTH DATE": ac["air_worth"],
            "EXPIRATION DATE": ymd(ac["expiration"]),
        }
        padded = {k: pad(v, MASTER_WIDTHS[k]) for k, v in row.items()}
        ac["master_row"] = padded
        ac["row_key"] = f"{row['N-NUMBER']}|{row['SERIAL NUMBER']}"
        rows.append((row["N-NUMBER"], row["CERT ISSUE DATE"], padded))
    rows.sort(key=lambda t: (t[0], t[1]))
    master = [r for _, _, r in rows]
    return {"aircraft": aircraft, "master_rows": master, "reuse_tails": reuse_tails,
            "individuals": individuals}


# --------------------------------------------------------------------------
# gold-pair accumulation
# --------------------------------------------------------------------------

class GoldPairs:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._op_rep: dict[str, str] = {}  # op id -> representative master row_key

    def set_operator_reps(self, aircraft: list[dict]) -> None:
        for ac in aircraft:
            op = ac["registrant"]["op"]
            if op is not None and not ac["registrant"]["variant"]:
                self._op_rep.setdefault(op["id"], ac["row_key"])
        # registry spelling-variant pairs (master <-> master)
        for ac in aircraft:
            op = ac["registrant"]["op"]
            if op is not None and ac["registrant"]["variant"] and op["id"] in self._op_rep:
                self.add("operator", f"operator:{op['id']}", "faa_master",
                         self._op_rep[op["id"]], "faa_master", ac["row_key"],
                         "registry_spelling_variant")

    def add(self, etype: str, eid: str, lt: str, lk: str, rt: str, rk: str, note: str = "") -> None:
        self.rows.append({"ENTITY_TYPE": etype, "ENTITY_ID": eid, "LEFT_TABLE": lt,
                          "LEFT_KEY": lk, "RIGHT_TABLE": rt, "RIGHT_KEY": rk, "NOTE": note})

    def aircraft_pair(self, ac: dict, table: str, key: str, note: str = "") -> None:
        self.add("aircraft", f"aircraft:{ac['uid']}", "faa_master", ac["row_key"],
                 table, key, note)

    def operator_pair(self, op: dict, table: str, key: str, note: str = "") -> None:
        rep = self._op_rep.get(op["id"])
        if rep is not None:
            self.add("operator", f"operator:{op['id']}", "faa_master", rep, table, key, note)

    def sorted_rows(self) -> list[dict]:
        return sorted(self.rows, key=lambda r: (r["ENTITY_TYPE"], r["ENTITY_ID"],
                                                r["LEFT_TABLE"], r["LEFT_KEY"],
                                                r["RIGHT_TABLE"], r["RIGHT_KEY"]))


# --------------------------------------------------------------------------
# ASRS
# --------------------------------------------------------------------------

def build_asrs(rng: random.Random, fleet: dict, airports: list[dict], gold: GoldPairs) -> dict:
    aircraft = fleet["aircraft"]
    active = [a for a in aircraft if a["status"] == "V"]
    old_reuse = [a for a in aircraft if a["status"] == "D"]
    rows: list[dict] = []
    anchors: dict = {"bird": [], "reuse": [], "meters": [], "cq8": None}
    acn = 1500000
    asrs_floor = date(1990, 1, 1)
    asrs_ceil = date(2025, 5, 31)

    for i in range(N_ASRS):
        acn += rng.randint(11, 97)
        linked = (i % 5) < 3
        is_meters = (i % 8) == 3
        is_blank_alt = (i % 12) == 7 and not is_meters
        force_descent = is_meters and (i % 16) == 3

        if i in ASRS_REUSE_ROWS:
            ac = old_reuse[(i - 20) * 2]  # two distinct old airframes
            linked = True
        elif linked:
            ac = rng.choice(active)
        else:
            ac = None

        if ac is not None:
            lo = max(ac["cert_issue"], asrs_floor)
            hi = min(ac["expiration"], asrs_ceil)
            d = rand_date_between(rng, lo, hi)
        else:
            d = rand_date(rng, 1990, 2024)

        airport = rng.choice(airports)
        phase = "Descent" if force_descent else rng.choice(FLIGHT_PHASES)

        if is_meters:
            if i == 3:
                alt_m = 3500   # 11483 ft: above 10,000 ft — punishes suffix-ignorers
            elif i == 19:
                alt_m = 1200   # 3937 ft: below the threshold
            else:
                alt_m = rng.randint(300, 3600)
            alt_str = f"{alt_m}m"
        elif is_blank_alt:
            alt_str = ""
        else:
            if phase in ("Cruise", "Descent"):
                alt_ft = rng.randint(3000, 17500)
            elif phase in ("Takeoff", "Landing", "Taxi"):
                alt_ft = rng.randint(0, 800)
            else:
                alt_ft = rng.randint(500, 9000)
            alt_str = str(alt_ft)

        if i in BIRD_ROWS:
            event = "a bird strike"
        else:
            event = rng.choice(ASRS_EVENTS)

        if ac is not None:
            make_model = ac["model"]["asrs_name"]
            op = ac["registrant"]["op"]
            if op is not None:
                op_name = rng.choice(op["asrs"])
            else:
                op_name = "Private Individual"
            tail = ac["tail"]
            alt_phrase = f"at {alt_str} ft AGL" if alt_str and not alt_str.endswith("m") else (
                f"at {alt_str} AGL" if alt_str else "at low altitude")
            s1 = (f"While in {phase.lower()} {rng.choice(['into', 'out of'])} {airport['iata']}, "
                  f"the crew of a {make_model}, tail number {tail}, experienced {event}.")
            s2 = f"The aircraft was operated by {op_name} and was {alt_phrase} when the event began."
            sentences = [s1, s2]
            if i in BIRD_ROWS:
                sentences.append("Several birds impacted the radome and the bird strike "
                                 "left visible damage to the leading edge.")
            if rng.random() < 0.6:
                sentences.append(rng.choice(ASRS_DETAILS))
            if rng.random() < 0.5:
                sentences.append(rng.choice(ASRS_OUTCOMES))
            narrative = " ".join(sentences[:5])
            synopsis = f"{make_model} reported {event} during {phase.lower()} near {airport['iata']}."
        else:
            make_model = rng.choice(["Light Transport", "Small Aircraft", "Medium Large Transport"])
            op_name = rng.choice(["Air Carrier", "Corporate", "Personal", "Fractional"])
            s1 = (f"During {phase.lower()} near {airport['iata']}, we experienced {event}.")
            s2 = rng.choice(ASRS_DETAILS)
            sentences = [s1, s2]
            if rng.random() < 0.5:
                sentences.append(rng.choice(ASRS_OUTCOMES))
            narrative = " ".join(sentences)
            synopsis = f"{make_model} flight crew reported {event} near {airport['iata']}."

        row = {
            "ACN": str(acn),
            "DATE": f"{d.year:04d}{d.month:02d}",
            "LOCAL TIME OF DAY": rng.choice(TIME_BUCKETS),
            "PLACE.LOCALE REFERENCE": f"{airport['iata']}.Airport",
            "STATE REFERENCE": airport["state"],
            "AIRCRAFT 1 MAKE MODEL": make_model,
            "AIRCRAFT 1 OPERATOR": op_name,
            "FLIGHT PHASE": phase,
            "ALTITUDE.AGL.SINGLE VALUE": alt_str,
            "NARRATIVE": narrative,
            "SYNOPSIS": synopsis,
        }
        rows.append(row)

        if ac is not None:
            note = "temporal_reuse_trap:old" if i in ASRS_REUSE_ROWS else ""
            gold.aircraft_pair(ac, "asrs_reports", row["ACN"], note)
            op = ac["registrant"]["op"]
            if op is not None:
                variant = op_name != op["asrs"][0]
                gold.operator_pair(op, "asrs_reports", row["ACN"],
                                   "operator_name_variant" if variant else "")
        if i in BIRD_ROWS:
            anchors["bird"].append((row, ac))
        if i in ASRS_REUSE_ROWS:
            anchors["reuse"].append((row, ac))
        if is_meters:
            anchors["meters"].append((row, ac))
        if i == 25 and ac is not None:
            anchors["cq8"] = (row, ac)
    # guarantee a CQ-8 anchor even if row 25 was unlinked (i%5<3 makes it linked)
    return {"rows": rows, "anchors": anchors}


# --------------------------------------------------------------------------
# NTSB
# --------------------------------------------------------------------------

def build_ntsb(rng: random.Random, fleet: dict, airports: list[dict], gold: GoldPairs) -> dict:
    aircraft = fleet["aircraft"]
    active = [a for a in aircraft if a["status"] == "V"]
    old_reuse = [a for a in aircraft if a["status"] == "D"]
    reuse_new = {a["tail"]: a for a in active if a["tail"] in set(fleet["reuse_tails"])}
    rows: list[dict] = []
    anchors: dict = {"fuel": None, "reuse_old": [], "reuse_new": [], "cq9": None, "cq14": None}
    used_ev: set[str] = set()
    # unlinked NTSB tails must never collide with registry tails
    used_tails_unlinked: set[str] = {a["tail"] for a in aircraft}
    ntsb_no_serial = 100
    ntsb_floor, ntsb_ceil = date(1982, 1, 1), date(2025, 5, 31)

    for i in range(N_NTSB):
        linked = (i % 4) != 3
        ac = None
        note = ""
        if i in (10, 11):
            ac = old_reuse[(i - 10) * 2 + 1]  # distinct from ASRS reuse anchors
            note = "temporal_reuse_trap:old"
        elif i == 12:
            # NEW airframe on a reused tail: same tail as row 10's aircraft
            ac = reuse_new[old_reuse[1]["tail"]]
            note = "temporal_reuse_trap:new"
        elif linked:
            ac = rng.choice(active)

        if ac is not None:
            lo = max(ac["cert_issue"], ntsb_floor)
            hi = min(ac["expiration"], ntsb_ceil)
            d = rand_date_between(rng, lo, hi)
            make_pool = MANUFACTURERS[ac["model"]["mfr_key"]]["ntsb"]
            make = rng.choice(make_pool)
            model = ac["model"]["model"]
            tail = ac["tail"]
            op = ac["registrant"]["op"]
            operator = rng.choice(op["ntsb"]) if op is not None else ac["registrant"]["name"]
        else:
            d = rand_date(rng, 1982, 2024)
            make, model = rng.choice(NON_REGISTRY_MAKES)
            tail = gen_tail(rng, used_tails_unlinked)
            operator = ""

        drop_n = (i % 7) == 2  # ~14% dropped 'N' prefix wart
        regist = tail[1:] if drop_n else tail

        airport = rng.choice(airports)
        ev_id = f"{ymd(d)}X{rng.randint(10000, 99999)}"
        while ev_id in used_ev:
            ev_id = f"{ymd(d)}X{rng.randint(10000, 99999)}"
        used_ev.add(ev_id)
        ntsb_no_serial += rng.randint(1, 5)
        region = rng.choice(["WPR", "ERA", "CEN", "ANC", "DCA", "GAA"])
        severity = rng.choice(["LA", "LA", "FA", "CA"])
        ntsb_no = f"{region}{d.year % 100:02d}{severity}{ntsb_no_serial % 1000:03d}"

        ev_type = "ACC" if rng.random() < 0.8 else "INC"
        damage = rng.choice(["DEST", "SUBS", "SUBS", "MINR", "NONE"])
        if (i % 7) == 6:
            inj = ""
        else:
            inj = str(rng.choices([0, 0, 0, 0, 1, 2, 3, 4], k=1)[0])

        if i == 30 and ac is not None:
            cause = ("The total loss of engine power due to fuel exhaustion, which resulted "
                     "in a forced landing. Contributing was the pilot's inadequate fuel planning.")
            anchors["fuel"] = None  # set after row constructed
            drop_n = True
            regist = tail[1:]
        else:
            cause = (f"The pilot's {rng.choice(NTSB_FAILURES)} during {rng.choice(['takeoff', 'landing', 'approach', 'cruise flight'])}, "
                     f"which resulted in {rng.choice(NTSB_RESULTS)}. Contributing to the accident was "
                     f"{rng.choice(NTSB_CONTRIB)}.")

        row = {
            "EV_ID": ev_id, "NTSB_NO": ntsb_no, "EV_DATE": iso(d),
            "EV_CITY": airport["city"], "EV_STATE": airport["state"], "EV_TYPE": ev_type,
            "ACFT_REGIST_NMBR": regist, "ACFT_MAKE": make, "ACFT_MODEL": model,
            "OPERATOR": operator, "INJ_TOT_F": inj, "DAMAGE": damage, "NARR_CAUSE": cause,
        }
        rows.append(row)

        if ac is not None:
            gold.aircraft_pair(ac, "ntsb_events", ev_id, note)
            op = ac["registrant"]["op"]
            if op is not None:
                gold.operator_pair(op, "ntsb_events", ev_id)
            if i == 30:
                anchors["fuel"] = (row, ac)
            if i in (10, 11):
                anchors["reuse_old"].append((row, ac))
            if i == 12:
                anchors["reuse_new"].append((row, ac))
            if i == 40 and ac["registrant"]["op"] is not None:
                anchors["cq14"] = (row, ac)
    # deterministic fallbacks for anchors that depend on rng outcomes
    if anchors["cq14"] is None:
        for row, ac in _linked_rows(rows, gold, fleet):
            if ac["registrant"]["op"] is not None:
                anchors["cq14"] = (row, ac)
                break
    rows.sort(key=lambda r: (r["EV_DATE"], r["EV_ID"]))
    return {"rows": rows, "anchors": anchors}


def _linked_rows(rows: list[dict], gold: GoldPairs, fleet: dict):
    by_key = {}
    for p in gold.rows:
        if p["ENTITY_TYPE"] == "aircraft" and p["RIGHT_TABLE"] == "ntsb_events":
            by_key[p["RIGHT_KEY"]] = p["LEFT_KEY"]
    ac_by_key = {a["row_key"]: a for a in fleet["aircraft"]}
    for row in rows:
        k = by_key.get(row["EV_ID"])
        if k:
            yield row, ac_by_key[k]


# --------------------------------------------------------------------------
# maintenance ERP
# --------------------------------------------------------------------------

def fmt_cost(rng: random.Random, value: float, styled: bool) -> str:
    if styled:
        return f"USD {value:,.2f}"
    return f"{value:.2f}"


def build_erp(rng: random.Random, fleet: dict, ntsb: dict, gold: GoldPairs) -> dict:
    aircraft = fleet["aircraft"]
    # ERP belongs to operators: org-owned active aircraft + a slice of individuals
    pool = [a for a in aircraft if a["status"] == "V" and a["registrant"]["op"] is not None]
    ind_pool = [a for a in aircraft if a["status"] == "V" and a["registrant"]["op"] is None]
    rows: list[dict] = []
    anchors: dict = {"cq9": None, "cq9_orders": [], "lg_op": None}
    mechanics = [f"M-{i:03d}" for i in range(1, 41)]
    erp_floor, erp_ceil = date(2015, 1, 1), date(2025, 5, 31)
    wo = 100000

    # ---- CQ-9 anchor: aircraft with an NTSB event + work orders around it ----
    cq9_row, cq9_ac = None, None
    for row, ac in _linked_rows(ntsb["rows"], gold, fleet):
        d = date.fromisoformat(row["EV_DATE"])
        if (ac["registrant"]["op"] is not None and date(2017, 1, 1) <= d <= date(2022, 12, 31)
                and ac["cert_issue"] <= erp_floor):
            cq9_row, cq9_ac = row, ac
            break
    assert cq9_row is not None, "no suitable CQ-9 NTSB anchor generated"
    anchors["cq9"] = (cq9_row, cq9_ac)
    ev_d = date.fromisoformat(cq9_row["EV_DATE"])

    def emit(ac: dict, open_d: date, comp: tuple[str, str], styled: bool,
             cost: float | None = None) -> dict:
        nonlocal wo
        wo += 1
        close_d = open_d + timedelta(days=rng.randint(0, 45))
        op = ac["registrant"]["op"]
        if op is not None:
            op_name = rng.choice(op["erp"])
        else:
            op_name = ac["registrant"]["name"].title()
        c = cost if cost is not None else round(math.exp(rng.uniform(math.log(80), math.log(90000))), 2)
        row = {
            "WORK_ORDER_ID": f"WO-{wo}",
            "TAIL_NUMBER": ac["tail"],
            "COMPONENT": comp[0],
            "ATA_CHAPTER": comp[1],
            "ACTION": rng.choice(ACTIONS),
            "MECHANIC_ID": rng.choice(mechanics),
            "LABOR_HOURS": f"{rng.uniform(0.5, 120):.1f}",
            "COST": fmt_cost(rng, c, styled),
            "OPEN_DATE": iso(open_d),
            "CLOSE_DATE": iso(close_d),
            "OPERATOR_NAME": op_name,
        }
        rows.append(row)
        gold.aircraft_pair(ac, "maintenance_erp", row["WORK_ORDER_ID"])
        if op is not None:
            gold.operator_pair(op, "maintenance_erp", row["WORK_ORDER_ID"])
        return row

    # 4 anchored orders: 2 before the accident, 2 after, mixed cost styles
    before1 = emit(cq9_ac, rand_date_between(rng, erp_floor, ev_d - timedelta(days=40)),
                   COMPONENTS[3], styled=True, cost=4210.50)
    before2 = emit(cq9_ac, rand_date_between(rng, erp_floor, ev_d - timedelta(days=40)),
                   COMPONENTS[13], styled=False, cost=812.25)
    after1 = emit(cq9_ac, rand_date_between(rng, ev_d + timedelta(days=1), erp_ceil - timedelta(days=60)),
                  COMPONENTS[17], styled=True, cost=23145.75)
    after2 = emit(cq9_ac, rand_date_between(rng, ev_d + timedelta(days=1), erp_ceil - timedelta(days=60)),
                  COMPONENTS[0], styled=False, cost=6499.00)
    anchors["cq9_orders"] = [before1, before2, after1, after2]

    # ---- bulk ----
    n_orphans = 3
    orphan_tails = []
    used = {a["tail"] for a in aircraft}
    for _ in range(n_orphans):
        orphan_tails.append(gen_tail(rng, used))
    while len(rows) < N_ERP - n_orphans:
        i = len(rows)
        if ind_pool and i % 9 == 8:
            ac = rng.choice(ind_pool)
        else:
            ac = rng.choice(pool)
        lo = max(erp_floor, ac["cert_issue"])
        open_d = rand_date_between(rng, lo, erp_ceil)
        comp = rng.choice(COMPONENTS)
        emit(ac, open_d, comp, styled=(i % 2 == 0))
    for j, t in enumerate(orphan_tails):  # referential-break wart (documented)
        wo += 1
        open_d = rand_date(rng, 2018, 2024)
        comp = rng.choice(COMPONENTS)
        rows.append({
            "WORK_ORDER_ID": f"WO-{wo}", "TAIL_NUMBER": t, "COMPONENT": comp[0],
            "ATA_CHAPTER": comp[1], "ACTION": rng.choice(ACTIONS),
            "MECHANIC_ID": rng.choice(mechanics),
            "LABOR_HOURS": f"{rng.uniform(0.5, 40):.1f}",
            "COST": fmt_cost(rng, round(rng.uniform(100, 9000), 2), styled=(j % 2 == 0)),
            "OPEN_DATE": iso(open_d), "CLOSE_DATE": iso(open_d + timedelta(days=rng.randint(1, 30))),
            "OPERATOR_NAME": "Zephyr Air Parts Brokerage",
        })
    rows.sort(key=lambda r: r["WORK_ORDER_ID"])
    return {"rows": rows, "anchors": anchors}


# --------------------------------------------------------------------------
# gold mini-ontology (frozen for the §11.3 de-risking vertical slice)
# --------------------------------------------------------------------------

def build_ontology() -> dict:
    def p(name, datatype="string", **kw):
        d = {"name": name, "datatype": datatype}
        d.update(kw)
        return d

    def link(name, range_, **kw):
        return {"name": name, "is_link": True, "range": range_, **kw}

    classes = [
        {"name": "Agent", "parents": [], "is_event": False,
         "definition": "Any legal person able to own or operate aircraft.",
         "properties": [p("name", synonyms=["full name", "legal name"])],
         "shapes": [{"prop": "name", "min_count": 1}]},
        {"name": "Organization", "parents": ["Agent"], "is_event": False,
         "definition": "A company, school, bank, or government body.",
         "properties": [p("org_kind",
                          definition="airline|cargo|regional|charter|school|corporate|trustee|government|heli")],
         "shapes": [{"prop": "org_kind",
                     "in_values": ["airline", "cargo", "regional", "charter", "school",
                                   "corporate", "trustee", "government", "heli"]}]},
        {"name": "Person", "parents": ["Agent"], "is_event": False,
         "definition": "A natural person (individual registrant, crew, mechanic).",
         "properties": [], "shapes": []},
        {"name": "Manufacturer", "parents": ["Organization"], "is_event": False,
         "definition": "An airframe or engine maker. The FAA registry spells the same "
                       "maker multiple ways (e.g. ROCKWELL INTERNATIONAL CORP vs ROCKWELL INTL).",
         "properties": [p("name_variants", cardinality="many",
                          definition="observed registry spellings")],
         "shapes": []},
        {"name": "Operator", "parents": ["Organization"], "is_event": False,
         "definition": "An organization that operates or holds the registration of aircraft.",
         "properties": [], "shapes": []},
        {"name": "Mechanic", "parents": ["Person"], "is_event": False,
         "definition": "A certificated maintenance technician identified by ERP id.",
         "properties": [p("mechanic_id", functional=True)],
         "shapes": [{"prop": "mechanic_id", "min_count": 1, "max_count": 1,
                     "pattern": "^M-[0-9]{3}$"}]},
        {"name": "Aircraft", "parents": [], "is_event": False,
         "definition": "A physical airframe. Identity anchors on serial number + model; "
                       "tail numbers are reused over time and are NOT identity.",
         "properties": [
             p("serial_number", functional=True),
             p("tail_number", synonyms=["n-number", "registration number"]),
             p("year_mfr", "integer"),
             p("mode_s_code"),
             link("model", "AircraftModel", cardinality="one"),
             link("engine", "Engine"),
             link("registrant", "Agent"),
         ],
         "shapes": [{"prop": "serial_number", "min_count": 1, "max_count": 1},
                    {"prop": "tail_number", "pattern": "^N[1-9][0-9A-Z]*$"},
                    {"prop": "year_mfr", "min_value": 1903, "max_value": 2026}]},
        {"name": "AircraftModel", "parents": [], "is_event": False,
         "definition": "A type design (manufacturer + model designation), per FAA ACFTREF.",
         "properties": [
             p("model_name", synonyms=["model", "type designation"]),
             p("mfr_mdl_code", functional=True),
             link("manufacturer", "Manufacturer"),
             p("seats", "integer", dimension={"count": 1}),
             p("engine_count", "integer", dimension={"count": 1}),
             p("weight_class"),
             p("cruise_speed", "float", dimension={"m": 1, "s": -1}, unit="mph",
               definition="ACFTREF average cruising speed; 0 when not published"),
             p("type_aircraft"),
         ],
         "shapes": [{"prop": "mfr_mdl_code", "min_count": 1, "max_count": 1,
                     "pattern": "^[0-9]{7}$"},
                    {"prop": "engine_count", "min_value": 0, "max_value": 8},
                    {"prop": "type_aircraft", "in_values": ["4", "5", "6"]}]},
        {"name": "Engine", "parents": [], "is_event": False,
         "definition": "An engine type (ENG MFR MDL code in the registry).",
         "properties": [p("eng_mfr_mdl", functional=True), p("engine_model"),
                        link("manufacturer", "Manufacturer"),
                        p("engine_type",
                          definition="FAA TYPE ENGINE code: 1 recip, 2 turboprop, 3 turboshaft, 5 turbofan")],
         "shapes": [{"prop": "engine_type", "in_values": ["0", "1", "2", "3", "4", "5"]}]},
        {"name": "Component", "parents": [], "is_event": False,
         "definition": "A maintainable aircraft component, addressed by ATA chapter.",
         "properties": [p("component_name"), p("ata_chapter")],
         "shapes": [{"prop": "ata_chapter", "pattern": "^[0-9]{2}$"}]},
        {"name": "Place", "parents": [], "is_event": False,
         "definition": "A named location in the US.",
         "properties": [p("place_name"), p("city"), p("state")],
         "shapes": [{"prop": "state", "pattern": "^[A-Z]{2}$"}]},
        {"name": "Airport", "parents": ["Place"], "is_event": False,
         "definition": "An airport with an IATA code (OpenFlights-seeded).",
         "properties": [p("iata", functional=True)],
         "shapes": [{"prop": "iata", "min_count": 1, "max_count": 1, "pattern": "^[A-Z]{3}$"}]},
        {"name": "Registration", "parents": [], "is_event": True,
         "definition": "The bi-temporal binding of a tail number to an airframe and a "
                       "registrant. Carries the validity window that resolves N-number reuse.",
         "properties": [
             link("aircraft", "Aircraft"), link("registrant_party", "Agent"),
             p("cert_issue_date", "date"), p("expiration_date", "date"),
             p("last_action_date", "date"), p("status_code"),
         ],
         "shapes": [{"prop": "status_code", "in_values": ["V", "D"]},
                    {"prop": "cert_issue_date", "min_count": 1}]},
        {"name": "SafetyEvent", "parents": [], "is_event": True,
         "definition": "Any reported aviation safety occurrence.",
         "properties": [
             p("event_date", "date"),
             link("aircraft", "Aircraft"), link("operator", "Operator"),
             link("place", "Place"),
         ],
         "shapes": [{"prop": "event_date", "min_count": 1}]},
        {"name": "IncidentReport", "parents": ["SafetyEvent"], "is_event": True,
         "definition": "A voluntary ASRS incident report (de-identified, self-reported).",
         "properties": [
             p("acn", functional=True),
             p("altitude_agl", "float", dimension={"m": 1}, unit="ft",
               definition="altitude above ground; a wart slice is recorded in meters "
                          "with an 'm' suffix and must be converted"),
             p("flight_phase"),
             p("narrative", "text"), p("synopsis", "text"),
         ],
         "shapes": [{"prop": "acn", "min_count": 1, "max_count": 1},
                    {"prop": "altitude_agl", "min_value": 0, "max_value": 60000, "unit": "ft"},
                    {"prop": "flight_phase",
                     "in_values": ["Takeoff", "Initial Climb", "Climb", "Cruise", "Descent",
                                   "Initial Approach", "Final Approach", "Landing", "Taxi"]}]},
        {"name": "AccidentEvent", "parents": ["SafetyEvent"], "is_event": True,
         "definition": "An NTSB-investigated accident or incident.",
         "properties": [
             p("ntsb_number", functional=True), p("ev_type"),
             p("damage"), p("fatalities", "integer", dimension={"count": 1}),
             p("cause_narrative", "text"),
         ],
         "shapes": [{"prop": "ev_type", "in_values": ["ACC", "INC"]},
                    {"prop": "damage", "in_values": ["DEST", "SUBS", "MINR", "NONE"]},
                    {"prop": "fatalities", "min_value": 0}]},
        {"name": "WorkOrder", "parents": [], "is_event": True,
         "definition": "A maintenance ERP work order on an airframe.",
         "properties": [
             p("work_order_id", functional=True),
             link("aircraft", "Aircraft"), link("component", "Component"),
             link("mechanic", "Mechanic"), link("operator", "Operator"),
             p("action"),
             p("labor_hours", "float", dimension={"s": 1}, unit="h"),
             p("cost", "float", dimension={"currency": 1}, unit="USD",
               definition="lexical forms mix 'USD 1,234.56' and '1234.56' (ANVIL bait)"),
             p("open_date", "date"), p("close_date", "date"),
         ],
         "shapes": [{"prop": "work_order_id", "min_count": 1, "max_count": 1,
                     "pattern": "^WO-[0-9]{6}$"},
                    {"prop": "action", "in_values": ACTIONS},
                    {"prop": "labor_hours", "min_value": 0, "max_value": 2000, "unit": "h"},
                    {"prop": "cost", "min_value": 0, "unit": "USD"}]},
    ]
    return {
        "estate": "aviation",
        "version": 1,
        "namespace": "onto://gold/aviation",
        "provenance": "gold://aviation/mini_ontology.json",
        "generator": {"script": "scripts/build_aviation_fixtures.py", "seed": SEED},
        "classes": classes,
    }


# --------------------------------------------------------------------------
# competency questions (gold answers computed from the generated rows)
# --------------------------------------------------------------------------

def parse_alt_ft(s: str) -> float | None:
    s = s.strip()
    if not s:
        return None
    if s.endswith("m"):
        return float(s[:-1]) * FT_PER_M
    return float(s)


def parse_cost(s: str) -> float:
    return float(s.replace("USD", "").replace(",", "").strip())


def cite(table: str, row_key: str, column: str) -> dict:
    return {"table": table, "row_key": row_key, "column": column}


def master_key(row: dict) -> str:
    return f"{row['N-NUMBER'].strip()}|{row['SERIAL NUMBER'].strip()}"


def build_competency(fleet: dict, models: list[dict], asrs: dict, ntsb: dict,
                     erp: dict, gold: GoldPairs) -> dict:
    aircraft = fleet["aircraft"]
    asrs_rows = asrs["rows"]
    ntsb_rows = ntsb["rows"]
    erp_rows = erp["rows"]
    questions: list[dict] = []

    def q(qid, kinds, question, answer, citations, answerable=True, expected="answer", notes=""):
        questions.append({
            "id": qid, "kinds": kinds, "question": question, "answerable": answerable,
            "expected_behavior": expected, "answer": answer, "citations": citations,
            "notes": notes,
        })

    # --- CQ-01: 2-hop manufacturer lookup ---------------------------------
    gulf = next(a for a in aircraft if a["model"]["mfr_key"] == "GULFSTREAM" and a["status"] == "V")
    q("CQ-01", ["multi_hop"],
      f"Which manufacturer name does the FAA aircraft reference record for the model of "
      f"the aircraft registered with tail number {gulf['tail']}?",
      gulf["model"]["mfr_name"],
      [cite("faa_master", gulf["row_key"], "MFR MDL CODE"),
       cite("faa_acftref", gulf["model"]["code"], "MFR")],
      notes="join MASTER.'MFR MDL CODE' -> ACFTREF.CODE; registry N-NUMBER omits the leading N")

    # --- CQ-02: aggregation over manufacturer-name variants ----------------
    rockwell_codes = sorted(m["code"] for m in models if m["mfr_key"] == "ROCKWELL")
    rockwell_acs = [a for a in aircraft if a["model"]["mfr_key"] == "ROCKWELL"]
    cits = [cite("faa_master", a["row_key"], "MFR MDL CODE") for a in
            sorted(rockwell_acs, key=lambda a: a["row_key"])]
    cits += [cite("faa_acftref", c, "MFR") for c in rockwell_codes]
    q("CQ-02", ["multi_hop", "aggregation", "name_variants"],
      "How many registry aircraft were built by Rockwell, counting BOTH the "
      "'ROCKWELL INTERNATIONAL CORP' and 'ROCKWELL INTL' manufacturer spellings in the "
      "FAA aircraft reference?",
      len(rockwell_acs), cits,
      notes="the two spellings are the same manufacturer entity (FAA-documented wart)")

    # --- CQ-03 / CQ-04: temporal as-of over a reused N-number --------------
    reuse_tail = fleet["reuse_tails"][0]
    pair = sorted([a for a in aircraft if a["tail"] == reuse_tail],
                  key=lambda a: a["cert_issue"])
    old_ac, new_ac = pair[0], pair[1]
    asof_old = old_ac["cert_issue"] + (old_ac["expiration"] - old_ac["cert_issue"]) / 2
    q("CQ-03", ["temporal_as_of"],
      f"Who was the registrant of tail number {reuse_tail} as of {iso(asof_old)}?",
      old_ac["registrant"]["name"],
      [cite("faa_master", old_ac["row_key"], "REGISTRANT NAME"),
       cite("faa_master", old_ac["row_key"], "CERT ISSUE DATE"),
       cite("faa_master", old_ac["row_key"], "EXPIRATION DATE")],
      notes="this N-number is REUSED; the as-of date selects the deregistered airframe")
    q("CQ-04", ["temporal_as_of"],
      f"Which airframe serial number did tail number {reuse_tail} refer to as of 2024-06-01?",
      new_ac["serial"],
      [cite("faa_master", new_ac["row_key"], "SERIAL NUMBER"),
       cite("faa_master", new_ac["row_key"], "CERT ISSUE DATE"),
       cite("faa_master", old_ac["row_key"], "EXPIRATION DATE")],
      notes="the older registration window ended decades earlier")

    # --- CQ-05: unit-sensitive threshold (meters wart) ----------------------
    descent = [r for r in asrs_rows if r["FLIGHT PHASE"] == "Descent"]
    below = [r for r in descent
             if (alt := parse_alt_ft(r["ALTITUDE.AGL.SINGLE VALUE"])) is not None and alt < 10000.0]
    q("CQ-05", ["unit_sensitive", "aggregation"],
      "How many ASRS reports with flight phase 'Descent' record an altitude AGL below "
      "10,000 ft? Note: some altitudes carry an 'm' suffix and are in METERS "
      "(1 m = 3.28084 ft).",
      len(below),
      [cite("asrs_reports", r["ACN"], "ALTITUDE.AGL.SINGLE VALUE") for r in below],
      notes="a naive reader that ignores the 'm' suffix gets a different count")

    # --- CQ-06: unit conversion --------------------------------------------
    meter_rows = [r for r in asrs_rows if r["ALTITUDE.AGL.SINGLE VALUE"].strip().endswith("m")]
    min_row = min(meter_rows, key=lambda r: float(r["ALTITUDE.AGL.SINGLE VALUE"].strip()[:-1]))
    min_m = float(min_row["ALTITUDE.AGL.SINGLE VALUE"].strip()[:-1])
    q("CQ-06", ["unit_sensitive"],
      "Among ASRS reports whose altitude AGL was recorded in meters ('m' suffix), what is "
      "the lowest altitude expressed in feet, rounded to the nearest foot "
      "(1 m = 3.28084 ft)?",
      round(min_m * FT_PER_M),
      [cite("asrs_reports", min_row["ACN"], "ALTITUDE.AGL.SINGLE VALUE")],
      notes=f"lowest meters value is {min_row['ALTITUDE.AGL.SINGLE VALUE']}")

    # --- CQ-07: structured<->unstructured (bird strikes) --------------------
    bird = asrs["anchors"]["bird"]
    tails = sorted({ac["tail"] for _, ac in bird})
    cits = [cite("asrs_reports", row["ACN"], "NARRATIVE") for row, _ in bird]
    cits += [cite("faa_master", ac["row_key"], "N-NUMBER")
             for ac in sorted({a["row_key"]: a for _, a in bird}.values(), key=lambda a: a["row_key"])]
    q("CQ-07", ["structured_unstructured", "multi_hop"],
      "Which registry tail numbers appear in ASRS narratives that describe a bird strike?",
      tails, cits,
      notes="tail numbers occur only inside the NARRATIVE free text")

    # --- CQ-08: narrative -> registry -> reference --------------------------
    cq8_row, cq8_ac = asrs["anchors"]["cq8"]
    q("CQ-08", ["structured_unstructured", "multi_hop"],
      f"ASRS report ACN {cq8_row['ACN']} names a registry aircraft in its narrative. "
      f"Per the FAA aircraft reference, who manufactured that aircraft?",
      cq8_ac["model"]["mfr_name"],
      [cite("asrs_reports", cq8_row["ACN"], "NARRATIVE"),
       cite("faa_master", cq8_ac["row_key"], "MFR MDL CODE"),
       cite("faa_acftref", cq8_ac["model"]["code"], "MFR")],
      notes="three sources: narrative text, registry row, ACFTREF row")

    # --- CQ-09: 3-source temporal aggregation ------------------------------
    cq9_row, cq9_ac = erp["anchors"]["cq9"]
    ev_d = date.fromisoformat(cq9_row["EV_DATE"])
    wos = [r for r in erp_rows if r["TAIL_NUMBER"] == cq9_ac["tail"]
           and date.fromisoformat(r["OPEN_DATE"]) > ev_d]
    total = round(sum(parse_cost(r["COST"]) for r in wos), 2)
    cits = [cite("ntsb_events", cq9_row["EV_ID"], "EV_DATE")]
    for r in sorted(wos, key=lambda r: r["WORK_ORDER_ID"]):
        cits.append(cite("maintenance_erp", r["WORK_ORDER_ID"], "COST"))
        cits.append(cite("maintenance_erp", r["WORK_ORDER_ID"], "OPEN_DATE"))
    q("CQ-09", ["multi_hop", "temporal_as_of", "aggregation", "unit_sensitive"],
      f"For tail number {cq9_ac['tail']}, what was the total maintenance cost in USD across "
      f"work orders OPENED AFTER its NTSB event of {cq9_row['EV_DATE']}? "
      f"Cost values mix 'USD 1,234.56' and bare '1234.56' lexical forms.",
      f"{total:.2f}", cits,
      notes="requires NTSB date + ERP date filter + mixed-format currency parsing")

    # --- CQ-10: aggregation with operator-name variants in ERP --------------
    lg = [r for r in erp_rows if r["COMPONENT"] == "LANDING GEAR"]
    lg_total = round(sum(float(r["LABOR_HOURS"]) for r in lg), 1)
    q("CQ-10", ["aggregation"],
      "What is the total LABOR_HOURS across all maintenance work orders whose component "
      "is LANDING GEAR (ATA chapter 32)?",
      f"{lg_total:.1f}",
      [cite("maintenance_erp", r["WORK_ORDER_ID"], "LABOR_HOURS")
       for r in sorted(lg, key=lambda r: r["WORK_ORDER_ID"])])

    # --- CQ-11: operator-variant folding count ------------------------------
    op1 = OPERATORS[0]  # Delta
    variants = {v.strip().upper() for v in op1["erp"]}
    op_rows = [r for r in erp_rows if r["OPERATOR_NAME"].strip().upper() in variants]
    q("CQ-11", ["aggregation", "name_variants"],
      f"How many maintenance work orders belong to {op1['registry'][0]} when all of its "
      f"ERP operator-name spellings are folded together?",
      len(op_rows),
      [cite("maintenance_erp", r["WORK_ORDER_ID"], "OPERATOR_NAME")
       for r in sorted(op_rows, key=lambda r: r["WORK_ORDER_ID"])],
      notes=f"ERP spellings: {sorted(op1['erp'])}")

    # --- CQ-12: N-number reuse discovery ------------------------------------
    by_tail: dict[str, set[str]] = {}
    for a in aircraft:
        by_tail.setdefault(a["tail"], set()).add(a["serial"])
    reused = sorted(t for t, s in by_tail.items() if len(s) > 1)
    cits = []
    for t in reused:
        for a in sorted([x for x in aircraft if x["tail"] == t], key=lambda x: x["row_key"]):
            cits.append(cite("faa_master", a["row_key"], "SERIAL NUMBER"))
    q("CQ-12", ["temporal_as_of", "aggregation"],
      "Which tail numbers in the FAA registry fixture are associated with more than one "
      "airframe serial number (N-number reuse)?",
      reused, cits,
      notes="the registration windows of each pair do not overlap")

    # --- CQ-13: NTSB narrative + dropped-N normalization ---------------------
    fuel_row, fuel_ac = ntsb["anchors"]["fuel"]
    q("CQ-13", ["structured_unstructured", "name_variants"],
      "Which NTSB event cites fuel exhaustion in its cause narrative, and what is the "
      "aircraft's registration number normalized WITH its leading 'N'?",
      {"ntsb_no": fuel_row["NTSB_NO"], "tail": fuel_ac["tail"]},
      [cite("ntsb_events", fuel_row["EV_ID"], "NARR_CAUSE"),
       cite("ntsb_events", fuel_row["EV_ID"], "ACFT_REGIST_NMBR"),
       cite("faa_master", fuel_ac["row_key"], "N-NUMBER")],
      notes="the NTSB row stores the registration without the leading N (documented wart)")

    # --- CQ-14: cross-source operator hop ------------------------------------
    cq14_row, cq14_ac = ntsb["anchors"]["cq14"]
    q("CQ-14", ["multi_hop"],
      f"What damage level did the NTSB record for event {cq14_row['NTSB_NO']}, and who is "
      f"the registry registrant of the involved aircraft?",
      {"damage": cq14_row["DAMAGE"], "registrant": cq14_ac["registrant"]["name"]},
      [cite("ntsb_events", cq14_row["EV_ID"], "DAMAGE"),
       cite("ntsb_events", cq14_row["EV_ID"], "ACFT_REGIST_NMBR"),
       cite("faa_master", cq14_ac["row_key"], "REGISTRANT NAME")])

    # --- CQ-15: aggregation with blank cells ----------------------------------
    states = sorted({r["EV_STATE"] for r in ntsb_rows})
    best_state = None
    for st in states:
        sub = [r for r in ntsb_rows if r["EV_STATE"] == st]
        numeric = [r for r in sub if r["INJ_TOT_F"].strip() != ""]
        blanks = [r for r in sub if r["INJ_TOT_F"].strip() == ""]
        if len(sub) >= 4 and blanks and sum(int(r["INJ_TOT_F"]) for r in numeric) > 0:
            best_state = st
            break
    assert best_state is not None, "no NTSB state with blanks + fatalities"
    sub = [r for r in ntsb_rows if r["EV_STATE"] == best_state]
    numeric = [r for r in sub if r["INJ_TOT_F"].strip() != ""]
    fat = sum(int(r["INJ_TOT_F"]) for r in numeric)
    q("CQ-15", ["aggregation"],
      f"How many total fatalities are RECORDED across NTSB events in {best_state}? "
      f"(Blank INJ_TOT_F cells are unknown, not zero — exclude them.)",
      fat,
      [cite("ntsb_events", r["EV_ID"], "INJ_TOT_F")
       for r in sorted(numeric, key=lambda r: r["EV_ID"])],
      notes="blank-vs-zero distinction; blank permissible fields are not errors")

    # --- CQ-16 / CQ-17: unanswerable (abstention targets) ---------------------
    some_tail = aircraft[100]["tail"]
    q("CQ-16", ["unanswerable"],
      f"What was the total airframe time in flight hours for {some_tail} at its most "
      f"recent annual inspection?",
      None, [], answerable=False, expected="abstain",
      notes="airframe hours appear in no estate source; the system must abstain")
    q("CQ-17", ["unanswerable"],
      f"Which insurance company underwrote the hull policy for {some_tail} in 2022?",
      None, [], answerable=False, expected="abstain",
      notes="insurance data appears in no estate source; the system must abstain")

    # --- CQ-18: trick unit (OQIR type-check rejection) -------------------------
    q("CQ-18", ["trick_unit"],
      "What is the total altitude in dollars across all ASRS incident reports?",
      None, [], answerable=False, expected="reject_unit_mismatch",
      notes="altitude has dimension length (ft); dollars is currency — the OQIR "
            "type-checker must reject the query, not coerce it")

    return {
        "estate": "aviation",
        "version": 1,
        "generator_seed": SEED,
        "conventions": {
            "row_key": "values of the table's key columns, stripped of padding, joined with '|'",
            "key_columns": {
                "faa_master": ["N-NUMBER", "SERIAL NUMBER"],
                "faa_acftref": ["CODE"],
                "asrs_reports": ["ACN"],
                "ntsb_events": ["EV_ID"],
                "maintenance_erp": ["WORK_ORDER_ID"],
            },
            "string_answers": "compared after stripping surrounding whitespace",
            "ft_per_m": FT_PER_M,
        },
        "questions": questions,
    }


# --------------------------------------------------------------------------
# emission
# --------------------------------------------------------------------------

def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    path.write_text(buf.getvalue(), encoding="utf-8")


def build_all(out_dir: Path, seed_dir: Path) -> dict:
    rng = random.Random(SEED)
    airports = load_airports(seed_dir)
    if not airports:
        raise SystemExit("no airport vocabulary available")
    models = build_models(rng, seed_dir)
    fleet = build_fleet(rng, models, airports)
    gold = GoldPairs()
    gold.set_operator_reps(fleet["aircraft"])
    asrs = build_asrs(rng, fleet, airports, gold)
    ntsb = build_ntsb(rng, fleet, airports, gold)
    erp = build_erp(rng, fleet, ntsb, gold)
    cq = build_competency(fleet, models, asrs, ntsb, erp, gold)
    onto = build_ontology()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gold").mkdir(exist_ok=True)
    write_csv(out_dir / "faa_master.csv", MASTER_COLUMNS, fleet["master_rows"])
    write_csv(out_dir / "faa_acftref.csv", ACFTREF_COLUMNS, acftref_rows(models))
    write_csv(out_dir / "asrs_reports.csv", ASRS_COLUMNS, asrs["rows"])
    write_csv(out_dir / "ntsb_events.csv", NTSB_COLUMNS, ntsb["rows"])
    write_csv(out_dir / "maintenance_erp.csv", ERP_COLUMNS, erp["rows"])
    write_csv(out_dir / "gold" / "er_gold_pairs.csv", GOLD_PAIR_COLUMNS, gold.sorted_rows())
    (out_dir / "gold" / "mini_ontology.json").write_text(
        json.dumps(onto, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "gold" / "competency_questions.yaml").write_text(
        yamlite.dumps(cq), encoding="utf-8")
    return {"aircraft": len(fleet["aircraft"]), "master": len(fleet["master_rows"]),
            "acftref": len(models), "asrs": len(asrs["rows"]), "ntsb": len(ntsb["rows"]),
            "erp": len(erp["rows"]), "gold_pairs": len(gold.rows),
            "questions": len(cq["questions"])}


# --------------------------------------------------------------------------
# seed refresh (network; never run in CI — fixtures pin the result)
# --------------------------------------------------------------------------

OPENFLIGHTS_AIRPORTS = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"
OPENFLIGHTS_PLANES = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/planes.dat"


def refresh_seeds(seed_dir: Path) -> None:
    import urllib.request
    from datetime import datetime, timezone

    seed_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"retrieved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                      "sources": {}}

    def fetch(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "ontoforge-fixtures/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    raw_air = fetch(OPENFLIGHTS_AIRPORTS)
    raw_planes = fetch(OPENFLIGHTS_PLANES)
    manifest["sources"]["airports.dat"] = {
        "url": OPENFLIGHTS_AIRPORTS, "sha256": hashlib.sha256(raw_air).hexdigest(),
        "bytes": len(raw_air)}
    manifest["sources"]["planes.dat"] = {
        "url": OPENFLIGHTS_PLANES, "sha256": hashlib.sha256(raw_planes).hexdigest(),
        "bytes": len(raw_planes)}

    air_rows = []
    for rec in csv.reader(io.StringIO(raw_air.decode("utf-8"))):
        if len(rec) < 5:
            continue
        name, city, country, iata = rec[1], rec[2], rec[3], rec[4]
        if country == "United States" and len(iata) == 3 and iata.isalpha() and iata.isupper():
            air_rows.append({"IATA": iata, "NAME": name, "CITY": city})
    air_rows.sort(key=lambda r: r["IATA"])
    dedup = {}
    for r in air_rows:
        dedup.setdefault(r["IATA"], r)
    write_csv(seed_dir / "airports_us.csv", ["IATA", "NAME", "CITY"], list(dedup.values()))

    plane_rows = []
    for rec in csv.reader(io.StringIO(raw_planes.decode("utf-8"))):
        if len(rec) >= 3:
            plane_rows.append({"NAME": rec[0], "IATA": rec[1], "ICAO": rec[2]})
    plane_rows.sort(key=lambda r: r["NAME"])
    write_csv(seed_dir / "planes.csv", ["NAME", "IATA", "ICAO"], plane_rows)

    manifest["trimmed"] = {"airports_us.csv": len(dedup), "planes.csv": len(plane_rows)}
    manifest["notes"] = (
        "OpenFlights downloads succeeded and are pinned here (trimmed). "
        "registry.faa.gov returns HTTP 403 to non-browser clients; the NTSB bulk "
        "avall.zip is a ~95MB Access database — both replaced by AMD-0006 "
        "schema-faithful generation.")
    (seed_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"seeds refreshed: {len(dedup)} US airports, {len(plane_rows)} plane types")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    ap.add_argument("--refresh-seeds", action="store_true",
                    help="re-download OpenFlights seeds (network!)")
    args = ap.parse_args(argv)
    if args.refresh_seeds:
        refresh_seeds(args.seed_dir)
    stats = build_all(args.out, args.seed_dir)
    print(json.dumps(stats))


if __name__ == "__main__":
    main()

"""Deterministic NON-aviation corpus for the generic estate engine: a retail
estate (customers / orders / products / suppliers) with the warts the engine
must conform — supplier-name spelling variants shared across tables, USD
prefix forms mixed with bare costs, kg-suffixed weights, ISO date columns,
exact city -> state FDs (a 3NF latent type), and clean FK INDs.

Everything is seeded; the expected answers asserted in the tests are computed
HERE from the same frames the CSVs are written from.
"""

from __future__ import annotations

import random
from pathlib import Path

import pandas as pd
import pytest

SEED = 1306

FIRST = [
    "AVA", "BEN", "CARLA", "DEV", "ELENA", "FELIX", "GINA", "HUGO", "IRIS", "JONAS",
    "KIRA", "LEO", "MAYA", "NICO", "OLGA", "PAUL", "QUINN", "ROSA", "SAUL", "TARA",
]
LAST = [
    "ADLER", "BRANDT", "CHEN", "DUARTE", "EBERT", "FISCHER", "GOMEZ", "HUANG",
    "IBARRA", "JENSEN", "KOWALSKI", "LINDGREN", "MORENO", "NAKAMURA", "OKAFOR",
    "PETROV", "QUIROGA", "RIVERA", "SATO", "TANAKA", "UENO", "VARGA", "WEBER",
]
CITY_STATE = [
    ("AUSTIN", "TX"), ("DALLAS", "TX"), ("DENVER", "CO"), ("BOULDER", "CO"),
    ("PORTLAND", "OR"), ("EUGENE", "OR"), ("MADISON", "WI"), ("TUCSON", "AZ"),
    ("ALBANY", "NY"), ("BUFFALO", "NY"), ("SAVANNAH", "GA"), ("TAMPA", "FL"),
]
SEGMENTS = ["consumer", "corporate", "home office"]
CATEGORIES = [
    "ELECTRONICS", "KITCHEN", "GARDEN", "TOYS", "STATIONERY", "LIGHTING",
    "FURNITURE", "SPORTS",
]
COUNTRIES = ["US", "DE", "JP", "MX", "PL", "VN"]
STATUSES = ["delivered", "shipped", "returned", "cancelled", "processing"]
SUPPLIER_WORDS = [
    "NORTHWIND", "GLOBEX", "INITECH", "UMBRELLA", "STARK", "WAYNE", "ACME",
    "TYRELL", "WONKA", "CYBERDYNE", "VANDELAY", "PIEDPIPER", "HOOLI", "OSCORP",
    "MASSIVE", "DUFF", "SIRIUS", "MONARCH", "ZENITH", "ATLAS", "ORION", "VERTEX",
    "LUMEN", "QUARTZ", "BOREAL", "CASCADE", "DELTA", "EMBER", "FALCON", "GRANITE",
]
SUPPLIER_SUFFIX = ["SUPPLY CO", "TRADING", "GOODS", "DISTRIBUTION", "WHOLESALE"]
LEGAL = ["INC", "LLC", "LTD", "CORP"]


def _supplier_names(rng: random.Random, n: int) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    i = 0
    while len(names) < n:
        w1 = SUPPLIER_WORDS[i % len(SUPPLIER_WORDS)]
        w2 = SUPPLIER_SUFFIX[(i // len(SUPPLIER_WORDS)) % len(SUPPLIER_SUFFIX)]
        legal = LEGAL[i % len(LEGAL)]
        name = f"{w1} {w2} {legal}"
        if name not in seen:
            seen.add(name)
            names.append(name)
        i += 1
    return names


def _variant(rng: random.Random, name: str) -> str:
    """A real-world spelling variant of a supplier name (legal-suffix noise,
    punctuation), the way procurement systems mangle vendor names."""
    parts = name.split()
    choice = rng.randrange(3)
    if choice == 0:
        return " ".join(parts[:-1])                       # drop legal suffix
    if choice == 1:
        return " ".join(parts[:-1]) + f" {parts[-1]}."    # 'INC.'
    return ", ".join([" ".join(parts[:-1]), parts[-1]])   # 'X GOODS, LLC'


def build_retail_frames() -> dict[str, pd.DataFrame]:
    rng = random.Random(SEED)

    n_suppliers, n_products, n_customers, n_orders = 150, 200, 200, 250
    supplier_names = _supplier_names(rng, n_suppliers)
    suppliers = pd.DataFrame(
        {
            "supplier_id": [f"S{i:03d}" for i in range(1, n_suppliers + 1)],
            "supplier_name": supplier_names,
            "country": [COUNTRIES[i % len(COUNTRIES)] for i in range(n_suppliers)],
            "rating": [str(rng.randint(1, 5)) for _ in range(n_suppliers)],
        }
    )

    products = []
    for i in range(1, n_products + 1):
        sup = supplier_names[(i * 7) % n_suppliers]
        # ~30% of product rows carry a mangled supplier spelling (ER work)
        sup_lex = _variant(rng, sup) if rng.random() < 0.30 else sup
        price = round(rng.uniform(4, 900), 2)
        weight = round(rng.uniform(0.2, 40.0), 1)
        products.append(
            {
                "product_id": f"P{i:04d}",
                "product_name": f"{CATEGORIES[i % len(CATEGORIES)].title()} item {i}",
                "category": CATEGORIES[i % len(CATEGORIES)],
                "unit_price": (f"USD {price:,.2f}" if rng.random() < 0.5 else f"{price:.2f}"),
                "weight": f"{weight} kg",
                "supplier_name": sup_lex,
                "_supplier_true": sup,
                "_price_num": price,
                "_weight_num": weight,
            }
        )
    products = pd.DataFrame(products)

    customers = []
    for i in range(1, n_customers + 1):
        city, state = CITY_STATE[i % len(CITY_STATE)]
        customers.append(
            {
                "customer_id": f"C{i:04d}",
                "customer_name": f"{FIRST[i % len(FIRST)]} {LAST[(i * 3) % len(LAST)]}",
                "city": city,
                "state": state,
                "segment": SEGMENTS[i % len(SEGMENTS)],
                "signup_date": f"20{18 + i % 6}-{1 + i % 12:02d}-{1 + i % 27:02d}",
            }
        )
    customers = pd.DataFrame(customers)

    orders = []
    for i in range(1, n_orders + 1):
        qty = rng.randint(1, 9)
        total = round(qty * rng.uniform(4, 900), 2)
        orders.append(
            {
                "order_id": f"O{i:05d}",
                "customer_id": f"C{1 + (i * 13) % n_customers:04d}",
                "product_id": f"P{1 + (i * 11) % n_products:04d}",
                "order_date": f"202{4 + i % 2}-{1 + i % 12:02d}-{1 + i % 27:02d}",
                "quantity": str(qty),
                "total_price": (f"USD {total:,.2f}" if i % 3 == 0 else f"{total:.2f}"),
                "status": STATUSES[i % len(STATUSES)],
                "_total_num": total,
            }
        )
    orders = pd.DataFrame(orders)
    return {
        "suppliers": suppliers,
        "products": products,
        "customers": customers,
        "orders": orders,
    }


@pytest.fixture(scope="session")
def retail_frames() -> dict[str, pd.DataFrame]:
    return build_retail_frames()


@pytest.fixture(scope="session")
def retail_dir(tmp_path_factory, retail_frames) -> Path:
    """The corpus on disk, hidden truth columns (underscore-prefixed) dropped."""
    out = tmp_path_factory.mktemp("retail")
    for name, df in retail_frames.items():
        public = df[[c for c in df.columns if not c.startswith("_")]]
        public.to_csv(out / f"{name}.csv", index=False)
    return out

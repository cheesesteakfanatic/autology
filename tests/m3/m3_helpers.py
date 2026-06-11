"""Seeded synthetic fixtures for M3: a mini TPC-H-like trio with DECLARED keys/FKs.

Declared truth (used by the recovery tests):
    customers : PK c_custkey                       (c_name also unique by construction)
    orders    : PK o_orderkey;  FK o_custkey  -> customers.c_custkey
                only customers 1..ACTIVE_CUST place orders, so the REVERSE
                inclusion c_custkey ⊆ o_custkey has coverage ACTIVE/N_CUST < 0.95.
    lineitems : PK (l_orderkey, l_linenumber); FK l_orderkey -> orders.o_orderkey
                every order has >= 1 line; order 1 is forced to 3 lines with
                distinct quantities so no single column determines the others.

All generators are deterministically seeded; non-key columns are deliberately
coarse (small domains) so single columns and most pairs are non-unique.
"""

from __future__ import annotations

import datetime
import random

SEED = 20260611
N_CUST = 120
ACTIVE_CUST = 100
N_ORDERS = 400

_SEGMENTS = ["AUTOMOBILE", "BUILDING", "FURNITURE", "MACHINERY", "HOUSEHOLD"]
_STATUSES = ["O", "F", "P"]
_PRICE_TIERS = [100.0, 250.0, 500.0, 750.0, 1000.0]
_DISCOUNTS = [0.0, 0.05, 0.1]


def make_customers(n_cust: int = N_CUST, seed: int = SEED) -> dict[str, list]:
    rng = random.Random(seed)
    return {
        "c_custkey": list(range(1, n_cust + 1)),
        "c_name": [f"Customer#{k:06d}" for k in range(1, n_cust + 1)],
        "c_nationkey": [rng.randrange(25) for _ in range(n_cust)],
        "c_acctbal": [round(rng.uniform(-999.99, 9999.99), 2) for _ in range(n_cust)],
        "c_mktsegment": [rng.choice(_SEGMENTS) for _ in range(n_cust)],
    }


def make_orders(n_orders: int = N_ORDERS, active_cust: int = ACTIVE_CUST,
                seed: int = SEED + 1) -> dict[str, list]:
    rng = random.Random(seed)
    base = datetime.date(1995, 1, 1)
    return {
        "o_orderkey": list(range(1, n_orders + 1)),
        "o_custkey": [rng.randint(1, active_cust) for _ in range(n_orders)],
        "o_status": [rng.choice(_STATUSES) for _ in range(n_orders)],
        "o_totalprice": [round(rng.uniform(900.0, 4500.0), 2) for _ in range(n_orders)],
        "o_orderdate": [(base + datetime.timedelta(days=rng.randrange(365))).isoformat()
                        for _ in range(n_orders)],
    }


def make_lineitems(n_orders: int = N_ORDERS, seed: int = SEED + 2) -> dict[str, list]:
    rng = random.Random(seed)
    base = datetime.date(1995, 1, 10)
    okeys: list[int] = []
    lnums: list[int] = []
    qtys: list[int] = []
    prices: list[float] = []
    discs: list[float] = []
    ships: list[str] = []
    # forced lines pin down ground truth regardless of rng draws:
    #   order 1 has 3 lines with distinct quantities (composite-key necessity), and
    #   extendedprice 500.0 occurs with quantities 5, 1, and 2 (so no single column
    #   — in particular l_extendedprice — determines l_quantity).
    forced: dict[int, list[tuple[int, float]]] = {
        1: [(5, 100.0), (17, 250.0), (41, 500.0)],
        2: [(1, 500.0), (2, 250.0)],
    }
    for okey in range(1, n_orders + 1):
        fixed = forced.get(okey)
        n_lines = len(fixed) if fixed else rng.randint(1, 4)
        for ln in range(1, n_lines + 1):
            okeys.append(okey)
            lnums.append(ln)
            if fixed:
                q, tier = fixed[ln - 1]
            else:
                q, tier = rng.randint(1, 50), rng.choice(_PRICE_TIERS)
            qtys.append(q)
            prices.append(tier * q)
            discs.append(rng.choice(_DISCOUNTS))
            ships.append((base + datetime.timedelta(days=rng.randrange(365))).isoformat())
    return {
        "l_orderkey": okeys,
        "l_linenumber": lnums,
        "l_quantity": qtys,
        "l_extendedprice": prices,
        "l_discount": discs,
        "l_shipdate": ships,
    }


def make_tpch() -> dict[str, dict[str, list]]:
    return {
        "customers": make_customers(),
        "orders": make_orders(),
        "lineitems": make_lineitems(),
    }


# --------------------------------------------------------- brute-force oracles


def fd_holds(columns: dict[str, list], lhs: tuple[str, ...], rhs: str) -> bool:
    """Ground-truth FD check by direct scan (nulls treated as one value, as in M3)."""
    n = max(len(v) for v in columns.values())
    seen: dict[tuple, object] = {}
    for i in range(n):
        key = tuple(columns[c][i] for c in lhs)
        val = columns[rhs][i]
        if key in seen:
            if seen[key] != val:
                return False
        else:
            seen[key] = val
    return True


def is_unique(columns: dict[str, list], key: tuple[str, ...]) -> bool:
    n = max(len(v) for v in columns.values())
    seen = set()
    for i in range(n):
        t = tuple(columns[c][i] for c in key)
        if t in seen:
            return False
        seen.add(t)
    return True

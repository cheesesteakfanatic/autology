"""profile_table orchestration tests (§3.1, §11.2 M3): φ(p) per column, table-level
FDs/keys, mixed-unit flagging through the contract, append-mostly hook, determinism,
and pandas/pyarrow input equivalence."""

from __future__ import annotations

import random

import pandas as pd
import pyarrow as pa

from m3_helpers import make_lineitems, make_orders
from ontoforge.contracts import LENGTH, SPEED, Datatype, TableProfile
from ontoforge.profiling import profile_table, profile_table_detailed


def _flights() -> dict[str, list]:
    rng = random.Random(11)
    tails = [f"N{rng.randint(1, 99999)}{'AB'[rng.randint(0, 1)] if rng.random() < 0.5 else ''}"
             for _ in range(60)]
    icaos = [rng.choice(["KJFK", "KLAX", "KSFO", "KORD", "EGLL", "KDEN"]) for _ in range(60)]
    return {
        "flight_id": list(range(1, 61)),
        "tail_number": tails,
        "dest_icao": icaos,
        "altitude_ft": [rng.randrange(2000, 41000, 100) for _ in range(60)],
        "speed_kt": [float(rng.randint(90, 520)) for _ in range(60)],
        "remarks": [None if i % 10 == 0 else
                    "Routine sector with no anomalies observed; crew reported smooth ride "
                    "and an on-time arrival at the destination gate after a standard descent "
                    f"profile and taxi-in, leg {i}." for i in range(60)],
    }


def test_profile_table_full_pass():
    tp = profile_table(_flights(), "src1", "flights")
    assert isinstance(tp, TableProfile)
    assert tp.source_id == "src1" and tp.table == "flights" and tp.row_count == 60
    assert set(tp.columns) == {"flight_id", "tail_number", "dest_icao",
                               "altitude_ft", "speed_kt", "remarks"}

    fid = tp.columns["flight_id"]
    assert fid.inferred_type is Datatype.INTEGER
    assert fid.null_count == 0
    assert fid.distinct_estimate == 60          # exact-fallback regime
    assert len(fid.quantiles) == 11
    assert fid.quantiles[0] == 1.0 and fid.quantiles[-1] == 60.0
    assert len(fid.minhash) == 64
    assert ("flight_id",) in tp.candidate_keys

    alt = tp.columns["altitude_ft"]
    assert alt.unit == "ft" and alt.dimension == LENGTH
    spd = tp.columns["speed_kt"]
    assert spd.unit == "kt" and spd.dimension == SPEED

    icao = tp.columns["dest_icao"]
    assert icao.semantic_type == "icao_code"
    assert icao.format_signature == "A{4}"

    rem = tp.columns["remarks"]
    assert rem.inferred_type is Datatype.TEXT
    assert rem.null_count == 6
    assert rem.token_stats and all(f > 0 for _, f in rem.token_stats)
    assert rem.quantiles == () and rem.format_signature == ""

    # key -> payload FDs surface at table level
    exact = {(f.lhs, f.rhs) for f in tp.fds if f.confidence == 1.0}
    assert (("flight_id",), "tail_number") in exact


def test_tail_number_semantics_and_signature():
    vals = ["N123AB", "N4567", "N89C", "N1", "N5523K"]
    tp = profile_table({"tail_number": vals}, "s", "t")
    cp = tp.columns["tail_number"]
    assert cp.semantic_type == "tail_number" and cp.semantic_confidence >= 0.6
    assert cp.format_signature == "A D{1,4} A{0,2}"  # digit runs span 1..4 in this sample
    assert all(s in vals for s in cp.sample_values)


def test_mixed_unit_column_never_silently_merged_in_profile():
    data = {"altitude": ["1200 ft", "3500 ft", "2200 ft", "800 m", "950 m", "600 m"]}
    tp, units = profile_table_detailed(data, "s", "t")
    cp = tp.columns["altitude"]
    assert cp.unit is None                       # contract-level: nothing asserted
    assert cp.dimension == LENGTH                # common dimension survives
    assert units["altitude"].mixed is True       # full signal via the detailed API
    assert {s for s, _ in units["altitude"].observed_units} >= {"ft", "m"}


def test_conflicted_unit_not_asserted_on_contract():
    data = {"distance_ft": ["12 km", "9 km", "15 km", "11 km"]}
    tp, units = profile_table_detailed(data, "s", "t")
    assert tp.columns["distance_ft"].unit is None
    assert units["distance_ft"].conflict is True and units["distance_ft"].unit == "km"


def test_pandas_and_pyarrow_inputs_agree():
    cols = make_orders()
    via_pd = profile_table(pd.DataFrame(cols), "s", "orders")
    via_pa = profile_table(pa.table(cols), "s", "orders")
    via_dict = profile_table(cols, "s", "orders")
    for name in cols:
        a, b, c = via_pd.columns[name], via_pa.columns[name], via_dict.columns[name]
        assert a.inferred_type == b.inferred_type == c.inferred_type
        assert a.null_count == b.null_count == c.null_count
        assert a.distinct_estimate == b.distinct_estimate == c.distinct_estimate
        assert a.minhash == b.minhash == c.minhash
    assert set(via_pd.fds) == set(via_pa.fds) == set(via_dict.fds)
    assert via_pd.candidate_keys == via_pa.candidate_keys


def test_nan_and_none_are_the_same_null():
    df = pd.DataFrame({"x": [1.0, float("nan"), 3.0, None]})
    tp = profile_table(df, "s", "t")
    assert tp.columns["x"].null_count == 2
    assert tp.columns["x"].distinct_estimate == 2


def test_profile_table_deterministic():
    li = make_lineitems()
    tp1 = profile_table(li, "s", "lineitems")
    tp2 = profile_table(li, "s", "lineitems")
    assert tp1 == tp2
    for name in li:
        assert tp1.columns[name].sketch_key() == tp2.columns[name].sketch_key()


# -------------------------------------------------------- append-mostly hook


def _base_rows(n: int, seed: int = 3) -> dict[str, list]:
    rng = random.Random(seed)
    return {
        "id": list(range(1, n + 1)),
        "category": [rng.choice(list("ABCDEFGH")) for _ in range(n)],
        "value": [f"v{rng.randrange(10**9)}-{i}" for i in range(n)],
    }


def test_append_mostly_true_on_pure_append():
    prev_data = _base_rows(1000)
    prev = profile_table(prev_data, "s", "t")
    rng = random.Random(4)
    cur_data = {
        "id": prev_data["id"] + list(range(1001, 1301)),
        "category": prev_data["category"] + [rng.choice(list("ABCDEFGH")) for _ in range(300)],
        "value": prev_data["value"] + [f"v{rng.randrange(10**9)}-{i}" for i in range(1000, 1300)],
    }
    cur = profile_table(cur_data, "s", "t", previous=prev)
    assert cur.append_mostly is True


def test_append_mostly_false_when_rows_do_not_grow():
    data = _base_rows(500)
    prev = profile_table(data, "s", "t")
    cur = profile_table(data, "s", "t", previous=prev)
    assert cur.append_mostly is False


def test_append_mostly_false_on_update_heavy_load():
    prev_data = _base_rows(1000)
    prev = profile_table(prev_data, "s", "t")
    rng = random.Random(9)
    # rewrite half the values in place (update-heavy), plus a small append
    new_vals = list(prev_data["value"])
    for i in range(0, 1000, 2):
        new_vals[i] = f"w{rng.randrange(10**9)}-{i}"
    cur_data = {
        "id": prev_data["id"] + list(range(1001, 1101)),
        "category": prev_data["category"] + [rng.choice(list("ABCDEFGH")) for _ in range(100)],
        "value": new_vals + [f"w{rng.randrange(10**9)}-{i}" for i in range(1000, 1100)],
    }
    cur = profile_table(cur_data, "s", "t", previous=prev)
    assert cur.append_mostly is False


def test_custom_append_detector_hook():
    data = _base_rows(50)
    prev = profile_table(data, "s", "t")
    grown = {k: v + v[:10] for k, v in data.items()}
    cur = profile_table(grown, "s", "t", previous=prev,
                        append_detector=lambda p, c: True)
    assert cur.append_mostly is True

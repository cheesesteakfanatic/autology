"""Perf gate — lexical pre-gate over the strptime date/datetime cascade.

The hot path the scout flagged: ``infer_datatype`` spends ~11.5s of its 17.5s
``profile_column`` cumtime inside the ``_parses_date`` / ``_parses_datetime``
``datetime.strptime`` cascade (the single largest leaf cost, 837k strptime calls
on Meridian). The optimization is a cheap, ZERO-FALSE-NEGATIVE regex pre-gate that
short-circuits the cascade for values whose character shape provably cannot match
any of the supported formats.

Two guarantees, both load-bearing:

* PARITY / BYTE-IDENTITY (always asserted, never skipped): ``_parses_date`` and
  ``_parses_datetime`` (the gated implementations) return EXACTLY the same boolean
  as a raw strptime cascade with the gate removed — verified exhaustively over a
  large structured + adversarial value corpus, plus property tests with Hypothesis.
  ``infer_datatype`` is asserted equal on a frozen fixture of representative columns.
* SPEEDUP (measured, soft): the gated path is meaningfully faster than the raw
  cascade on a realistic non-date-heavy column mix. Skipped (not failed) when the
  machine is too slow for a stable timing, so it never flakes; PARITY is asserted
  unconditionally.
"""

from __future__ import annotations

import datetime as _dt
import random
import string
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ontoforge.contracts import Datatype
from ontoforge.profiling import infer_datatype
from ontoforge.profiling.semantic_types import (
    _DATE_FORMATS,
    _DT_FORMATS,
    _parses_date,
    _parses_datetime,
)


# --------------------------------------------------- raw (un-gated) reference


def _raw_parses_date(s: str) -> bool:
    """The pre-optimization cascade, verbatim: strptime with NO pre-gate."""
    for fmt in _DATE_FORMATS:
        try:
            _dt.datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def _raw_parses_datetime(s: str) -> bool:
    for fmt in _DT_FORMATS:
        try:
            _dt.datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


# --------------------------------------------------------- value generators


def _structured_values(rng: random.Random, n: int) -> list[str]:
    """Date-shaped + near-miss + junk strings that exercise every format branch."""
    out: list[str] = []
    months = ["Jan", "feb", "MAR", "Apr", "may", "Jun", "Jul", "aug",
              "Sep", "Oct", "Nov", "Dec", "Xyz", "Foo"]
    seps = ["-", "/"]
    junk = string.ascii_letters + string.digits + " :+.TZ-/"
    for _ in range(n):
        r = rng.random()
        if r < 0.22:  # YMD / MDY numeric dates (valid + out-of-range)
            parts = [str(rng.randint(0, 9999)).zfill(rng.choice([1, 2, 4]))
                     for _ in range(3)]
            out.append(rng.choice(seps).join(parts))
        elif r < 0.34:  # compact %Y%m%d and bare digit runs
            out.append(str(rng.randint(0, 99_999_999)))
        elif r < 0.46:  # d-Mon-Y (real + bogus month token)
            out.append(f"{rng.randint(1, 31)}-{rng.choice(months)}-{rng.randint(1, 2099)}")
        elif r < 0.72:  # full datetimes with assorted time/tz shapes
            d = f"{rng.randint(0, 9999)}-{rng.randint(0, 99)}-{rng.randint(0, 99)}"
            tail = rng.choice(
                ["", " 10:00", " 10:00:00", "T10:00:00", "T10:00:00Z",
                 "T10:00:00+0000", "T10:00:00+00:00", "T25:99:99", " 10:00:00.5"]
            )
            out.append(d + tail)
        else:  # arbitrary junk (alpha codes, ICAO, narrative tokens, symbols)
            k = rng.randint(0, 16)
            out.append("".join(rng.choice(junk) for _ in range(k)))
    return out


# ------------------------------------------------------------ PARITY (hard)


def test_pre_gate_is_zero_false_negative_exhaustive():
    """The gate NEVER skips a value the raw strptime cascade would accept.

    Hard requirement: gated result == raw cascade result for every value. A single
    divergence means the pre-gate changed a column's inferred datatype, which feeds
    FD/IND/relationship/semantic logic — a parity failure, not a perf regression.
    """
    rng = random.Random(0)
    values = _structured_values(rng, 300_000)
    date_fn, date_ref = 0, 0
    for s in values:
        g = _parses_date(s)
        r = _raw_parses_date(s)
        assert g == r, f"_parses_date parity broke on {s!r}: gated={g} raw={r}"
        date_fn += g
        date_ref += r
        gt = _parses_datetime(s)
        rt = _raw_parses_datetime(s)
        assert gt == rt, f"_parses_datetime parity broke on {s!r}: gated={gt} raw={rt}"
    # sanity: the corpus actually contains parsable dates (else the test is vacuous)
    assert date_ref > 0 and date_fn == date_ref


@settings(derandomize=True, max_examples=2000, deadline=None)
@given(st.text(max_size=24))
def test_pre_gate_parity_property(s):
    """Property: on ANY string, gated parsers agree with the raw cascade."""
    assert _parses_date(s) == _raw_parses_date(s)
    assert _parses_datetime(s) == _raw_parses_datetime(s)


@settings(derandomize=True, max_examples=2000, deadline=None)
@given(
    st.text(alphabet="0123456789-/: TZ+abcJanFeMrApyulgSpOctNvD", max_size=24),
)
def test_pre_gate_parity_property_dateish_alphabet(s):
    """Same property biased toward the date alphabet (where gate near-misses live)."""
    assert _parses_date(s) == _raw_parses_date(s)
    assert _parses_datetime(s) == _raw_parses_datetime(s)


def test_infer_datatype_parity_on_representative_fixture():
    """``infer_datatype`` output is byte-identical with the pre-gate vs the raw path.

    We rebuild the raw verdict by monkeypatching the module's gated predicates back
    to the un-gated cascade and asserting the Datatype matches on a frozen fixture of
    columns that span every branch (date, datetime, int, float, codes, text, mixed).
    """
    import ontoforge.profiling.semantic_types as stmod

    fixture: dict[str, tuple[list, Datatype]] = {
        "iso_date": (["2024-01-15", "2023-12-09", "2022-06-30"], Datatype.DATE),
        "slash_date": (["2024/01/15", "2023/12/09"], Datatype.DATE),
        "mdy_date": (["01/15/2024", "12/09/2023"], Datatype.DATE),
        "dmon_date": (["01-Jan-2024", "15-Mar-2023"], Datatype.DATE),
        # NB: bare digit runs pass the INTEGER check (which precedes the date check
        # in infer_datatype), so a %Y%m%d-shaped column reads as INTEGER. This is the
        # EXISTING engine behavior and is identical on both code paths — the point of
        # this fixture is that the pre-gate does not change it.
        "compact_date": (["20240115", "20231209"], Datatype.INTEGER),
        "iso_dt": (["2024-01-15T10:00:00", "2023-12-09 08:30:00"], Datatype.DATETIME),
        "tz_dt": (["2024-01-15T10:00:00+0000", "2023-12-09T08:30:00+00:00"], Datatype.DATETIME),
        "ints": (["1", "2", "300", "4567"], Datatype.INTEGER),
        "zero_padded": (["00123", "00456", "07890"], Datatype.STRING),
        "floats": (["1.5", "2.25", "3.0"], Datatype.FLOAT),
        "icao": (["KJFK", "KLAX", "EGLL"], Datatype.STRING),
        "codes": (["A123", "B456", "C789"], Datatype.STRING),
        "tails": (["N123AB", "N4567", "N89C"], Datatype.STRING),
        "narrative": (["the aircraft returned to the field"] * 3, Datatype.STRING),
        # dirty cells under the 97% threshold must not flip the type either way
        "dirty_date": (["2024-01-15"] * 99 + ["n/a"], Datatype.DATE),
        # numeric-shaped but NOT a valid date (separator-structure passes gate, strptime rejects)
        "bad_date_shape": (["13-99-9999", "00-00-0000"], Datatype.STRING),
    }

    gated = {name: infer_datatype(vals) for name, (vals, _) in fixture.items()}

    # frozen expected verdicts (independent of either code path)
    for name, (_vals, expected) in fixture.items():
        assert gated[name] is expected, f"{name}: {gated[name]} != {expected}"

    # now swap the gated predicates for the raw cascade and assert IDENTICAL output
    orig_d, orig_t = stmod._parses_date, stmod._parses_datetime
    try:
        stmod._parses_date = _raw_parses_date
        stmod._parses_datetime = _raw_parses_datetime
        raw = {name: infer_datatype(vals) for name, (vals, _) in fixture.items()}
    finally:
        stmod._parses_date, stmod._parses_datetime = orig_d, orig_t

    assert raw == gated  # byte-identical datatype verdicts across the two paths


# ------------------------------------------------------------- SPEEDUP (soft)


def _bench_cascade(parse_date, parse_dt, cols: list[list[str]], repeat: int) -> float:
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        for vals in cols:
            d = sum(1 for v in vals if parse_date(v)) / len(vals)
            if d < 0.97:
                _ = sum(1 for v in vals if parse_dt(v)) / len(vals)
        best = min(best, time.perf_counter() - t0)
    return best


def test_pre_gate_is_faster_on_non_date_columns():
    """Soft perf gate: the pre-gate beats the raw strptime cascade on a realistic mix.

    Mirrors the Meridian profile (most STRING columns are NOT dates: alpha codes,
    ICAO, ints, narrative). The gate rejects them before strptime, so it should be
    several x faster. Generous 1.5x threshold so it never flakes; skipped if the
    machine is too slow to time stably. PARITY is covered above, unconditionally.
    """
    rng = random.Random(7)

    def col(kind: str) -> list[str]:
        if kind == "date":
            return [f"{rng.randint(2000, 2024)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
                    for _ in range(1000)]
        if kind == "int":
            return [str(rng.randint(1, 999_999)) for _ in range(1000)]
        if kind == "code":
            return [rng.choice("ABCDEFGH") + str(rng.randint(100, 999)) for _ in range(1000)]
        if kind == "icao":
            return [rng.choice(["KJFK", "KLAX", "EGLL", "KSFO"]) for _ in range(1000)]
        return [rng.choice(["open ticket pending review", "closed and resolved",
                            "escalated to operations"]) for _ in range(1000)]

    kinds = (["date"] * 20 + ["int"] * 40 + ["code"] * 30
             + ["icao"] * 20 + ["text"] * 28)  # 118/138 non-date STRING, date-light
    cols = [col(k) for k in kinds]

    raw_s = _bench_cascade(_raw_parses_date, _raw_parses_datetime, cols, repeat=3)
    gate_s = _bench_cascade(_parses_date, _parses_datetime, cols, repeat=3)

    if raw_s < 0.05:  # too fast to time reliably on this box
        pytest.skip(f"raw cascade too fast to time stably ({raw_s:.4f}s)")

    speedup = raw_s / gate_s if gate_s > 0 else float("inf")
    assert speedup >= 1.5, (
        f"pre-gate not meaningfully faster: raw={raw_s:.4f}s gate={gate_s:.4f}s "
        f"({speedup:.2f}x)"
    )

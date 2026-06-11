"""Record extraction tests (M5 step 1): field maps, normalization, and the
temporal-reuse separability invariant."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from ontoforge.er.records import (
    EntityMention,
    norm_name,
    norm_serial,
    norm_tail,
    norm_text,
    parse_date_ordinal,
)


class TestNormalizers:
    def test_norm_text(self):
        assert norm_text("  united   air lines inc  ") == "UNITED AIR LINES INC"
        assert norm_text(None) == ""

    def test_norm_tail_leading_n_variants(self):
        # registry stores N-less; narrative/NTSB/ERP store with leading N
        assert norm_tail("N3484Z") == "3484Z"
        assert norm_tail("3484Z") == "3484Z"
        assert norm_tail(" n44304 ") == "44304"
        assert norm_tail("N-123AB") == "123AB"
        # a leading N NOT followed by a digit is part of the identifier
        assert norm_tail("NX211") == "NX211"

    def test_norm_serial(self):
        assert norm_serial("28-1974519") == "281974519"
        assert norm_serial("1617                          ") == "1617"
        assert norm_serial("") == ""

    def test_norm_name_punctuation(self):
        assert norm_name("DELTA AIR LINES, INC.") == "DELTA AIR LINES INC"
        assert norm_name("U.S. Department of the Interior") == "US DEPARTMENT OF THE INTERIOR"
        assert norm_name("AmeriFlight L.L.C.") == "AMERIFLIGHT LLC"
        assert norm_name("Wells Fargo Trust Co., N.A., Trustee") == "WELLS FARGO TRUST CO NA TRUSTEE"

    def test_parse_date_ordinal(self):
        assert parse_date_ordinal("20081012") == date(2008, 10, 12).toordinal()
        assert parse_date_ordinal("1982-10-17") == date(1982, 10, 17).toordinal()
        assert parse_date_ordinal("202404") == date(2024, 4, 15).toordinal()  # ASRS month
        assert parse_date_ordinal("") is None
        assert parse_date_ordinal("99999999") is None


class TestExtraction:
    def test_counts_and_kinds(self, estate, mentions):
        kinds = defaultdict(int)
        for m in mentions:
            kinds[m.entity_kind] += 1
        n_rows = sum(
            len(estate["tables"][t])
            for t in ("faa_master", "asrs_reports", "ntsb_events", "maintenance_erp")
        )
        # one aircraft mention per row of the four entity-bearing tables
        assert kinds["aircraft"] == n_rows
        # operator mentions only where the name field is non-blank
        assert 0 < kinds["operator"] <= n_rows

    def test_mention_ids_unique_and_stable(self, mentions):
        ids = [m.mention_id for m in mentions]
        assert len(ids) == len(set(ids))
        assert all(m.mention_id == f"{m.entity_kind}/{m.table}/{m.row_key}" for m in mentions)

    def test_asrs_tail_extracted_from_narrative(self, mentions):
        asrs = [m for m in mentions if m.entity_kind == "aircraft" and m.table == "asrs_reports"]
        with_tail = [m for m in asrs if m.fields["tail"]]
        # the fixture embeds 'tail number Nxxxxx' in 210 of 350 narratives
        assert len(with_tail) >= 200
        # extracted tails are normalized to the registry's N-less form:
        # they start with a digit, never with the 'N' prefix
        assert all(m.fields["tail"][0].isdigit() for m in with_tail)

    def test_registry_mentions_carry_validity_window(self, mentions):
        faa = [m for m in mentions if m.entity_kind == "aircraft" and m.table == "faa_master"]
        assert all(m.fields["is_registry"] == "1" for m in faa)
        windowed = [m for m in faa if m.fields["date_lo"] is not None and m.fields["date_hi"] is not None]
        assert len(windowed) / len(faa) > 0.95

    def test_temporal_reuse_trap_stays_separable(self, mentions):
        """Same tail, different serial: normalization must NOT merge them —
        the reused-tail registry rows remain distinct mentions with distinct
        serials and disjoint validity windows (the trap decided downstream)."""
        faa = [m for m in mentions if m.entity_kind == "aircraft" and m.table == "faa_master"]
        by_tail = defaultdict(list)
        for m in faa:
            by_tail[m.fields["tail"]].append(m)
        dups = {t: ms for t, ms in by_tail.items() if len(ms) > 1}
        assert len(dups) >= 5, "fixture documents reused N-numbers"
        for tail, ms in dups.items():
            serials = {m.fields["serial"] for m in ms}
            assert len(serials) == len(ms), f"tail {tail}: serials must stay distinct"
            ids = {m.mention_id for m in ms}
            assert len(ids) == len(ms)
            # validity windows are disjoint (the temporal trap structure)
            spans = sorted((m.fields["date_lo"], m.fields["date_hi"]) for m in ms)
            for (lo1, hi1), (lo2, hi2) in zip(spans, spans[1:]):
                assert hi1 < lo2, f"tail {tail}: expected disjoint registration windows"

    def test_operator_mentions_skip_blank_names(self, mentions):
        ops = [m for m in mentions if m.entity_kind == "operator"]
        assert all(m.fields["name_norm"] for m in ops)

    def test_mention_dataclass_shape(self, mentions):
        m = mentions[0]
        assert isinstance(m, EntityMention)
        assert m.entity_kind in ("aircraft", "operator")
        assert isinstance(m.fields, dict)
        assert m.source_id and m.table and m.row_key

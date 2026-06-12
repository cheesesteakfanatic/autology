"""Messy-data wart presence, rates, and recoverability.

Every wart class from the corpus spec must actually be present (a clean corpus
makes the demo unconvincing) at a bounded rate (too dirty makes gold questions
silently unanswerable), and hero rows referenced by gold questions stay clean.
"""

from __future__ import annotations

import re

from meridian_helpers import pad_vendor

HERO_SUPPLIER = "Hailong Precision Industry Co., Ltd."


def test_vendor_id_zero_strip_wart(frames):
    po = frames["purchase_order_lines"]
    stripped = po[~po["VENDOR_ID"].str.startswith("0")]
    rate = len(stripped) / len(po)
    assert 0.02 <= rate <= 0.15, f"zero-strip rate {rate:.2%}"
    # deterministic recovery: every stripped id zero-pads to a contracted supplier
    contracted = set(frames["supplier_contracts"]["SUPPLIER_ID"])
    assert all(pad_vendor(v) in contracted for v in stripped["VENDOR_ID"])
    # hero rows are never stripped
    assert (po.loc[po["SUPPLIER_NAME"] == HERO_SUPPLIER, "VENDOR_ID"].str.startswith("0")).all()


def test_supplier_name_variants(frames):
    po = frames["purchase_order_lines"]
    legal = set(frames["supplier_contracts"]["SUPPLIER_LEGAL_NAME"])
    variants = po[~po["SUPPLIER_NAME"].isin(legal)]
    assert 0.03 <= len(variants) / len(po) <= 0.20
    # quality is name-only with wilder forms: several distinct spellings beyond
    # the legal names, including truncations and site tags
    qn_names = set(frames["quality_notifications"]["SUPPLIER"])
    wild = {n for n in qn_names if n not in legal}
    assert len(wild) >= 30
    assert any("(" in n for n in wild)           # '(Shenzhen)'-style site tags
    assert any(n != n.strip() or "  " in n for n in qn_names)  # whitespace warts
    # hero PO rows carry the exact uniform legal name
    assert (po["SUPPLIER_NAME"] == HERO_SUPPLIER).sum() >= 10


def test_weight_unit_mix(frames):
    w = frames["shipments"]["GROSS_WEIGHT"].str.lower()
    assert w.str.endswith("kg").any() and w.str.endswith("lb").any()
    uom = frames["shipments"]["WEIGHT_UOM"]
    assert ((uom == "KG") == w.str.endswith("kg")).all()


def test_area_unit_mix_with_blank_uom(frames):
    uom = frames["leases"]["AREA_UOM"]
    assert (uom == "SF").any() and (uom == "SQM").any()
    blank = (uom == "").mean()
    assert 0.01 <= blank <= 0.12   # recoverable by country convention


def test_currency_format_warts(frames):
    pos = frames["retail_pos_sales"]["NET_SALES_AMOUNT"]
    assert pos.str.match(r"\d{1,3}(\.\d{3})+,\d{2}$").any(), "EU decimal-comma missing"
    assert pos.str.startswith("US$").any(), "US$ prefix missing"
    usd = frames["purchase_order_lines"]["NET_VALUE_USD"]
    prefix_rate = usd.str.startswith("USD ").mean()
    assert 0.15 <= prefix_rate <= 0.45
    # the bridge stays computable: every value parses after prefix/comma strip
    assert usd.str.replace("USD ", "", regex=False).str.replace(",", "", regex=False).astype(float).notna().all()


def test_date_locale_warts(frames):
    commence = frames["leases"]["LEASE_COMMENCEMENT_DATE"]
    assert commence.str.match(r"\d{2}\.\d{2}\.\d{4}$").any(), "DD.MM.YYYY missing"
    assert commence.str.match(r"\d{4}-\d{2}-\d{2}$").any(), "ISO commencements missing"
    # the gold-filtered expiration column stays ISO (documented convention)
    assert frames["leases"]["EXPIRATION_DATE"].str.match(r"\d{4}-\d{2}-\d{2}$").all()
    assert frames["shipments"]["SHIP_DATE"].str.match(r"\d{2}-[A-Z]{3}-\d{2}$").all()
    ts = frames["support_tickets"]["CREATED_TS"]
    assert ts.str.endswith("Z").any() and ts.str.contains(re.escape("+08:00")).any()


def test_null_token_zoo(frames):
    assert (frames["site_headcount"]["CONTRACTOR_COUNT"] == "-").any()
    assert (frames["support_tickets"]["RESOLVED_DATE"] == "NULL").any()
    assert (frames["support_tickets"]["RESOLVED_DATE"] == "N/A").any()
    assert (frames["supplier_contracts"]["INCOTERMS_NAMED_PLACE"] == "TBD").any()
    assert (frames["products"]["END_OF_SALE_DATE"] == "9999-12-31").any()
    assert (frames["bom_components"]["EFFECTIVE_TO_DATE"] == "9999-12-31").any()
    assert (frames["products"]["STORAGE_GB"] == "N/A").any()
    assert (frames["products"]["STORAGE_GB"] == "").any()


def test_double_entered_rows(frames):
    # keyless snapshot tables carry exact duplicate rows
    for name in ("retail_pos_sales", "site_headcount"):
        df = frames[name]
        assert df.duplicated().sum() >= 3, name
    # keyed event tables carry re-keyed double entries (identical but for id)
    for name, key in (("quality_notifications", "NOTIFICATION_ID"), ("shipments", "SHIPMENT_ID")):
        df = frames[name]
        assert not df[key].duplicated().any()
        body = df.drop(columns=[key])
        assert body.duplicated().sum() >= 5, name


def test_near_duplicate_quality_notifications(frames):
    qn = frames["quality_notifications"]
    assert qn["DEFECT_DESCRIPTION"].str.startswith("rework review:").sum() >= 3


def test_single_mojibake_cell(frames):
    mangled = frames["leases"]["CITY"].str.contains("Ã", regex=False)
    assert mangled.any()
    assert set(frames["leases"].loc[mangled, "CITY"]) == {"ZÃ¼rich"}
    for name, df in frames.items():
        if name == "leases":
            continue
        for col in df.columns:
            assert not df[col].str.contains("Ã", regex=False).any(), f"{name}.{col}"


def test_dangling_shipment_po_references(frames):
    ships = frames["shipments"]
    real = set(frames["purchase_order_lines"]["PO_NUMBER"])
    refs = ships["PO_NUMBER"]
    dangling = refs[(refs != "") & (~refs.isin(real))]
    assert 0.005 <= len(dangling) / len(ships) <= 0.05
    assert (refs == "").any()                      # inter-DC transfers
    assert (refs.isin(real)).mean() >= 0.5         # but most legs join


def test_kpc_price_unit_trap_on_the_trick_part(frames):
    po = frames["purchase_order_lines"]
    dsp = po[po["MATERIAL_NUMBER"] == "CMP-DSP-0451"]
    assert {"EA", "KPC"} <= set(dsp["ORDER_UOM"])
    assert dsp["PRICE_UNIT"].nunique() >= 2
    assert dsp["CURRENCY"].nunique() >= 1
    assert (dsp["PO_DATE"] >= "2026-01-01").any()


def test_incoterm_conflicts_between_po_and_shipment(frames):
    po = frames["purchase_order_lines"]
    ships = frames["shipments"]
    po_inco = dict(zip(po["PO_NUMBER"], po["INCOTERMS"]))
    joined = ships[ships["PO_NUMBER"].isin(po_inco)]
    conflicts = sum(po_inco[r.PO_NUMBER] != r.INCOTERMS for r in joined.itertuples())
    assert 0.01 <= conflicts / len(joined) <= 0.99  # the system-of-record tension exists


def test_unanswerables_have_no_leaked_proxy_columns(frames):
    """Adversarial audit: no ownership-ish column for MQ-11, no customer
    registry for MQ-10."""
    for name, df in frames.items():
        for col in df.columns:
            low = col.lower()
            assert "owner" not in low and "book_value" not in low, f"{name}.{col}"
            assert "churn" not in low and "customer_id" not in low, f"{name}.{col}"

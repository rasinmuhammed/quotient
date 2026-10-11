import csv
from dataclasses import replace
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP
from decimal import Decimal as D

import pytest

from quotient.config import ROOT
from quotient.domain import FlagCode, PricingRequest, Severity
from quotient.pricing.engine import RULES_USED, PricingEngine, quote_total
from quotient.pricing.rules import RuleSet
from quotient.pricing.units import pack_size_in_uom

TODAY = date(2026, 10, 12)


def req(sku: str, qty: str, **kw) -> PricingRequest:
    base = dict(incoterm="EXW", quote_date=TODAY)
    return PricingRequest(sku=sku, quantity=D(qty), **{**base, **kw})


def codes(line) -> set[FlagCode]:
    return {f.code for f in line.flags}


def rows(name: str) -> list[dict]:
    with open(ROOT / "data" / f"{name}.csv", newline="") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------------------------- an independent oracle


def test_a_line_recomputed_by_hand_from_the_raw_csv_rows(engine: PricingEngine):
    """10 t of technical IPA, CIF Hamburg, for a tier-B German customer quoting in EUR, recomputed without the engine."""
    product = next(r for r in rows("products") if r["sku"] == "QX-IPA-TG")
    customer = next(r for r in rows("customers") if r["name"] == "Elbtal Lackfabrik GmbH")
    density = D(product["density_kg_per_l"])
    ibc_kg = D(1000) * density  # 786 kg in a 1000 L IBC
    n = (D(10000) / ibc_kg).to_integral_value(rounding=ROUND_CEILING)  # 13 IBCs
    quoted = n * ibc_kg
    tier = next(
        r
        for r in rows("price_list")
        if r["sku"] == "QX-IPA-TG"
        and r["pack_code"] == "IBC1000"
        and D(r["min_qty"]) <= quoted
        and (not r["max_qty"] or quoted < D(r["max_qty"]))
    )
    fr = next(
        r
        for r in rows("freight_rates")
        if r["origin_port"] == product["origin_port"]
        and r["destination_country_code"] == "DE"
        and r["incoterm"] == "CIF"
    )
    eur = D(next(r for r in rows("fx_rates") if r["quote"] == "EUR")["rate"])
    c4 = D("0.0001")

    list_price = D(tier["unit_price"])
    discount = (list_price * D({"A": 6, "B": 4, "C": 2, "D": 0}[customer["discount_tier"]]) / 100).quantize(
        c4, ROUND_HALF_UP
    )
    net = list_price - discount
    assert (net - D(product["standard_cost_usd"])) / net >= D("0.12")  # this line does not touch the floor
    freight = D(fr["rate_usd_per_kg"]).quantize(c4)
    dg = D("0.035")
    insurance = ((net + freight + dg) * D("1.10") * D("0.0015")).quantize(c4, ROUND_HALF_UP)
    unit = ((net + freight + dg + insurance) * eur).quantize(D("0.001"), ROUND_HALF_UP)
    total = (unit * quoted).quantize(D("0.01"), ROUND_HALF_UP)

    line = engine.price(
        req("QX-IPA-TG", "10000", customer_id=customer["customer_id"], incoterm="CIF", destination_country_code="DE")
    )
    assert (line.pack_code, line.packs, line.quoted_qty) == ("IBC1000", int(n), quoted)
    assert (line.currency, line.unit_price, line.line_total) == ("EUR", unit, total)
    assert line.price_row_id == tier["price_row_id"] and line.freight_row_id == fr["freight_row_id"]


# --------------------------------------------------------------------------------------------- invariants over everything


INCOTERMS = ("EXW", "FOB", "CFR", "CIF", "DAP")


def test_every_sku_pack_tier_and_incoterm_prices_and_reconciles(engine: PricingEngine, catalog):
    """Each SKU, each offered pack, quantities on both sides of every tier boundary, every supported incoterm."""
    checked = 0
    for p in catalog.products.values():
        for code in p.pack_codes:
            size = pack_size_in_uom(p, catalog.packs[code])
            tiers = catalog.price_rows[(p.sku, code)]
            for t in tiers:
                for qty in {max(t.min_qty, size), t.min_qty + size * 3}:
                    for inc, cur, dest in zip(
                        INCOTERMS, ("USD", "EUR", "JPY", "BRL", "AED"), ("US", "DE", "JP", "BR", "AE"), strict=True
                    ):
                        line = engine.price(
                            req(
                                p.sku,
                                str(qty),
                                pack_code=code,
                                customer_id="C-005",
                                incoterm=inc,
                                destination_country_code=dest,
                                currency=cur,
                            )
                        )
                        assert line.priced, (p.sku, code, qty, inc, line.flags)
                        parts = (line.net_unit_usd, line.freight_unit_usd, line.dg_unit_usd, line.insurance_unit_usd)
                        assert sum(parts) == line.delivered_unit_usd
                        assert line.list_unit_usd - line.discount_unit_usd <= line.net_unit_usd
                        assert line.quoted_qty >= line.requested_qty and line.quoted_qty == size * line.packs
                        band = next(r for r in tiers if r.price_row_id == line.price_row_id)
                        assert band.min_qty <= line.quoted_qty and (
                            band.max_qty is None or line.quoted_qty < band.max_qty
                        )
                        dp = 1 if cur == "JPY" else 3
                        assert line.unit_price == (line.delivered_unit_usd * line.fx_rate).quantize(
                            D(1).scaleb(-dp), ROUND_HALF_UP
                        )
                        assert line.line_total == (line.unit_price * line.quoted_qty).quantize(
                            D(1) if cur == "JPY" else D("0.01"), ROUND_HALF_UP
                        )
                        assert (line.freight_row_id is None) == (inc == "EXW")
                        assert set(line.rules_applied) <= set(RULES_USED)
                        assert all(s.rule_id in line.rules_applied for s in line.steps)
                        checked += 1
    assert checked > 2000


# --------------------------------------------------------------------------------------------- packs and quantities


def test_default_pack_prefers_the_largest_that_does_not_overshoot(engine):
    assert engine.price(req("QX-TOL-AR", "300")).pack_code == "CAN25"  # 12 cans exactly, not 2 drums (+33%)
    assert engine.price(req("QX-IPA-TG", "10000")).pack_code == "IBC1000"  # 13 IBCs, +2.2%
    assert engine.price(req("QX-TIO2-TG", "4000")).pack_code == "FIBC1000"


def test_quantity_is_rounded_up_to_whole_packs_and_says_so(engine):
    line = engine.price(req("QX-IPA-TG", "1000"))
    assert (line.packs, line.quoted_qty) == (7, D("1100.4"))  # 7 drums of 157.2 kg
    assert any(s.rule_id == "PACK-ROUNDUP-v1" and "requested 1,000 kg" in s.label for s in line.steps)


def test_a_pack_the_sku_is_not_offered_in_is_flagged_not_swapped_silently(engine):
    line = engine.price(req("QX-TIO2-TG", "2000", pack_code="IBC1000"))
    assert FlagCode.PACK_UNAVAILABLE in codes(line) and line.pack_code in ("BAG25", "FIBC1000") and line.blocked


def test_below_moq_is_priced_as_asked_and_blocked(engine, catalog):
    p = catalog.product("QX-IPA-TG")
    line = engine.price(req("QX-IPA-TG", "150"))
    assert line.quoted_qty < p.moq and line.priced
    assert FlagCode.BELOW_MOQ in codes(line) and line.blocked
    assert line.quoted_qty == D("157.2")  # one drum, not raised to the MOQ


# --------------------------------------------------------------------------------------------- price rows


def test_an_expired_price_row_is_shown_but_blocks_the_line(engine, catalog):
    expired = next(rs for rs in catalog.price_rows.values() if rs[0].valid_to < TODAY)
    sku, pack = expired[0].sku, expired[0].pack_code
    line = engine.price(req(sku, "1000", pack_code=pack))
    assert line.priced and FlagCode.PRICE_EXPIRED in codes(line) and line.blocked
    assert line.price_row_id in {r.price_row_id for r in expired}


def test_tier_boundaries_are_min_inclusive_max_exclusive(engine, catalog):
    rs = catalog.price_rows[("QX-TIO2-TG", "BAG25")]
    assert engine.price(req("QX-TIO2-TG", "975", pack_code="BAG25")).price_tier == 1  # 39 bags = 975 kg
    assert engine.price(req("QX-TIO2-TG", "1000", pack_code="BAG25")).price_row_id == rs[1].price_row_id
    assert engine.price(req("QX-TIO2-TG", "20000", pack_code="BAG25")).price_tier == 4


# --------------------------------------------------------------------------------------------- discount and margin floor


def test_discount_follows_the_customer_tier(engine, catalog):
    for cust in catalog.customers.values():
        line = engine.price(req("QX-MEOH-TG", "2000", customer_id=cust.customer_id, currency="USD"))
        pct = {"A": 6, "B": 4, "C": 2, "D": 0}[cust.discount_tier]
        assert line.discount_pct == pct
        assert line.discount_unit_usd == (line.list_unit_usd * pct / 100).quantize(D("0.0001"), ROUND_HALF_UP)


def test_unknown_customer_pays_list_and_is_flagged(engine):
    line = engine.price(req("QX-MEOH-TG", "2000", customer_id="C-999"))
    assert line.discount_unit_usd == 0 and FlagCode.CUSTOMER_UNKNOWN in codes(line)
    assert FlagCode.CUSTOMER_UNKNOWN not in codes(engine.price(req("QX-MEOH-TG", "2000")))


def test_margin_floor_raises_the_net_price_and_flags_it(engine, catalog):
    tier_a = next(c for c in catalog.customers.values() if c.discount_tier == "A")
    hits = []
    for p in catalog.products.values():
        line = engine.price(req(p.sku, "25000", customer_id=tier_a.customer_id, currency="USD"))
        if FlagCode.MARGIN_FLOOR in codes(line):
            hits.append((p, line))
    assert hits, "the generated data should contain low-markup SKUs that hit the floor on big orders"
    for p, line in hits:
        floor_price = (p.standard_cost_usd / D("0.88")).quantize(D("0.0001"), ROUND_CEILING)
        assert line.net_unit_usd == floor_price
        assert (line.net_unit_usd - p.standard_cost_usd) / line.net_unit_usd >= D("0.12")
        assert next(f for f in line.flags if f.code == FlagCode.MARGIN_FLOOR).severity == Severity.WARN


# --------------------------------------------------------------------------------------------- freight and incoterms


def test_dangerous_goods_pay_a_surcharge_only_on_main_carriage(engine):
    for inc, dest in (("EXW", None), ("FOB", None), ("CFR", "US"), ("CIF", "US"), ("DAP", "US")):
        hazmat = engine.price(req("QX-ACE-TG", "2000", incoterm=inc, destination_country_code=dest))
        safe = engine.price(req("QX-MPG-TG", "2000", incoterm=inc, destination_country_code=dest))
        assert (hazmat.dg_unit_usd > 0) == (inc in ("CFR", "CIF", "DAP"))
        assert safe.dg_unit_usd == 0
        assert FlagCode.HAZMAT in codes(hazmat) and FlagCode.HAZMAT not in codes(safe)


def test_insurance_only_under_cif(engine):
    for inc in ("CFR", "CIF", "DAP"):
        line = engine.price(req("QX-MPG-TG", "2000", incoterm=inc, destination_country_code="NL"))
        assert (line.insurance_unit_usd > 0) == (inc == "CIF")


def test_fob_needs_no_destination(engine):
    line = engine.price(req("QX-MPG-TG", "2000", incoterm="FOB"))
    assert line.priced and line.freight_row_id and line.transit_days == 0


def test_an_unmodelled_incoterm_leaves_the_line_unpriced_and_blocked(engine):
    line = engine.price(req("QX-MPG-TG", "2000", incoterm="DDP", destination_country_code="US"))
    assert not line.priced and line.net_unit_usd is not None and FlagCode.INCOTERM_UNSUPPORTED in codes(line)


def test_a_destination_without_a_lane_leaves_the_line_unpriced_and_blocked(engine):
    line = engine.price(req("QX-MPG-TG", "2000", incoterm="CIF", destination_country_code="ZA"))
    assert not line.priced and FlagCode.NO_FREIGHT_LANE in codes(line) and line.blocked


# --------------------------------------------------------------------------------------------- currency and totals


def test_yen_prices_have_one_decimal_and_whole_totals(engine):
    line = engine.price(req("QX-MPG-TG", "2000", currency="JPY"))
    assert (
        line.unit_price == line.unit_price.quantize(D("0.1")) and line.line_total == line.line_total.to_integral_value()
    )
    assert any(s.source == line.fx_row_id for s in line.steps)


def test_quote_total_is_the_sum_of_line_totals_and_refuses_mixed_currencies(engine):
    a = engine.price(req("QX-MPG-TG", "2000", currency="EUR"))
    b = engine.price(req("QX-GLY-TG", "1000", currency="EUR"))
    assert quote_total([a, b]) == a.line_total + b.line_total
    with pytest.raises(ValueError):
        quote_total([a, engine.price(req("QX-GLY-TG", "1000", currency="USD"))])


def test_the_engine_refuses_a_rule_set_missing_a_rule_it_uses(catalog, rules):
    broken = replace(rules, rules={k: v for k, v in rules.rules.items() if k != "MARGIN-FLOOR-v1"})
    with pytest.raises(ValueError, match="MARGIN-FLOOR-v1"):
        PricingEngine(catalog, broken)


def test_rules_file_documents_every_rule_the_engine_cites(rules: RuleSet):
    rules.require(*RULES_USED, "UNIT-CONV-v1")
    assert all(r.title and r.text for r in rules.rules.values())


def test_the_default_pack_avoids_a_lapsed_price_when_a_valid_one_exists(engine, catalog):
    # USP sodium bicarbonate: the fibre-drum price lapsed, the bag price is current; both packs hold 25 kg
    assert catalog.price_rows[("QX-NAHCO3-USP", "FDRUM25")][0].valid_to < TODAY
    line = engine.price(req("QX-NAHCO3-USP", "2000"))
    assert line.pack_code == "BAG25" and FlagCode.PRICE_EXPIRED not in codes(line)
    asked = engine.price(req("QX-NAHCO3-USP", "2000", pack_code="FDRUM25"))  # asked for explicitly: priced, flagged
    assert asked.pack_code == "FDRUM25" and FlagCode.PRICE_EXPIRED in codes(asked)

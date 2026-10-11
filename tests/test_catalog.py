import json
from decimal import Decimal
from itertools import pairwise

from quotient.catalog import Catalog
from quotient.config import ROOT


def _cas_ok(cas: str) -> bool:
    a, b, check = cas.split("-")
    body = a + b
    return sum((i + 1) * int(d) for i, d in enumerate(reversed(body))) % 10 == int(check)


def test_catalog_matches_the_generator_manifest(catalog: Catalog):
    files = json.loads((ROOT / "data" / "manifest.json").read_text())["files"]
    assert len(catalog.products) == files["products.csv"]["rows"]
    assert sum(len(v) for v in catalog.price_rows.values()) == files["price_list.csv"]["rows"]
    assert len(catalog.customers) == files["customers.csv"]["rows"]
    assert len(catalog.freight) == files["freight_rates.csv"]["rows"]


def test_every_cas_number_has_a_valid_check_digit(catalog: Catalog):
    assert all(_cas_ok(p.cas) for p in catalog.products.values())


def test_names_synonyms_and_trade_names_point_at_one_chemical(catalog: Catalog):
    owner: dict[str, str] = {}
    for p in catalog.products.values():
        for name in (p.chemical_name, *p.synonyms, *p.trade_names):
            key = " ".join(name.lower().split())
            assert owner.setdefault(key, p.chemical_code) == p.chemical_code, f"{name} is ambiguous"


def test_price_tiers_are_contiguous_and_cover_every_quantity(catalog: Catalog):
    for rows in catalog.price_rows.values():
        assert rows[0].min_qty == 0 and rows[-1].max_qty is None
        for lower, upper in pairwise(rows):
            assert lower.max_qty == upper.min_qty


def test_money_is_exact_decimal(catalog: Catalog):
    row = next(iter(catalog.price_rows.values()))[0]
    product = next(iter(catalog.products.values()))
    assert isinstance(row.unit_price, Decimal) and isinstance(product.standard_cost_usd, Decimal)

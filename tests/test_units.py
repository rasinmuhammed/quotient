from decimal import Decimal as D

import pytest

from quotient.catalog import Catalog
from quotient.domain import FlagCode
from quotient.pricing.units import to_uom


def conv(catalog: Catalog, sku: str, value: str, unit: str, country: str | None = None):
    return to_uom(D(value), unit, catalog.product(sku), catalog.packs, country)


@pytest.mark.parametrize(
    ("value", "unit", "kg"),
    [
        ("10", "MT", "10000"),
        ("2.5", "metric tonnes", "2500"),
        ("3", "t", "3000"),
        ("750", "kgs", "750"),
        ("1000", "lbs", "453.5924"),
        ("500", "g", "0.5"),
    ],
)
def test_mass_units_to_kg(catalog, value, unit, kg):
    c = conv(catalog, "QX-TIO2-TG", value, unit)
    assert c.quantity == D(kg) and c.uom == "kg" and not c.flags


def test_litres_of_a_per_kg_liquid_go_through_density(catalog):
    c = conv(catalog, "QX-IPA-TG", "1000", "litres")
    assert c.quantity == D("786.0000")  # 1000 L x 0.786 kg/L
    assert "0.786 kg/L" in c.steps[-1]


def test_kg_of_a_per_litre_liquid_go_through_density(catalog):
    c = conv(catalog, "QX-TOL-AR", "867", "kg")
    assert c.uom == "L" and c.quantity == D("1000.0000")  # 867 kg / 0.867 kg/L


def test_drums_of_a_liquid_are_200_litre_drums(catalog):
    c = conv(catalog, "QX-IPA-TG", "5", "drums")
    assert c.pack_code == "DRUM200" and c.quantity == D("786.0000")
    assert c.steps[0].startswith("5 x 200 L drum = 1,000 L")


def test_drums_of_a_solid_are_fibre_drums(catalog):
    c = conv(catalog, "QX-APAP-USP", "8", "drums")
    assert c.pack_code == "FDRUM25" and c.quantity == D("200.0000")


def test_german_pack_words(catalog):
    assert conv(catalog, "QX-NMP-TG", "4", "Fässer").pack_code == "DRUM200"
    assert conv(catalog, "QX-TIO2-TG", "40", "Säcke").pack_code == "BAG25"


def test_us_gallons(catalog):
    c = conv(catalog, "QX-ACE-AR", "100", "gal")
    assert c.quantity == D("378.5412")


def test_a_volume_for_a_solid_cannot_be_converted(catalog):
    c = conv(catalog, "QX-TIO2-TG", "500", "L")
    assert c.quantity is None and [f.code for f in c.flags] == [FlagCode.UNIT_CONVERSION]


def test_bags_of_a_liquid_cannot_be_converted(catalog):
    c = conv(catalog, "QX-IPA-TG", "10", "bags")
    assert c.quantity is None and c.flags[0].code == FlagCode.UNIT_CONVERSION


def test_tons_from_a_us_buyer_are_flagged_but_not_from_elsewhere(catalog):
    us = conv(catalog, "QX-TIO2-TG", "20", "tons", "US")
    de = conv(catalog, "QX-TIO2-TG", "20", "tons", "DE")
    assert us.quantity == de.quantity == D("20000")
    assert [f.code for f in us.flags] == [FlagCode.AMBIGUOUS_UNIT] and not de.flags


def test_unknown_unit_is_flagged(catalog):
    c = conv(catalog, "QX-IPA-TG", "3", "pallets")
    assert c.quantity is None and c.flags[0].code == FlagCode.UNIT_CONVERSION


@pytest.mark.parametrize(
    ("sku", "qty", "unit", "expected", "pack"),
    [
        ("QX-MEOH-AR", "20", "25L cans", "500", "CAN25"),
        ("QX-MEOH-AR", "20", "x 25L", "500", "CAN25"),  # the size alone names the only 25 L pack
        ("QX-ZNO-TG", "20", "1 MT big bags", "20000", "FIBC1000"),
        ("QX-NAOH50-TG", "3", "IBC totes", "4560", "IBC1000"),  # 3 x 1000 L x 1.52 kg/L
        ("QX-TIO2-TG", "10", "50 kg bags", "500", None),  # not a pack we sell: an amount, no pack
    ],
)
def test_pack_phrasings(catalog, sku, qty, unit, expected, pack):
    c = conv(catalog, sku, qty, unit)
    assert c.quantity == D(expected) and c.pack_code == pack and not c.flags


@pytest.mark.parametrize("unit", ["20ft FCL", "pallets", "truckloads"])
def test_units_without_a_fixed_quantity_are_flagged(catalog, unit):
    c = conv(catalog, "QX-MEG-TG", "2", unit)
    assert c.quantity is None and c.flags[0].code == FlagCode.UNIT_CONVERSION

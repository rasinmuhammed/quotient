"""Quantity normalisation: whatever unit the customer wrote, into the SKU's unit of measure (kg or L).

Every conversion records its steps in words, so the review screen can show "5 drums -> 1000 L -> 786 kg".
When a conversion cannot be made (a volume for a solid) or the unit is ambiguous, the result carries a flag
instead of a guess.
"""

from __future__ import annotations

import re
from decimal import Decimal

from quotient.domain import Flag, FlagCode, Model, Pack, Product

RULE = "UNIT-CONV-v1"
LB_KG = Decimal("0.45359237")
GAL_L = Decimal("3.785411784")
Q = Decimal("0.0001")

MASS = {"kg": Decimal(1), "g": Decimal("0.001"), "mt": Decimal(1000), "lb": LB_KG}
VOLUME = {"l": Decimal(1), "ml": Decimal("0.001"), "gal": GAL_L, "m3": Decimal(1000)}

ALIASES = {
    "kg": "kg",
    "kgs": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "kilogramm": "kg",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "mt": "mt",
    "t": "mt",
    "tonne": "mt",
    "tonnes": "mt",
    "metric ton": "mt",
    "metric tons": "mt",
    "metric tonne": "mt",
    "metric tonnes": "mt",
    "tonnen": "mt",
    "ton": "ton",
    "tons": "ton",  # metric everywhere except, possibly, the United States
    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "l": "l",
    "lt": "l",
    "ltr": "l",
    "ltrs": "l",
    "litre": "l",
    "litres": "l",
    "liter": "l",
    "liters": "l",
    "ml": "ml",
    "gal": "gal",
    "gals": "gal",
    "gallon": "gal",
    "gallons": "gal",
    "us gal": "gal",
    "m3": "m3",
    "cbm": "m3",
}

# Pack words, by product form: a "drum" of a solid is a fibre drum.
PACK_WORDS = {
    "drum": {"liquid": "DRUM200", "solid": "FDRUM25"},
    "barrel": {"liquid": "DRUM200"},
    "fass": {"liquid": "DRUM200"},
    "ibc": {"liquid": "IBC1000"},
    "tote": {"liquid": "IBC1000"},
    "container ibc": {"liquid": "IBC1000"},
    "can": {"liquid": "CAN25"},
    "jerrycan": {"liquid": "CAN25"},
    "kanister": {"liquid": "CAN25"},
    "bag": {"solid": "BAG25"},
    "sack": {"solid": "BAG25"},
    "big bag": {"solid": "FIBC1000"},
    "bigbag": {"solid": "FIBC1000"},
    "fibc": {"solid": "FIBC1000"},
    "jumbo bag": {"solid": "FIBC1000"},
    "fibre drum": {"solid": "FDRUM25"},
    "fiber drum": {"solid": "FDRUM25"},
}
_PLURALS = {"fässer": "fass", "faesser": "fass", "säcke": "sack", "saecke": "sack", "kanister": "kanister"}


class Conversion(Model):
    quantity: Decimal | None  # in the product's unit of measure; None when it cannot be converted
    uom: str
    pack_code: str | None = None  # the unit was itself a pack ("12 drums")
    steps: tuple[str, ...] = ()
    flags: tuple[Flag, ...] = ()


def canonical_unit(unit: str) -> str:
    u = re.sub(r"[.\s]+", " ", unit.strip().lower()).strip()
    return ALIASES.get(u, u)


def pack_for_word(unit: str, form: str) -> str | None:
    u = re.sub(r"[.\s]+", " ", unit.strip().lower()).strip()
    u = _PLURALS.get(u, u)
    for word in (u, u.rstrip("s"), u.removesuffix("es")):
        if word in PACK_WORDS:
            return PACK_WORDS[word].get(form)
    return None


def is_pack_word(unit: str) -> bool:
    u = re.sub(r"[.\s]+", " ", unit.strip().lower()).strip()
    u = _PLURALS.get(u, u)
    return any(w in PACK_WORDS for w in (u, u.rstrip("s"), u.removesuffix("es")))


def pack_size_in_uom(product: Product, pack: Pack) -> Decimal:
    """One pack's content in the product's unit of measure (a 200 L drum of a per-kg liquid holds 200 x density kg)."""
    if pack.size_uom == product.uom:
        return pack.size
    if product.density_kg_per_l is None:
        raise ValueError(f"{product.sku}: pack {pack.pack_code} is in {pack.size_uom} but the product has no density")
    if pack.size_uom == "L":
        return (pack.size * product.density_kg_per_l).quantize(Q)
    return (pack.size / product.density_kg_per_l).quantize(Q)


def _fmt(d: Decimal) -> str:
    return f"{d.normalize():,f}"


def to_uom(
    value: Decimal, unit: str, product: Product, packs: dict[str, Pack], country_code: str | None = None
) -> Conversion:
    """Convert `value unit` into the product's unit of measure."""
    target = product.uom
    density = product.density_kg_per_l
    steps: list[str] = []
    flags: list[Flag] = []
    pack_code = None
    u = canonical_unit(unit)

    if is_pack_word(unit):
        pack_code = pack_for_word(unit, product.form)
        if pack_code is None:
            return Conversion(
                quantity=None,
                uom=target,
                flags=(Flag.of(FlagCode.UNIT_CONVERSION, f"'{unit}' is not a pack a {product.form} ships in", RULE),),
            )
        pack = packs[pack_code]
        amount, u = value * pack.size, pack.size_uom.lower()
        steps.append(f"{_fmt(value)} x {pack.description} = {_fmt(amount)} {pack.size_uom}")
    else:
        amount = value

    if u == "ton":
        if country_code == "US":
            flags.append(
                Flag.of(
                    FlagCode.AMBIGUOUS_UNIT,
                    f"'{unit}' from a US buyer may mean short tons (907.185 kg); read as metric tonnes",
                    RULE,
                )
            )
        steps.append(f"'{unit}' read as metric tonnes")
        u = "mt"

    if u in MASS:
        kg = amount * MASS[u]
        if u != "kg":
            steps.append(f"{_fmt(amount)} {u} = {_fmt(kg)} kg")
        if target == "kg":
            return Conversion(
                quantity=kg.quantize(Q), uom=target, pack_code=pack_code, steps=tuple(steps), flags=tuple(flags)
            )
        if density is None:  # a per-litre SKU always has a density; kept for safety
            return Conversion(
                quantity=None,
                uom=target,
                flags=(Flag.of(FlagCode.UNIT_CONVERSION, "no density to convert kg to L", RULE),),
            )
        litres = (kg / density).quantize(Q)
        steps.append(f"{_fmt(kg)} kg / {_fmt(density)} kg/L = {_fmt(litres)} L")
        return Conversion(quantity=litres, uom=target, pack_code=pack_code, steps=tuple(steps), flags=tuple(flags))

    if u in VOLUME:
        litres = amount * VOLUME[u]
        if u != "l":
            steps.append(f"{_fmt(amount)} {u} = {_fmt(litres)} L")
        if target == "L":
            return Conversion(
                quantity=litres.quantize(Q), uom=target, pack_code=pack_code, steps=tuple(steps), flags=tuple(flags)
            )
        if density is None:
            return Conversion(
                quantity=None,
                uom=target,
                flags=(
                    Flag.of(
                        FlagCode.UNIT_CONVERSION,
                        f"{product.name} is a solid: a volume ({_fmt(value)} {unit}) cannot be converted to kg",
                        RULE,
                    ),
                ),
            )
        kg = (litres * density).quantize(Q)
        steps.append(f"{_fmt(litres)} L x {_fmt(density)} kg/L = {_fmt(kg)} kg")
        return Conversion(quantity=kg, uom=target, pack_code=pack_code, steps=tuple(steps), flags=tuple(flags))

    return Conversion(
        quantity=None, uom=target, flags=(Flag.of(FlagCode.UNIT_CONVERSION, f"unknown unit '{unit}'", RULE),)
    )

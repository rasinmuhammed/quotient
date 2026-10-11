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

# Pack words, by product form: a "drum" of a solid is a fibre drum. Multi-word names are tried first.
PACK_WORDS = {
    "fibre drum": {"solid": "FDRUM25"},
    "fiber drum": {"solid": "FDRUM25"},
    "big bag": {"solid": "FIBC1000"},
    "jumbo bag": {"solid": "FIBC1000"},
    "bigbag": {"solid": "FIBC1000"},
    "fibc": {"solid": "FIBC1000"},
    "drum": {"liquid": "DRUM200", "solid": "FDRUM25"},
    "barrel": {"liquid": "DRUM200"},
    "fass": {"liquid": "DRUM200"},
    "ibc": {"liquid": "IBC1000"},
    "tote": {"liquid": "IBC1000"},
    "jerrycan": {"liquid": "CAN25"},
    "kanister": {"liquid": "CAN25"},
    "can": {"liquid": "CAN25"},
    "bag": {"solid": "BAG25"},
    "sack": {"solid": "BAG25"},
}
_PLURALS = {"fässer": "fass", "faesser": "fass", "säcke": "sack", "saecke": "sack", "kanister": "kanister"}
_SIZED = re.compile(r"^(?:x\s*)?(\d+(?:[.,]\d+)?)\s*(kg|kgs|mt|t|l|lt|ltr|litre|liter)\b\.?\s*(.*)$")


def _clean(text: str) -> str:
    return re.sub(r"[.\s]+", " ", text.strip().lower()).strip()


def pack_word_in(text: str) -> str | None:
    """The pack word a unit or packaging text names ("IBC totes" -> "ibc", "steel drums" -> "drum"), if any."""
    t = _clean(text)
    words = [_PLURALS.get(w, w) for w in re.findall(r"[a-zäöüß]+", t)]
    singular = [w[:-2] if w.endswith("es") and w[:-2] in PACK_WORDS else w[:-1] if w.endswith("s") and w[:-1] in PACK_WORDS else w
                for w in words]
    joined = " ".join(singular)
    for key in PACK_WORDS:  # multi-word names first (dict order)
        if re.search(rf"\b{re.escape(key)}\b", joined):
            return key
    return None


def pack_for_word(text: str, form: str) -> str | None:
    word = pack_word_in(text)
    return PACK_WORDS[word].get(form) if word else None


def is_pack_word(text: str) -> bool:
    return canonical_unit(text) not in ALIASES.values() and pack_word_in(text) is not None


def sized_pack(text: str, form: str, packs: dict[str, Pack]) -> tuple[Decimal, str, str | None] | None:
    """'25L cans', '200 L drums', '1 MT big bags': (size, size unit, the matching pack code or None)."""
    m = _SIZED.match(_clean(text))
    if not m or (m.group(3) and pack_word_in(m.group(3)) is None):
        return None
    size = Decimal(m.group(1).replace(",", "."))
    unit = canonical_unit(m.group(2))
    if unit == "mt":
        size, unit = size * 1000, "kg"
    unit_label = "L" if unit == "l" else unit
    if m.group(3):  # "25L cans": the pack word names it
        code = pack_for_word(m.group(3), form)
        same = code is not None and packs[code].size == size and packs[code].size_uom == unit_label
        return size, unit, code if same else None
    # "x 25L": the size alone names a pack when exactly one pack of this form has that size
    fits = [c for c, p in packs.items() if p.form == form and p.size == size and p.size_uom == unit_label]
    return size, unit, fits[0] if len(fits) == 1 else None


def pack_from_packaging(text: str, product: Product, packs: dict[str, Pack]) -> tuple[str | None, tuple[Flag, ...]]:
    """The pack a packaging phrase asks for ("in IBC", "25 kg bags"). A pack word or size we do not ship this
    product in is flagged, never swapped silently; text that names no pack at all leaves the default rule to it."""
    sized = sized_pack(text, product.form, packs)
    if sized is not None:
        if sized[2] is not None:
            return sized[2], ()
        return None, (Flag.of(FlagCode.PACK_UNAVAILABLE, f"'{text}' is not a pack size we offer; priced in the default pack",
                              "PACK-OFFERED-v1"),)
    word = pack_word_in(text)
    if word is None:
        return None, ()
    code = PACK_WORDS[word].get(product.form)
    if code is None:
        return None, (Flag.of(FlagCode.PACK_UNAVAILABLE, f"{product.chemical_name} is a {product.form}; it does not ship in "
                              f"{word}s. Priced in the default pack", "PACK-OFFERED-v1"),)
    return code, ()


class Conversion(Model):
    quantity: Decimal | None  # in the product's unit of measure; None when it cannot be converted
    uom: str
    pack_code: str | None = None  # the unit was itself a pack ("12 drums")
    steps: tuple[str, ...] = ()
    flags: tuple[Flag, ...] = ()


def canonical_unit(unit: str) -> str:
    u = re.sub(r"[.\s]+", " ", unit.strip().lower()).strip()
    return ALIASES.get(u, u)


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

    sized = sized_pack(unit, product.form, packs)
    if sized is not None:
        size, u, pack_code = sized
        amount = value * size
        label = packs[pack_code].description if pack_code else f"{_fmt(size)} {u} pack"
        steps.append(f"{_fmt(value)} x {label} = {_fmt(amount)} {'L' if u == 'l' else u}")
    elif is_pack_word(unit):
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

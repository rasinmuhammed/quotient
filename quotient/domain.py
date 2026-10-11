"""Types shared by every stage. Money and quantities are Decimal end to end; floats never carry a price."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Severity(StrEnum):
    BLOCK = "block"  # a human must resolve it before the quote can go out
    WARN = "warn"  # goes out as is unless a human objects; always shown


class FlagCode(StrEnum):
    LOW_CONFIDENCE = "low_confidence_extraction"
    QUANTITY_MISSING = "quantity_missing"
    NO_MATCH = "no_match"
    AMBIGUOUS_MATCH = "ambiguous_match"
    GRADE_NOT_SPECIFIED = "grade_not_specified"
    UNIT_CONVERSION = "unit_conversion_impossible"
    AMBIGUOUS_UNIT = "ambiguous_unit"
    PACK_UNAVAILABLE = "pack_unavailable"
    BELOW_MOQ = "below_moq"
    PRICE_EXPIRED = "price_expired"
    NO_PRICE = "no_price"
    MARGIN_FLOOR = "margin_floor_applied"
    INCOTERM_CONFLICT = "incoterm_conflict"
    INCOTERM_UNSUPPORTED = "incoterm_unsupported"
    NO_FREIGHT_LANE = "no_freight_lane"
    HAZMAT = "hazmat_documents"
    CUSTOMER_UNKNOWN = "customer_unknown"


# Severity is policy, fixed per code, not chosen by whoever raises the flag.
SEVERITY: dict[FlagCode, Severity] = {
    FlagCode.LOW_CONFIDENCE: Severity.BLOCK,
    FlagCode.QUANTITY_MISSING: Severity.BLOCK,
    FlagCode.NO_MATCH: Severity.BLOCK,
    FlagCode.AMBIGUOUS_MATCH: Severity.BLOCK,
    FlagCode.GRADE_NOT_SPECIFIED: Severity.BLOCK,
    FlagCode.UNIT_CONVERSION: Severity.BLOCK,
    FlagCode.AMBIGUOUS_UNIT: Severity.BLOCK,
    FlagCode.PACK_UNAVAILABLE: Severity.BLOCK,
    FlagCode.BELOW_MOQ: Severity.BLOCK,
    FlagCode.PRICE_EXPIRED: Severity.BLOCK,
    FlagCode.NO_PRICE: Severity.BLOCK,
    FlagCode.MARGIN_FLOOR: Severity.WARN,
    FlagCode.INCOTERM_CONFLICT: Severity.BLOCK,
    FlagCode.INCOTERM_UNSUPPORTED: Severity.BLOCK,
    FlagCode.NO_FREIGHT_LANE: Severity.BLOCK,
    FlagCode.HAZMAT: Severity.WARN,
    FlagCode.CUSTOMER_UNKNOWN: Severity.WARN,
}


class Flag(Model):
    code: FlagCode
    severity: Severity
    message: str
    rule_id: str | None = None

    @classmethod
    def of(cls, code: FlagCode, message: str, rule_id: str | None = None) -> Flag:
        return cls(code=code, severity=SEVERITY[code], message=message, rule_id=rule_id)


class Step(Model):
    """One line of a price breakdown: what was done, under which rule, from which source row."""

    rule_id: str
    label: str
    value: Decimal | None = None
    unit: str = ""
    source: str | None = None  # the data row the value came from (price, freight, fx row id)


# ------------------------------------------------------------------------------------------- catalog records


class Product(Model):
    sku: str
    name: str
    chemical_code: str
    chemical_name: str
    cas: str
    grade: str
    grade_label: str
    purity_pct: Decimal
    form: str
    uom: str  # "kg" or "L": what the price list is per
    density_kg_per_l: Decimal | None
    hs_code: str
    un_number: str | None
    hazard_class: str | None
    moq: Decimal
    lead_time_days: int
    origin_port: str
    standard_cost_usd: Decimal
    pack_codes: tuple[str, ...]
    synonyms: tuple[str, ...]
    trade_names: tuple[str, ...]


class Pack(Model):
    pack_code: str
    description: str
    size: Decimal
    size_uom: str
    form: str


class PriceRow(Model):
    price_row_id: str
    sku: str
    pack_code: str
    currency: str
    uom: str
    tier: int
    min_qty: Decimal
    max_qty: Decimal | None  # exclusive; None on the top tier
    unit_price: Decimal
    valid_from: date
    valid_to: date


class Customer(Model):
    customer_id: str
    name: str
    country: str
    country_code: str
    currency: str
    default_incoterm: str
    payment_terms: str
    discount_tier: str
    contact_name: str
    contact_email: str


class FreightRate(Model):
    freight_row_id: str
    origin_port: str
    origin_port_name: str
    destination_country: str
    destination_country_code: str
    destination_port: str
    destination_port_name: str
    incoterm: str
    rate_usd_per_kg: Decimal
    transit_days: int


class FxRate(Model):
    fx_row_id: str
    snapshot_date: date
    base: str
    quote: str
    rate: Decimal


# ------------------------------------------------------------------------------------------- pricing


class PricingRequest(Model):
    """A matched, normalised line ready to price. Quantity is in the SKU's unit of measure."""

    sku: str
    quantity: Decimal
    pack_code: str | None = None  # None: the default pack rule chooses
    customer_id: str | None = None
    incoterm: str
    destination_country_code: str | None = None
    currency: str | None = None  # None: the customer's currency, else USD
    quote_date: date


class PricedLine(Model):
    sku: str
    product_name: str
    uom: str
    pack_code: str
    pack_description: str
    pack_size: Decimal  # one pack, in the SKU's unit of measure
    packs: int
    requested_qty: Decimal
    quoted_qty: Decimal
    incoterm: str
    currency: str
    # per unit of measure, USD, each rounded once at the component precision so the parts add up exactly
    list_unit_usd: Decimal | None
    discount_pct: Decimal
    discount_unit_usd: Decimal | None
    net_unit_usd: Decimal | None
    freight_unit_usd: Decimal | None
    dg_unit_usd: Decimal | None
    insurance_unit_usd: Decimal | None
    delivered_unit_usd: Decimal | None
    unit_price: Decimal | None  # in the quote currency, per unit of measure
    line_total: Decimal | None
    price_row_id: str | None
    price_tier: int | None
    freight_row_id: str | None
    fx_row_id: str
    fx_rate: Decimal
    fx_snapshot_date: date
    lead_time_days: int
    transit_days: int | None
    rules_applied: tuple[str, ...]
    steps: tuple[Step, ...]
    flags: tuple[Flag, ...]

    @property
    def priced(self) -> bool:
        return self.line_total is not None

    @property
    def blocked(self) -> bool:
        return any(f.severity == Severity.BLOCK for f in self.flags)

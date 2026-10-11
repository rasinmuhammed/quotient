"""The deterministic pricing engine.

One pure function from a matched, normalised line to a priced line. No model is involved: every number comes
from a catalog row and a named rule, and each step of the breakdown cites both. The order of operations is
fixed and is the order the breakdown is shown in:

    pack -> whole packs -> MOQ check -> volume tier -> validity -> discount -> margin floor
         -> freight -> DG surcharge -> CIF insurance -> FX -> rounding

Problems are flagged, never fixed silently: a line below MOQ is priced as asked and blocked; an expired price
is shown as the last known price and blocked; a missing freight lane leaves the line without a delivered price.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from quotient.catalog import Catalog
from quotient.domain import Flag, FlagCode, Pack, PricedLine, PricingRequest, Product, Step
from quotient.pricing.rules import RuleSet
from quotient.pricing.units import pack_size_in_uom

PACK_DEFAULT = "PACK-DEFAULT-v1"
PACK_OFFERED = "PACK-OFFERED-v1"
PACK_ROUNDUP = "PACK-ROUNDUP-v1"
PRICE_TIER = "PRICE-TIER-v1"
PRICE_VALIDITY = "PRICE-VALIDITY-v1"
MOQ = "MOQ-v1"
DISC_TIER = "DISC-TIER-v1"
MARGIN_FLOOR = "MARGIN-FLOOR-v1"
FREIGHT = "FREIGHT-v1"
DG_SURCHARGE = "DG-SURCHARGE-v1"
CIF_INSURANCE = "CIF-INSURANCE-v1"
FX = "FX-v1"
ROUND = "ROUND-v1"
HAZMAT_DOCS = "HAZMAT-DOCS-v1"

RULES_USED = (
    PACK_DEFAULT,
    PACK_OFFERED,
    PACK_ROUNDUP,
    PRICE_TIER,
    PRICE_VALIDITY,
    MOQ,
    DISC_TIER,
    MARGIN_FLOOR,
    FREIGHT,
    DG_SURCHARGE,
    CIF_INSURANCE,
    FX,
    ROUND,
    HAZMAT_DOCS,
)
MAIN_CARRIAGE = ("CFR", "CIF", "DAP")
HUNDRED = Decimal(100)


def _q(value: Decimal, places: int, rounding: str = ROUND_HALF_UP) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=rounding)


def _n(d: Decimal) -> str:
    """A decimal for a sentence: grouped, without trailing zeros."""
    s = f"{d.normalize():,f}"
    return s


class _Trace:
    """Accumulates the breakdown, the flags and the rules a line used."""

    def __init__(self) -> None:
        self.steps: list[Step] = []
        self.flags: list[Flag] = []
        self.rules: list[str] = []

    def step(
        self, rule_id: str, label: str, value: Decimal | None = None, unit: str = "", source: str | None = None
    ) -> None:
        self.use(rule_id)
        self.steps.append(Step(rule_id=rule_id, label=label, value=value, unit=unit, source=source))

    def flag(self, code: FlagCode, message: str, rule_id: str) -> None:
        self.use(rule_id)
        self.flags.append(Flag.of(code, message, rule_id))

    def use(self, rule_id: str) -> None:
        if rule_id not in self.rules:
            self.rules.append(rule_id)


class PricingEngine:
    def __init__(self, catalog: Catalog, rules: RuleSet) -> None:
        rules.require(*RULES_USED)
        self.catalog = catalog
        self.rules = rules
        self.cdp = int(rules.param(ROUND, "component_decimals"))

    # ------------------------------------------------------------------------------------------------ helpers

    def _decimals(self, key: str, currency: str) -> int:
        table = self.rules.param(ROUND, key)
        return int(table.get(currency, table["default"]))

    def _c(self, value: Decimal) -> Decimal:
        return _q(value, self.cdp)

    def _valid_on(self, sku: str, pack_code: str, day) -> bool:
        rows = self.catalog.price_rows.get((sku, pack_code), ())
        return bool(rows) and all(r.valid_from <= day <= r.valid_to for r in rows)

    def _choose_pack(self, p: Product, req: PricingRequest, t: _Trace) -> Pack:
        packs = self.catalog.packs
        if req.pack_code and req.pack_code in p.pack_codes:
            pack = packs[req.pack_code]
            t.step(PACK_OFFERED, f"{pack.description}, as requested", source=pack.pack_code)
            return pack
        tolerance = self.rules.decimal(PACK_DEFAULT, "max_overshoot_pct") / HUNDRED
        valid = [c for c in p.pack_codes if self._valid_on(p.sku, c, req.quote_date)]
        options = []  # (overshoot, -size, pack): whole packs needed minus what was asked, as a share of what was asked
        for code in valid or p.pack_codes:
            size = pack_size_in_uom(p, packs[code])
            needed = (req.quantity / size).to_integral_value(rounding=ROUND_CEILING) * size
            options.append(((needed - req.quantity) / req.quantity, -size, packs[code]))
        close = [o for o in options if o[0] <= tolerance]
        pack = min(close, key=lambda o: o[1])[2] if close else min(options, key=lambda o: (o[0], o[1]))[2]
        if req.pack_code:
            asked = packs[req.pack_code].description if req.pack_code in packs else req.pack_code
            t.flag(
                FlagCode.PACK_UNAVAILABLE,
                f"{p.sku} is not offered in {asked}; priced in {pack.description} "
                f"(offered: {', '.join(packs[c].description for c in p.pack_codes)})",
                PACK_OFFERED,
            )
        t.step(PACK_DEFAULT, f"{pack.description} (default pack for this quantity)", source=pack.pack_code)
        return pack

    # ------------------------------------------------------------------------------------------------ pricing

    def price(self, req: PricingRequest) -> PricedLine:
        c, t = self.catalog, _Trace()
        p = c.product(req.sku)
        uom = p.uom
        per = f"USD/{uom}"
        if req.quantity <= 0:
            raise ValueError(f"{req.sku}: quantity must be positive, got {req.quantity}")

        customer = c.customers.get(req.customer_id) if req.customer_id else None
        currency = req.currency or (customer.currency if customer else "USD")
        fx = c.fx.get(currency)
        if fx is None:
            raise ValueError(f"no FX rate for {currency} in the {c.fx_snapshot_date} snapshot")

        # pack and whole packs
        pack = self._choose_pack(p, req, t)
        size = pack_size_in_uom(p, pack)
        packs = int((req.quantity / size).to_integral_value(rounding=ROUND_CEILING))
        quoted = size * packs
        t.step(
            PACK_ROUNDUP,
            f"{packs} x {pack.description} ({_n(size)} {uom} each); requested {_n(req.quantity)} {uom}",
            quoted,
            uom,
        )

        if quoted < p.moq:
            t.flag(
                FlagCode.BELOW_MOQ,
                f"quoted {_n(quoted)} {uom} is below the MOQ of {_n(p.moq)} {uom}; priced as asked",
                MOQ,
            )
        if p.un_number:
            t.flag(
                FlagCode.HAZMAT,
                f"{p.un_number}, class {p.hazard_class}: SDS and dangerous goods declaration needed",
                HAZMAT_DOCS,
            )

        common = dict(
            sku=p.sku,
            product_name=p.name,
            uom=uom,
            pack_code=pack.pack_code,
            pack_description=pack.description,
            pack_size=size,
            packs=packs,
            requested_qty=req.quantity,
            quoted_qty=quoted,
            incoterm=req.incoterm.upper(),
            currency=currency,
            fx_row_id=fx.fx_row_id,
            fx_rate=fx.rate,
            fx_snapshot_date=c.fx_snapshot_date,
            lead_time_days=p.lead_time_days,
        )

        # volume tier and validity
        rows = c.price_rows.get((p.sku, pack.pack_code))
        if not rows:
            t.flag(FlagCode.NO_PRICE, f"no price rows for {p.sku} in {pack.description}", PRICE_TIER)
            return self._unpriced(common, t)
        row = next(r for r in reversed(rows) if r.min_qty <= quoted)
        band = f"{_n(row.min_qty)}+ {uom}" if row.max_qty is None else f"{_n(row.min_qty)} to <{_n(row.max_qty)} {uom}"
        if row.currency != "USD":
            raise ValueError(f"{row.price_row_id}: price rows are expected in USD, found {row.currency}")
        t.step(PRICE_TIER, f"List price, tier {row.tier} ({band})", row.unit_price, per, row.price_row_id)
        if not row.valid_from <= req.quote_date <= row.valid_to:
            t.flag(
                FlagCode.PRICE_EXPIRED,
                f"{row.price_row_id} is valid {row.valid_from} to {row.valid_to}, not on "
                f"{req.quote_date}; shown as the last known price",
                PRICE_VALIDITY,
            )

        # customer discount
        list_price = row.unit_price
        if customer:
            pct = self.rules.decimal(DISC_TIER, customer.discount_tier)
            label = f"Discount tier {customer.discount_tier} ({_n(pct)}%), {customer.name}"
        else:
            pct = Decimal(0)
            label = "No customer record: list price, no discount"
            if req.customer_id:
                t.flag(FlagCode.CUSTOMER_UNKNOWN, f"customer {req.customer_id} is not in the customer table", DISC_TIER)
        discount = self._c(list_price * pct / HUNDRED)
        net = list_price - discount
        t.step(DISC_TIER, label, -discount, per)

        # margin floor
        floor = self.rules.decimal(MARGIN_FLOOR, "min_margin_pct") / HUNDRED
        cost = p.standard_cost_usd
        if net - cost < floor * net:
            floor_price = _q(cost / (1 - floor), self.cdp, ROUND_CEILING)
            margin = (net - cost) / net * HUNDRED
            t.flag(
                FlagCode.MARGIN_FLOOR,
                f"net {_n(net)} {per} gives {margin:.1f}% margin, under the {_n(floor * HUNDRED)}% "
                f"floor; raised to {_n(floor_price)} {per}",
                MARGIN_FLOOR,
            )
            t.step(MARGIN_FLOOR, f"Raised to the {_n(floor * HUNDRED)}% margin floor", floor_price - net, per)
            net = floor_price
        t.step(DISC_TIER, "Net price", net, per)

        # freight, surcharge, insurance
        kg_per_unit = Decimal(1) if uom == "kg" else p.density_kg_per_l
        incoterm = req.incoterm.upper()
        freight = dg = insurance = Decimal(0)
        freight_row_id, transit = None, None
        supported = self.rules.param(FREIGHT, "supported")
        if incoterm not in supported:
            t.flag(
                FlagCode.INCOTERM_UNSUPPORTED,
                f"{incoterm} is not modelled (supported: {', '.join(supported)}); no delivered price",
                FREIGHT,
            )
            return self._unpriced(common, t, list_price, pct, discount, net, row.price_row_id, row.tier)
        if incoterm == "EXW":
            t.step(FREIGHT, f"EXW {p.origin_port}: buyer collects, no freight", Decimal(0), per)
        else:
            rate = self._freight_row(p.origin_port, req.destination_country_code, incoterm)
            if rate is None:
                where = req.destination_country_code or "an unstated destination"
                t.flag(FlagCode.NO_FREIGHT_LANE, f"no {incoterm} rate from {p.origin_port} to {where}", FREIGHT)
                return self._unpriced(common, t, list_price, pct, discount, net, row.price_row_id, row.tier)
            freight_row_id, transit = rate.freight_row_id, rate.transit_days
            freight = self._c(rate.rate_usd_per_kg * kg_per_unit)
            to = "origin handling" if incoterm == "FOB" else f"{rate.origin_port_name} to {rate.destination_port}"
            per_kg = "" if uom == "kg" else f" x {_n(kg_per_unit)} kg/L"
            t.step(FREIGHT, f"{incoterm} {to}: {_n(rate.rate_usd_per_kg)} USD/kg{per_kg}", freight, per, freight_row_id)
            if p.un_number and incoterm in MAIN_CARRIAGE:
                dg = self._c(self.rules.decimal(DG_SURCHARGE, "usd_per_kg") * kg_per_unit)
                t.step(DG_SURCHARGE, f"Dangerous goods surcharge ({p.un_number})", dg, per)
            if incoterm == "CIF":
                cover = self.rules.decimal(CIF_INSURANCE, "cover_pct") / HUNDRED
                ins_rate = self.rules.decimal(CIF_INSURANCE, "rate_pct") / HUNDRED
                insurance = self._c((net + freight + dg) * cover * ins_rate)
                t.step(
                    CIF_INSURANCE,
                    f"Insurance on {_n(cover * HUNDRED)}% of CFR value at {_n(ins_rate * HUNDRED)}%",
                    insurance,
                    per,
                )
        delivered = net + freight + dg + insurance
        t.step(FREIGHT, f"Delivered {incoterm}", delivered, per)

        # currency and rounding
        unit_dp, total_dp = self._decimals("unit_decimals", currency), self._decimals("total_decimals", currency)
        unit_price = _q(delivered * fx.rate, unit_dp)
        line_total = _q(unit_price * quoted, total_dp)
        if currency != "USD":
            t.step(
                FX,
                f"x {_n(fx.rate)} {currency}/USD (snapshot {c.fx_snapshot_date})",
                fx.rate,
                f"{currency}/USD",
                fx.fx_row_id,
            )
        else:
            t.use(FX)
        t.step(ROUND, f"Unit price, {unit_dp} decimals", unit_price, f"{currency}/{uom}")
        t.step(ROUND, f"Line total: {_n(unit_price)} x {_n(quoted)} {uom}, {total_dp} decimals", line_total, currency)

        return PricedLine(
            **common,
            list_unit_usd=list_price,
            discount_pct=pct,
            discount_unit_usd=discount,
            net_unit_usd=net,
            freight_unit_usd=freight,
            dg_unit_usd=dg,
            insurance_unit_usd=insurance,
            delivered_unit_usd=delivered,
            unit_price=unit_price,
            line_total=line_total,
            price_row_id=row.price_row_id,
            price_tier=row.tier,
            freight_row_id=freight_row_id,
            transit_days=transit,
            rules_applied=tuple(t.rules),
            steps=tuple(t.steps),
            flags=tuple(t.flags),
        )

    def _freight_row(self, origin: str, dest: str | None, incoterm: str):
        f = self.catalog.freight
        if dest is not None:
            return f.get((origin, dest, incoterm))
        if incoterm == "FOB":  # FOB handling depends on the origin port only; any of its rows carries the rate
            rows = sorted(
                (r for (o, _d, i), r in f.items() if o == origin and i == "FOB"), key=lambda r: r.freight_row_id
            )
            return rows[0] if rows else None
        return None

    @staticmethod
    def _unpriced(
        common: dict, t: _Trace, list_price=None, pct=Decimal(0), discount=None, net=None, price_row_id=None, tier=None
    ) -> PricedLine:
        return PricedLine(
            **common,
            list_unit_usd=list_price,
            discount_pct=pct,
            discount_unit_usd=discount,
            net_unit_usd=net,
            freight_unit_usd=None,
            dg_unit_usd=None,
            insurance_unit_usd=None,
            delivered_unit_usd=None,
            unit_price=None,
            line_total=None,
            price_row_id=price_row_id,
            price_tier=tier,
            freight_row_id=None,
            transit_days=None,
            rules_applied=tuple(t.rules),
            steps=tuple(t.steps),
            flags=tuple(t.flags),
        )


def quote_total(lines: Iterable[PricedLine]) -> Decimal | None:
    """Sum of the priced line totals, or None when no line could be priced. Lines must share one currency."""
    priced = [ln for ln in lines if ln.line_total is not None]
    if not priced:
        return None
    if len({ln.currency for ln in priced}) != 1:
        raise ValueError("a quote total needs all lines in one currency")
    return sum((ln.line_total for ln in priced), Decimal(0))

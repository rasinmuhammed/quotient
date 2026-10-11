"""The synthetic catalog, loaded once from data/*.csv into frozen, indexed records.

DuckDB reads each CSV against an explicit schema, so money and quantities arrive as exact DECIMALs and codes
such as HS 2905.12 stay text instead of being guessed into floats.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import duckdb

from quotient.config import settings
from quotient.domain import Customer, FreightRate, FxRate, Pack, PriceRow, Product

MONEY = "DECIMAL(18,4)"

SCHEMAS: dict[str, dict[str, str]] = {
    "products": {
        "sku": "VARCHAR",
        "name": "VARCHAR",
        "chemical_code": "VARCHAR",
        "chemical_name": "VARCHAR",
        "cas": "VARCHAR",
        "grade": "VARCHAR",
        "grade_label": "VARCHAR",
        "purity_pct": "DECIMAL(6,2)",
        "form": "VARCHAR",
        "uom": "VARCHAR",
        "density_kg_per_l": "DECIMAL(8,4)",
        "hs_code": "VARCHAR",
        "un_number": "VARCHAR",
        "hazard_class": "VARCHAR",
        "moq": "DECIMAL(18,4)",
        "lead_time_days": "INTEGER",
        "origin_port": "VARCHAR",
        "standard_cost_usd": MONEY,
        "pack_codes": "VARCHAR",
        "synonyms": "VARCHAR",
        "trade_names": "VARCHAR",
    },
    "packs": {
        "pack_code": "VARCHAR",
        "description": "VARCHAR",
        "size": "DECIMAL(10,3)",
        "size_uom": "VARCHAR",
        "form": "VARCHAR",
        "premium": "DECIMAL(6,3)",
    },
    "price_list": {
        "price_row_id": "VARCHAR",
        "sku": "VARCHAR",
        "pack_code": "VARCHAR",
        "currency": "VARCHAR",
        "uom": "VARCHAR",
        "tier": "INTEGER",
        "min_qty": "DECIMAL(18,4)",
        "max_qty": "DECIMAL(18,4)",
        "unit_price": MONEY,
        "valid_from": "DATE",
        "valid_to": "DATE",
    },
    "customers": {
        "customer_id": "VARCHAR",
        "name": "VARCHAR",
        "country": "VARCHAR",
        "country_code": "VARCHAR",
        "currency": "VARCHAR",
        "default_incoterm": "VARCHAR",
        "payment_terms": "VARCHAR",
        "discount_tier": "VARCHAR",
        "contact_name": "VARCHAR",
        "contact_email": "VARCHAR",
    },
    "freight_rates": {
        "freight_row_id": "VARCHAR",
        "origin_port": "VARCHAR",
        "origin_port_name": "VARCHAR",
        "destination_country": "VARCHAR",
        "destination_country_code": "VARCHAR",
        "destination_port": "VARCHAR",
        "destination_port_name": "VARCHAR",
        "incoterm": "VARCHAR",
        "rate_usd_per_kg": "DECIMAL(10,4)",
        "transit_days": "INTEGER",
    },
    "fx_rates": {
        "fx_row_id": "VARCHAR",
        "snapshot_date": "DATE",
        "base": "VARCHAR",
        "quote": "VARCHAR",
        "rate": "DECIMAL(14,6)",
        "source": "VARCHAR",
    },
}


def read_table(data_dir: Path, name: str) -> list[dict]:
    cols = SCHEMAS[name]
    spec = "{" + ", ".join(f"'{c}': '{t}'" for c, t in cols.items()) + "}"
    with duckdb.connect() as con:
        cur = con.execute(
            f"SELECT * FROM read_csv(?, header = true, columns = {spec})", [str(data_dir / f"{name}.csv")]
        )
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _split(v: str | None) -> tuple[str, ...]:
    return tuple(s for s in (v or "").split("|") if s)


@dataclass(frozen=True)
class Catalog:
    products: dict[str, Product]
    packs: dict[str, Pack]
    customers: dict[str, Customer]
    price_rows: dict[tuple[str, str], tuple[PriceRow, ...]]  # (sku, pack) -> tiers, ascending
    freight: dict[tuple[str, str, str], FreightRate]  # (origin port, destination country code, incoterm)
    fx: dict[str, FxRate]  # quote currency -> rate per USD
    fx_snapshot_date: date
    version: str = "unknown"  # a hash of the data files, from data/manifest.json

    @classmethod
    def load(cls, data_dir: Path) -> Catalog:
        products = {}
        for r in read_table(data_dir, "products"):
            r.update(
                pack_codes=_split(r["pack_codes"]), synonyms=_split(r["synonyms"]), trade_names=_split(r["trade_names"])
            )
            products[r["sku"]] = Product(**r)
        packs = {
            r["pack_code"]: Pack(**{k: v for k, v in r.items() if k != "premium"})
            for r in read_table(data_dir, "packs")
        }
        customers = {r["customer_id"]: Customer(**r) for r in read_table(data_dir, "customers")}

        tiers: dict[tuple[str, str], list[PriceRow]] = defaultdict(list)
        for r in read_table(data_dir, "price_list"):
            row = PriceRow(**r)
            tiers[(row.sku, row.pack_code)].append(row)
        price_rows = {k: tuple(sorted(v, key=lambda p: p.min_qty)) for k, v in tiers.items()}

        freight = {}
        for r in read_table(data_dir, "freight_rates"):
            f = FreightRate(**r)
            freight[(f.origin_port, f.destination_country_code, f.incoterm)] = f
        fx = {
            r["quote"]: FxRate(**{k: v for k, v in r.items() if k != "source"})
            for r in read_table(data_dir, "fx_rates")
        }
        snapshot = {f.snapshot_date for f in fx.values()}
        if len(snapshot) != 1:
            raise ValueError(f"fx_rates must be one snapshot, found dates {sorted(snapshot)}")

        for p in products.values():
            missing = [c for c in p.pack_codes if c not in packs or (p.sku, c) not in price_rows]
            if missing:
                raise ValueError(f"{p.sku}: packs {missing} have no pack record or price rows")
        manifest = data_dir / "manifest.json"
        version = "unknown"
        if manifest.exists():
            files = json.loads(manifest.read_text())["files"]
            version = hashlib.sha256("".join(f["sha256"] for _, f in sorted(files.items())).encode()).hexdigest()[:12]
        return cls(products, packs, customers, price_rows, freight, fx, snapshot.pop(), version)

    def product(self, sku: str) -> Product:
        try:
            return self.products[sku]
        except KeyError:
            raise KeyError(f"unknown SKU {sku!r}") from None


@lru_cache
def default_catalog() -> Catalog:
    return Catalog.load(settings().quotient_data_dir)

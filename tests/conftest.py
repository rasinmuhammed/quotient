from __future__ import annotations

import pytest

from quotient.catalog import Catalog, default_catalog
from quotient.pricing.engine import PricingEngine
from quotient.pricing.rules import RuleSet, default_rules


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return default_catalog()


@pytest.fixture(scope="session")
def rules() -> RuleSet:
    return default_rules()


@pytest.fixture(scope="session")
def engine(catalog: Catalog, rules: RuleSet) -> PricingEngine:
    return PricingEngine(catalog, rules)

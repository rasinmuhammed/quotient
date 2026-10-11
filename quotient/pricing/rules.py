"""The named, versioned rule set the engine prices under."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from quotient.config import settings


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    text: str
    params: dict[str, Any]


@dataclass(frozen=True)
class RuleSet:
    name: str
    version: str
    rules: dict[str, Rule]

    @classmethod
    def load(cls, path: Path) -> RuleSet:
        raw = yaml.safe_load(path.read_text())
        rules = {
            rid: Rule(rid, r["title"], " ".join(r["text"].split()), r.get("params") or {})
            for rid, r in raw["rules"].items()
        }
        return cls(raw["ruleset"], str(raw["version"]), rules)

    def require(self, *ids: str) -> None:
        missing = [i for i in ids if i not in self.rules]
        if missing:
            raise ValueError(f"rule set {self.version} lacks rules {missing}")

    def decimal(self, rule_id: str, *path: str) -> Decimal:
        """A numeric parameter as an exact Decimal (YAML numbers are read through str, never through float maths)."""
        v: Any = self.rules[rule_id].params
        for key in path:
            v = v[key]
        return Decimal(str(v))

    def param(self, rule_id: str, key: str) -> Any:
        return self.rules[rule_id].params[key]


@lru_cache
def default_rules() -> RuleSet:
    return RuleSet.load(settings().quotient_rules_file)

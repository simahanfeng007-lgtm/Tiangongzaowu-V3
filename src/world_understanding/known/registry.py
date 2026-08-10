"""Shared stateless deterministic-rule registry."""
from __future__ import annotations
from collections.abc import Iterable
from .rule import DeterministicRule

class RuleRegistry:
    __slots__ = ("_rules", "_declared_transitive_outputs")
    def __init__(self, rules: Iterable[DeterministicRule] = (), *, declared_transitive_outputs: Iterable[str] = ()) -> None:
        self._rules: dict[str, DeterministicRule] = {}
        self._declared_transitive_outputs = frozenset(declared_transitive_outputs)
        for rule in rules:
            self.register(rule)

    def register(self, rule: DeterministicRule, *, replace: bool = False) -> None:
        key = rule.spec.rule_id
        if not key:
            raise ValueError("rule_id required")
        if rule.spec.allows_transitivity and key != "wu.rule.code.call-reachability" and not self._declared_transitive_outputs:
            raise ValueError("transitive rule requires explicit ontology declaration")
        if key in self._rules and not replace:
            raise ValueError(f"rule already registered: {key}")
        self._rules[key] = rule

    def rules(self) -> tuple[DeterministicRule, ...]:
        return tuple(self._rules[key] for key in sorted(self._rules))

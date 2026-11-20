"""Approve a fixed configuration before the replay port starts."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from station.capture.filter_table import FixedFilterTable, resolve_fixed_table
from station.procedure.registration import ProcedureDraft
from station.procedure.snapshot import FrozenRules, freeze_rules


class AdmissionError(ValueError):
    """An invalid subscription, filter table, or port capacity request."""


def _resolve_subscription(rules: FrozenRules, subscription: Iterable[str]) -> frozenset[str]:
    if isinstance(subscription, (str, bytes)) or not isinstance(subscription, Iterable):
        raise AdmissionError('subscription must be a collection of stream names')
    members = tuple(subscription)
    if not members or any(not isinstance(name, str) or name not in rules.catalog.rates for name in members):
        raise AdmissionError('subscription must contain registered streams')
    return frozenset(members)


def resolve_switch_target(
    rules: FrozenRules, subscription: Iterable[str], slots: Sequence[str | None]
) -> tuple[frozenset[str], FixedFilterTable]:
    """Validate a requested terminal configuration against the frozen rules."""
    requested = _resolve_subscription(rules, subscription)
    try:
        table = resolve_fixed_table(rules.catalog, slots)
    except (KeyError, ValueError) as exc:
        raise AdmissionError('invalid fixed filter table') from exc
    if not requested <= table.allowed_streams:
        raise AdmissionError('physical filter table does not cover the subscription')
    if rules.catalog.reserved_rate(table.allowed_streams) > rules.capacity:
        raise AdmissionError('physical reservation exceeds capacity')
    return requested, table


@dataclass(frozen=True, slots=True)
class FixedAdmission:
    rules: FrozenRules
    subscription: frozenset[str]
    table: FixedFilterTable
    reserved_rate: int


def admit_fixed_round(
    procedure_id: str,
    draft: ProcedureDraft,
    subscription: Iterable[str],
    slots: Sequence[str | None],
) -> FixedAdmission:
    rules = freeze_rules(procedure_id, draft)
    requested, table = resolve_switch_target(rules, subscription, slots)
    rate = rules.catalog.reserved_rate(table.allowed_streams)
    return FixedAdmission(rules, requested, table, rate)

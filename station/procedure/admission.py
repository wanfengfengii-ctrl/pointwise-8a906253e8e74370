"""Approve a fixed configuration before the replay port starts."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from station.capture.filter_table import FixedFilterTable, resolve_fixed_table
from station.procedure.registration import ProcedureDraft
from station.procedure.snapshot import FrozenRules, freeze_rules


class AdmissionError(ValueError):
    """An invalid subscription, filter table, or port capacity request."""


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
    if isinstance(subscription, (str, bytes)) or not isinstance(subscription, Iterable):
        raise AdmissionError('subscription must be a collection of stream names')
    members = tuple(subscription)
    if not members or any(not isinstance(name, str) or name not in draft.catalog.rates for name in members):
        raise AdmissionError('subscription must contain registered streams')
    requested = frozenset(members)
    try:
        table = resolve_fixed_table(draft.catalog, slots)
    except (KeyError, ValueError) as exc:
        raise AdmissionError('invalid fixed filter table') from exc

    if not requested <= table.allowed_streams:
        raise AdmissionError('physical filter table does not cover the subscription')
    rate = draft.catalog.reserved_rate(table.allowed_streams)
    if rate > draft.capacity:
        raise AdmissionError(f'physical reservation {rate} exceeds capacity {draft.capacity}')
    return FixedAdmission(freeze_rules(procedure_id, draft), requested, table, rate)

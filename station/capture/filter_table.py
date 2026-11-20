"""Interpret the two fixed device slots as one physical allow set."""

from collections.abc import Sequence
from dataclasses import dataclass

from station.procedure.catalog import StreamCatalog


@dataclass(frozen=True, slots=True)
class FixedFilterTable:
    slots: tuple[str | None, str | None]
    _allowed: frozenset[str]

    @property
    def allowed_streams(self) -> frozenset[str]:
        return self._allowed

    def allows(self, stream: str) -> bool:
        return stream in self._allowed


def resolve_fixed_table(catalog: StreamCatalog, slots: Sequence[str | None]) -> FixedFilterTable:
    if isinstance(slots, (str, bytes)) or not isinstance(slots, Sequence) or len(slots) != 2:
        raise ValueError('exactly two filter slots are required')
    first, second = slots
    if any(name is not None and not isinstance(name, str) for name in (first, second)):
        raise ValueError('filter slots must be mask names or None')
    allowed = catalog.mask_members(first) | catalog.mask_members(second)
    return FixedFilterTable((first, second), allowed)

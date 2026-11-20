"""Device stream rates and named masks."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class StreamCatalog:
    rates: Mapping[str, int]
    masks: Mapping[str, frozenset[str]]

    def mask_members(self, name: str | None) -> frozenset[str]:
        if name is None:
            return frozenset()
        return self.masks[name]

    def reserved_rate(self, streams: Iterable[str]) -> int:
        return sum(self.rates[stream] for stream in frozenset(streams))


def _name(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_catalog(
    rates: Mapping[str, int], masks: Mapping[str, Iterable[str]]
) -> StreamCatalog:
    if not isinstance(rates, Mapping) or not rates:
        raise ValueError('at least one device stream is required')
    if not isinstance(masks, Mapping):
        raise ValueError('masks must be a mapping of names to streams')

    rate_copy = dict(rates)
    for stream, rate in rate_copy.items():
        if not _name(stream) or type(rate) is not int or rate <= 0:
            raise ValueError('stream names and positive integer rates are required')

    mask_copy = {}
    for name, members in masks.items():
        if not _name(name) or isinstance(members, (str, bytes)) or not isinstance(members, Iterable):
            raise ValueError('each mask requires a name and an iterable of streams')
        listed = tuple(members)
        if any(not _name(stream) or stream not in rate_copy for stream in listed):
            raise ValueError(f'mask {name} references an unknown stream')
        mask_copy[name] = frozenset(listed)

    return StreamCatalog(MappingProxyType(rate_copy), MappingProxyType(mask_copy))

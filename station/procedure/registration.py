"""Registration and revision of operator procedure drafts."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from station.procedure.calibration import CalibrationSpec, validate_calibration
from station.procedure.catalog import StreamCatalog, validate_catalog


@dataclass(slots=True)
class _DraftState:
    catalog: StreamCatalog
    calibrations: dict[str, CalibrationSpec]
    capacity: int


@dataclass(frozen=True, slots=True)
class ProcedureDraft:
    """Read-only view of the registered procedure's current draft."""

    _state: _DraftState

    @property
    def catalog(self) -> StreamCatalog:
        return self._state.catalog

    @property
    def calibrations(self) -> Mapping[str, CalibrationSpec]:
        return MappingProxyType(self._state.calibrations)

    @property
    def capacity(self) -> int:
        return self._state.capacity


def _validated_draft(rates, masks, calibrations, capacity) -> _DraftState:
    catalog = validate_catalog(rates, masks)
    if type(capacity) is not int or capacity <= 0:
        raise ValueError('port capacity must be a positive integer')
    if not isinstance(calibrations, Mapping) or calibrations.keys() != catalog.rates.keys():
        raise ValueError('calibrations must cover exactly the registered streams')
    checked = {stream: validate_calibration(spec) for stream, spec in calibrations.items()}
    return _DraftState(catalog, checked, capacity)


class ProcedureRegistry:
    def __init__(self) -> None:
        self._drafts: dict[str, ProcedureDraft] = {}

    def register(
        self,
        rates: Mapping[str, int],
        masks: Mapping[str, Iterable[str]],
        calibrations: Mapping[str, CalibrationSpec],
        capacity: int,
    ) -> str:
        state = _validated_draft(rates, masks, calibrations, capacity)
        procedure_id = f'P-{len(self._drafts) + 1}'
        self._drafts[procedure_id] = ProcedureDraft(state)
        return procedure_id

    def revise(
        self,
        procedure_id: str,
        rates: Mapping[str, int],
        masks: Mapping[str, Iterable[str]],
        calibrations: Mapping[str, CalibrationSpec],
        capacity: int,
    ) -> None:
        state = self.get_draft(procedure_id)._state
        replacement = _validated_draft(rates, masks, calibrations, capacity)
        state.calibrations.clear()
        state.calibrations.update(replacement.calibrations)
        state.catalog = replacement.catalog
        state.capacity = replacement.capacity

    def get_draft(self, procedure_id: str) -> ProcedureDraft:
        return self._drafts[procedure_id]

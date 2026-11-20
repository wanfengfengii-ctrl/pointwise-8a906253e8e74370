"""Value snapshot of the complete rules at round admission."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from station.procedure.calibration import CalibrationSpec
from station.procedure.catalog import StreamCatalog, validate_catalog
from station.procedure.registration import ProcedureDraft


@dataclass(frozen=True, slots=True)
class FrozenRules:
    procedure_id: str
    catalog: StreamCatalog
    calibrations: Mapping[str, CalibrationSpec]
    capacity: int


def freeze_rules(procedure_id: str, draft: ProcedureDraft) -> FrozenRules:
    catalog = validate_catalog(draft.catalog.rates, draft.catalog.masks)
    calibrations = MappingProxyType(dict(draft.calibrations))
    return FrozenRules(procedure_id, catalog, calibrations, draft.capacity)

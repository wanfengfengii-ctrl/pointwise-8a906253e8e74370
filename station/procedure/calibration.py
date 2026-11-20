"""Integer calibration recorded for one acquisition stream."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CalibrationSpec:
    scale: int
    offset: int
    upper_limit: int


def validate_calibration(spec: CalibrationSpec) -> CalibrationSpec:
    if not isinstance(spec, CalibrationSpec):
        raise ValueError('each stream requires a CalibrationSpec')
    for name in ('scale', 'offset', 'upper_limit'):
        if type(getattr(spec, name)) is not int:
            raise ValueError(f'calibration {name} must be an integer')
    return spec

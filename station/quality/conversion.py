"""Exact affine conversion to integer micro-units."""

from station.procedure.calibration import CalibrationSpec


def to_micro_units(raw_code: int, calibration: CalibrationSpec) -> int:
    return calibration.scale * raw_code + calibration.offset

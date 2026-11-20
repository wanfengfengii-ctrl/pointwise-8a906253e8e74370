"""Upper-limit grading for one valid calibrated observation."""

from enum import StrEnum


class QualityLevel(StrEnum):
    WITHIN_LIMIT = 'within_limit'
    OVER_LIMIT = 'over_limit'


def classify_value(value: int, upper_limit: int) -> QualityLevel:
    if value > upper_limit:
        return QualityLevel.OVER_LIMIT
    return QualityLevel.WITHIN_LIMIT

"""Publish a stopped round or return its original publication receipt."""

from dataclasses import dataclass

from station.archive.audit import EvidenceMismatch
from station.archive.evidence import encode_evidence, freeze_evidence
from station.archive.storage import ArchiveStore, StoredResult
from station.capture.session import CaptureStation
from station.quality.report import RoundReport, build_report, decode_report, encode_report


@dataclass(frozen=True, slots=True)
class SealResult:
    result_id: int
    receipt_id: str
    report: RoundReport


def _seal_result(stored: StoredResult) -> SealResult:
    if not stored.complete:
        raise EvidenceMismatch(f'result {stored.result_id} has incomplete publication records')
    try:
        report = decode_report(stored.report_payload)
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError) as exc:
        raise EvidenceMismatch(f'result {stored.result_id} has an invalid report') from exc
    if report.round_id != stored.round_id:
        raise EvidenceMismatch(f'result {stored.result_id} has an inconsistent round id')
    return SealResult(stored.result_id, stored.receipt_id, report)


class ArchivePublisher:
    def __init__(self, station: CaptureStation, store: ArchiveStore) -> None:
        self._station = station
        self._store = store

    def seal(self, round_id: str) -> SealResult:
        existing = self._store.result_for_round(round_id)
        if existing is not None:
            return _seal_result(existing)
        stopped = self._station.stopped_round(round_id)
        report = build_report(stopped.round_id, stopped.tallies, stopped.before_tallies)
        evidence = freeze_evidence(stopped)
        stored = self._store.commit_round(round_id, encode_report(report), encode_evidence(evidence))
        return _seal_result(stored)

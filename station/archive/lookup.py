"""Read committed archive records and return evidence checked by replay."""

from dataclasses import dataclass

from station.archive.audit import AuditResult, EvidenceMismatch, audit_result
from station.archive.evidence import RoundEvidence, decode_evidence
from station.archive.storage import ArchiveStore
from station.quality.report import RoundReport, decode_report


class UnknownResult(KeyError):
    """The result number has not been published."""


@dataclass(frozen=True, slots=True)
class ReadResult:
    result_id: int
    receipt_id: str
    report: RoundReport
    evidence: RoundEvidence
    audit: AuditResult


class ArchiveReader:
    def __init__(self, store: ArchiveStore) -> None:
        self._store = store

    def read(self, result_id: int) -> ReadResult:
        stored = self._store.load_result(result_id)
        if stored is None:
            raise UnknownResult(result_id)
        if not stored.complete:
            raise EvidenceMismatch(f'result {result_id} has incomplete publication records')
        try:
            report = decode_report(stored.report_payload)
            evidence = decode_evidence(stored.evidence_payload)
        except (AttributeError, KeyError, TypeError, ValueError, RecursionError) as exc:
            raise EvidenceMismatch(f'result {result_id} contains malformed evidence or report data') from exc
        if report.round_id != stored.round_id or evidence.round_id != stored.round_id:
            raise EvidenceMismatch(f'result {result_id} contains mismatched round ids')
        try:
            audit = audit_result(report, evidence)
        except EvidenceMismatch as exc:
            raise EvidenceMismatch(f'result {result_id}: {exc}') from exc
        return ReadResult(result_id, stored.receipt_id, report, evidence, audit)

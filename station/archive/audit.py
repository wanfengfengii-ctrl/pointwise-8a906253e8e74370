"""Validate the stopped round's relationships and rebuild its quality report."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from station.archive.evidence import RoundEvidence
from station.capture.filter_table import resolve_fixed_table
from station.quality.report import RoundReport
from station.quality.tally import RoundTally, StreamTally
from station.receipt.frame import FrameVerdict, ReceivedFrame, raw_code_in_range
from station.receipt.rejections import RejectionKind


class EvidenceMismatch(ValueError):
    """A known result cannot be reconciled with its stored evidence."""


@dataclass(frozen=True, slots=True)
class AuditResult:
    round_id: str
    tallies: Mapping[str, StreamTally]


def _check_admission(evidence: RoundEvidence) -> None:
    admission = evidence.admission
    rules = admission.rules
    try:
        table = resolve_fixed_table(rules.catalog, admission.table.slots)
        rate = rules.catalog.reserved_rate(table.allowed_streams)
    except (KeyError, ValueError) as exc:
        raise EvidenceMismatch('fixed device table cannot be resolved') from exc
    if table != admission.table or not admission.subscription or not admission.subscription <= table.allowed_streams:
        raise EvidenceMismatch('fixed table and subscription disagree')
    if rate != admission.reserved_rate or rate > rules.capacity:
        raise EvidenceMismatch('stored physical rate disagrees with device capacity')
    if rules.calibrations.keys() != rules.catalog.rates.keys() or evidence.raw_min > evidence.raw_max:
        raise EvidenceMismatch('frozen calibration or raw-code interval is invalid')


def _forwarded_frames(evidence: RoundEvidence) -> list[tuple[int, str]]:
    table = evidence.admission.table
    previous = None
    forwarded = []
    for observation in evidence.observations:
        if previous is not None and observation.source_seq <= previous:
            raise EvidenceMismatch('physical observations are not in source order')
        previous = observation.source_seq
        if observation.forwarded != table.allows(observation.stream):
            raise EvidenceMismatch(f'physical forwarding disagrees at source sequence {previous}')
        if observation.forwarded:
            forwarded.append((observation.source_seq, observation.stream))
    return forwarded


def _check_rejections(evidence: RoundEvidence, firsts: Mapping[int, ReceivedFrame]) -> None:
    bad_receipts = {seq for seq, item in firsts.items() if item.verdict is FrameVerdict.BAD_CODE}
    logged_bad = set()
    previous = None
    for rejection in evidence.journal.rejections:
        seq = rejection.incoming.source_seq
        position = rejection.after_first_seq
        first = firsts.get(seq)
        if position not in firsts or (previous is not None and position < previous):
            raise EvidenceMismatch('rejection references an invalid receipt position')
        previous = position
        if rejection.kind is RejectionKind.BAD_CODE:
            if (seq not in bad_receipts or seq in logged_bad or seq != position
                    or rejection.first is not None or first.frame != rejection.incoming):
                raise EvidenceMismatch('bad-code log does not match its first receipt')
            logged_bad.add(seq)
        elif rejection.kind is RejectionKind.CONFLICT:
            if (first is None or rejection.first != first.frame
                    or first.frame == rejection.incoming or seq > position):
                raise EvidenceMismatch('conflict does not identify a distinct first receipt')
        elif first is not None or seq >= position or rejection.first is not None:
            raise EvidenceMismatch('out-of-order log contradicts the receipt sequence')
    if logged_bad != bad_receipts:
        raise EvidenceMismatch('bad-code log is missing a first receipt')


def audit_result(report: RoundReport, evidence: RoundEvidence) -> AuditResult:
    if evidence.round_id != report.round_id:
        raise EvidenceMismatch('report and evidence refer to different rounds')
    _check_admission(evidence)
    first_frames = evidence.journal.first_frames
    first_keys = [(item.frame.source_seq, item.frame.stream) for item in first_frames]
    if first_keys != _forwarded_frames(evidence):
        raise EvidenceMismatch('physical deliveries and receipt firsts disagree')

    admission = evidence.admission
    rebuilt = RoundTally(admission.subscription, admission.rules.calibrations)
    firsts = {}
    for item in first_frames:
        frame = item.frame
        firsts[frame.source_seq] = item
        if frame.stream not in admission.subscription:
            expected = FrameVerdict.FILTERED
        elif raw_code_in_range(frame, evidence.raw_min, evidence.raw_max):
            expected = FrameVerdict.ACCEPTED
            rebuilt.record_valid(frame.stream, frame.source_seq, frame.raw_code)
        else:
            expected = FrameVerdict.BAD_CODE
            rebuilt.record_bad(frame.stream)
        if item.verdict is not expected:
            raise EvidenceMismatch(f'receipt verdict disagrees at source sequence {frame.source_seq}')

    _check_rejections(evidence, firsts)
    tallies = rebuilt.snapshot()
    if tallies != report.tallies:
        streams = sorted(set(tallies) | set(report.tallies))
        different = [stream for stream in streams if tallies.get(stream) != report.tallies.get(stream)]
        raise EvidenceMismatch(f'quality totals disagree for streams {", ".join(different)}')
    return AuditResult(evidence.round_id, MappingProxyType(tallies))

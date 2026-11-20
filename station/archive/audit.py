"""Validate the stopped round's relationships and rebuild its quality report."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from station.archive.evidence import RoundEvidence
from station.capture.filter_table import FixedFilterTable, resolve_fixed_table
from station.quality.report import RoundReport
from station.quality.tally import RoundTally, SegmentLedger, StreamTally
from station.receipt.frame import FrameVerdict, ReceivedFrame, raw_code_in_range
from station.receipt.rejections import RejectionKind


class EvidenceMismatch(ValueError):
    """A known result cannot be reconciled with its stored evidence."""


@dataclass(frozen=True, slots=True)
class AuditResult:
    round_id: str
    tallies: Mapping[str, StreamTally]
    before_tallies: Mapping[str, StreamTally] | None = None


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


def _check_switch(evidence: RoundEvidence) -> tuple[list[tuple[int | None, FixedFilterTable]], int | None]:
    """Replay the ordered actions, returning each in-force table and the cut anchor."""
    switch = evidence.switch
    if switch is None:
        return [(None, evidence.admission.table)], None
    rules = evidence.admission.rules
    catalog = rules.catalog
    start_slots = evidence.admission.table.slots
    old_subscription = evidence.admission.subscription
    new_subscription = switch.subscription
    if not new_subscription:
        raise EvidenceMismatch('switch target subscription is empty')
    if not new_subscription <= catalog.rates.keys():
        raise EvidenceMismatch('switch target names an unregistered stream')

    slots = list(start_slots)
    table = evidence.admission.table
    timeline: list[tuple[int | None, FixedFilterTable]] = [(None, table)]
    cut: int | None = None
    seen_cut = False
    observed = {item.source_seq for item in evidence.observations}
    previous_anchor: int | None = None
    for action in switch.actions:
        if action.after_source_seq is not None and action.after_source_seq not in observed:
            raise EvidenceMismatch('switch action anchor is not a physically observed frame')
        if (action.after_source_seq is not None and previous_anchor is not None
                and action.after_source_seq < previous_anchor):
            raise EvidenceMismatch('switch action anchors are not in execution order')
        if action.after_source_seq is not None:
            previous_anchor = action.after_source_seq
        if action.slot is not None:
            if action.slot not in (0, 1):
                raise EvidenceMismatch('switch action refers to an unknown slot')
            if action.mask is not None and action.mask not in catalog.masks:
                raise EvidenceMismatch('switch action refers to an unknown mask')
            if action.mask == slots[action.slot]:
                raise EvidenceMismatch('switch action does not change its slot')
            slots[action.slot] = action.mask
            try:
                table = resolve_fixed_table(catalog, tuple(slots))
            except (KeyError, ValueError) as exc:
                raise EvidenceMismatch('switch action produces an invalid table') from exc
        elif action.mask is not None:
            raise EvidenceMismatch('a cutover-only action cannot set a mask')
        if action.cutover:
            if seen_cut:
                raise EvidenceMismatch('switch round has more than one cutover')
            seen_cut = True
            cut = action.after_source_seq
        subscription = new_subscription if seen_cut else old_subscription
        if not subscription <= table.allowed_streams:
            raise EvidenceMismatch('switch state does not cover the subscription in force')
        if catalog.reserved_rate(table.allowed_streams) > rules.capacity:
            raise EvidenceMismatch('switch state exceeds device capacity')
        timeline.append((action.after_source_seq, table))

    if not seen_cut:
        raise EvidenceMismatch('switch round never marks a cutover')
    try:
        final_table = resolve_fixed_table(catalog, switch.final_slots)
    except (KeyError, ValueError) as exc:
        raise EvidenceMismatch('stored final slot table cannot be resolved') from exc
    if final_table.allowed_streams != table.allowed_streams or tuple(slots) != switch.final_slots:
        raise EvidenceMismatch('stored final slot table disagrees with the executed actions')
    if cut != switch.cut_after_source_seq:
        raise EvidenceMismatch('stored cut anchor disagrees with the executed actions')
    if not new_subscription <= final_table.allowed_streams:
        raise EvidenceMismatch('final switch state does not cover the new subscription')
    return timeline, cut


def _forwarded_frames(
    evidence: RoundEvidence, timeline: list[tuple[int | None, FixedFilterTable]],
) -> list[tuple[int, str]]:
    previous = None
    forwarded = []
    for observation in evidence.observations:
        if previous is not None and observation.source_seq <= previous:
            raise EvidenceMismatch('physical observations are not in source order')
        previous = observation.source_seq
        table = timeline[0][1]
        for anchor, candidate in timeline[1:]:
            if anchor is None or anchor < observation.source_seq:
                table = candidate
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


def _subscription_at(evidence: RoundEvidence, cut: int | None, source_seq: int) -> frozenset[str]:
    switch = evidence.switch
    if switch is None:
        return evidence.admission.subscription
    if cut is None or source_seq > cut:
        return switch.subscription
    return evidence.admission.subscription


def audit_result(report: RoundReport, evidence: RoundEvidence) -> AuditResult:
    if evidence.round_id != report.round_id:
        raise EvidenceMismatch('report and evidence refer to different rounds')
    _check_admission(evidence)
    timeline, cut = _check_switch(evidence)
    first_frames = evidence.journal.first_frames
    first_keys = [(item.frame.source_seq, item.frame.stream) for item in first_frames]
    if first_keys != _forwarded_frames(evidence, timeline):
        raise EvidenceMismatch('physical deliveries and receipt firsts disagree')

    admission = evidence.admission
    switch = evidence.switch
    if switch is None:
        rebuilt = RoundTally(admission.subscription, admission.rules.calibrations)
    else:
        rebuilt = SegmentLedger(
            RoundTally(admission.subscription, admission.rules.calibrations),
            admission.rules.calibrations,
        )
    firsts = {}
    cut_done = False
    for item in first_frames:
        frame = item.frame
        firsts[frame.source_seq] = item
        if isinstance(rebuilt, SegmentLedger) and not cut_done and (
            cut is None or frame.source_seq > cut
        ):
            rebuilt.cutover(switch.subscription)
            cut_done = True
        subscription = _subscription_at(evidence, cut, frame.source_seq)
        if frame.stream not in subscription:
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
    if isinstance(rebuilt, SegmentLedger):
        if not cut_done:
            rebuilt.cutover(switch.subscription)
        before_tallies, after_tallies = rebuilt.segments()
    else:
        before_tallies, after_tallies = rebuilt.snapshot(), None
    if switch is not None and after_tallies is None:
        raise EvidenceMismatch('switch round is missing its new-segment tallies')
    tallies = after_tallies if after_tallies is not None else before_tallies
    if tallies != report.tallies:
        streams = sorted(set(tallies) | set(report.tallies))
        different = [stream for stream in streams if tallies.get(stream) != report.tallies.get(stream)]
        raise EvidenceMismatch(f'quality totals disagree for streams {", ".join(different)}')
    if switch is not None:
        if report.before_tallies is None:
            raise EvidenceMismatch('switch report is missing the old-segment tallies')
        if before_tallies != report.before_tallies:
            streams = sorted(set(before_tallies) | set(report.before_tallies))
            different = [stream for stream in streams
                         if before_tallies.get(stream) != report.before_tallies.get(stream)]
            raise EvidenceMismatch(
                f'old-segment totals disagree for streams {", ".join(different)}',
            )
    elif report.before_tallies is not None:
        raise EvidenceMismatch('fixed report must not carry old-segment tallies')
    return AuditResult(
        evidence.round_id, MappingProxyType(tallies),
        None if after_tallies is None else MappingProxyType(before_tallies),
    )

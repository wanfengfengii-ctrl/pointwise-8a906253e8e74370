"""Operator API for the lifecycle of a fixed acquisition round."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from uuid import uuid4

from station.capture.delivery import DeliveryOutcome, deliver_frame, deliver_received
from station.capture.filter_table import resolve_fixed_table
from station.capture.port import PhysicalObservation, PortStep, ReplayPort
from station.capture.recording import Recording, RecordingCatalog, SourceFrame
from station.capture.switchover import SwitchAction, SwitchRecord, plan_switch
from station.procedure.admission import FixedAdmission, admit_fixed_round, resolve_switch_target
from station.procedure.registration import ProcedureRegistry
from station.quality.tally import LiveLedger, RoundTally, SegmentLedger, StreamTally
from station.quality.tally import UnknownRound as UnknownRound
from station.receipt.ingest import ReceiptOutcome
from station.receipt.journal import JournalSnapshot, ReceiptJournal


class RoundStateError(RuntimeError):
    """The round is in the wrong lifecycle state for this operation."""


@dataclass(frozen=True, slots=True)
class StoppedRound:
    round_id: str
    recording_id: str
    admission: FixedAdmission
    raw_min: int
    raw_max: int
    observations: tuple[PhysicalObservation, ...]
    journal: JournalSnapshot
    tallies: Mapping[str, StreamTally]
    before_tallies: Mapping[str, StreamTally] | None = None
    switch: SwitchRecord | None = None


@dataclass(slots=True)
class _Round:
    recording_id: str
    recording: Recording
    admission: FixedAdmission
    port: ReplayPort
    journal: ReceiptJournal
    tally: RoundTally | SegmentLedger
    ledger: LiveLedger
    stopped: StoppedRound | None = None
    switch: SwitchRecord | None = None
    after_subscription: frozenset[str] | None = None
    switching: bool = False

    @property
    def active_subscription(self) -> frozenset[str]:
        return self.after_subscription if self.after_subscription is not None else self.admission.subscription

    def accept_step(self, step: PortStep) -> DeliveryOutcome:
        return deliver_frame(
            step, self.journal, self.tally, self.active_subscription,
            self.recording.raw_min, self.recording.raw_max,
        )

    def segments_snapshot(self) -> tuple[dict[str, StreamTally], dict[str, StreamTally] | None]:
        if isinstance(self.tally, SegmentLedger):
            return self.tally.segments()
        return self.tally.snapshot(), None


class CaptureStation:
    def __init__(self, procedures: ProcedureRegistry, recordings: RecordingCatalog) -> None:
        self.procedures = procedures
        self.recordings = recordings
        self._ledger = LiveLedger()
        self._rounds: dict[str, _Round] = {}

    @property
    def round_ids(self) -> tuple[str, ...]:
        return tuple(self._rounds)

    def open_round(
        self,
        procedure_id: str,
        subscription: Iterable[str],
        slots: Sequence[str | None],
        recording_id: str,
    ) -> str:
        draft = self.procedures.get_draft(procedure_id)
        recording = self.recordings.resolve(recording_id)
        admission = admit_fixed_round(procedure_id, draft, subscription, slots)

        round_id = uuid4().hex
        port = ReplayPort()
        port.start(recording, admission.table)
        tally = self._ledger.open(round_id, admission.subscription, admission.rules.calibrations)
        self._rounds[round_id] = _Round(
            recording_id, recording, admission, port, ReceiptJournal(), tally, self._ledger,
        )
        return round_id

    def _get_round(self, round_id: str) -> _Round:
        try:
            return self._rounds[round_id]
        except KeyError as exc:
            raise UnknownRound(round_id) from exc

    def _running_round(self, round_id: str) -> _Round:
        current = self._get_round(round_id)
        if current.stopped is not None:
            raise RoundStateError(f'round {round_id} is stopped')
        return current

    def _serial_round(self, round_id: str) -> _Round:
        """Guard entry points that must not be nested inside a switch callback."""
        current = self._running_round(round_id)
        if current.switching:
            raise RoundStateError('round is executing a device action')
        return current

    def step(self, round_id: str) -> DeliveryOutcome:
        current = self._serial_round(round_id)
        return current.accept_step(current.port.advance())

    def inject_source(self, round_id: str, frame: SourceFrame) -> DeliveryOutcome:
        current = self._running_round(round_id)
        return current.accept_step(current.port.supply(frame))

    def redeliver(self, round_id: str, frame: SourceFrame) -> ReceiptOutcome:
        current = self._running_round(round_id)
        if not isinstance(frame, SourceFrame):
            raise ValueError('redelivery requires a source frame')
        last_seq = current.journal.last_first_seq
        if last_seq is None or frame.source_seq > last_seq:
            raise ValueError('redelivery must refer to an existing or older receipt position')
        return deliver_received(
            frame, current.journal, current.tally, current.active_subscription,
            current.recording.raw_min, current.recording.raw_max,
        )

    def switch_subscription(
        self,
        round_id: str,
        subscription: Iterable[str],
        slots: Sequence[str | None],
        *,
        after_action=None,
    ) -> SwitchRecord | None:
        current = self._running_round(round_id)
        if current.switch is not None or current.switching:
            raise RoundStateError(f'round {round_id} has already switched subscription')

        rules = current.admission.rules
        requested, table = resolve_switch_target(rules, subscription, slots)
        start_slots = current.admission.table.slots
        plan = plan_switch(
            rules.catalog, rules.capacity, current.admission.subscription,
            start_slots, requested, table.slots,
        )
        if plan is None:
            return None

        current.tally = self._ledger.begin_switch(round_id, rules.calibrations)
        segments = current.tally
        current.switching = True

        actions: list[SwitchAction] = []
        cut_after: int | None = None
        slot_values = list(start_slots)
        try:
            for planned in plan:
                if planned.slot is not None:
                    slot_values[planned.slot] = planned.mask
                    current.port.replace_table(
                        resolve_fixed_table(rules.catalog, tuple(slot_values)),
                    )
                if planned.cutover:
                    cut_after = current.port.last_source_seq
                    segments.cutover(requested)
                    current.after_subscription = requested
                action = SwitchAction(
                    planned.slot, planned.mask, planned.cutover, current.port.last_source_seq,
                )
                actions.append(action)
                if after_action is not None:
                    after_action(action)
        finally:
            current.switching = False

        record = SwitchRecord(requested, table.slots, cut_after, tuple(actions))
        current.switch = record
        return record

    def live(self, round_id: str) -> dict[str, StreamTally]:
        return self._ledger.live(round_id)

    def observations(self, round_id: str) -> tuple[PhysicalObservation, ...]:
        return self._get_round(round_id).port.observations()

    def receipts(self, round_id: str) -> JournalSnapshot:
        return self._get_round(round_id).journal.snapshot()

    def recording_position(self, round_id: str) -> int:
        return self._get_round(round_id).port.recording_position

    def stop(self, round_id: str) -> StoppedRound:
        current = self._get_round(round_id)
        if current.switching:
            raise RoundStateError('round is executing a device action')
        if current.stopped is None:
            current.port.stop()
            before_tallies, after_tallies = current.segments_snapshot()
            current.stopped = StoppedRound(
                round_id, current.recording_id, current.admission,
                current.recording.raw_min, current.recording.raw_max,
                current.port.observations(), current.journal.snapshot(),
                MappingProxyType(after_tallies if after_tallies is not None else before_tallies),
                None if after_tallies is None else MappingProxyType(before_tallies),
                current.switch,
            )
        return current.stopped

    def stopped_round(self, round_id: str) -> StoppedRound:
        stopped = self._get_round(round_id).stopped
        if stopped is None:
            raise RoundStateError(f'round {round_id} is still running')
        return stopped

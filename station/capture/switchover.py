"""Plan and record an online subscription switch as ordered single-slot actions."""

from collections import deque
from dataclasses import dataclass

from station.capture.filter_table import FixedFilterTable, resolve_fixed_table
from station.procedure.catalog import StreamCatalog


@dataclass(frozen=True, slots=True)
class SwitchAction:
    """One successfully executed device action on the serial port."""

    slot: int | None
    mask: str | None
    cutover: bool
    after_source_seq: int | None


@dataclass(frozen=True, slots=True)
class SwitchRecord:
    """The frozen outcome of the round's one successful subscription switch."""

    subscription: frozenset[str]
    final_slots: tuple[str | None, str | None]
    cut_after_source_seq: int | None
    actions: tuple[SwitchAction, ...]


def _slot_values(catalog: StreamCatalog) -> tuple[str | None, ...]:
    return tuple(sorted(catalog.masks)) + (None,)


def _ordered_values(catalog: StreamCatalog, slot: str | None) -> list[str | None]:
    """Prefer slot changes that alter the allow set, then same-set renames."""
    current = catalog.mask_members(slot)
    changes = []
    renames = []
    for value in _slot_values(catalog):
        (renames if catalog.mask_members(value) == current else changes).append(value)
    return changes + renames


def _valid_table(
    catalog: StreamCatalog,
    capacity: int,
    subscription: frozenset[str],
    slots: tuple[str | None, str | None],
) -> FixedFilterTable | None:
    try:
        table = resolve_fixed_table(catalog, slots)
    except (KeyError, ValueError):
        return None
    if not subscription <= table.allowed_streams:
        return None
    if catalog.reserved_rate(table.allowed_streams) > capacity:
        return None
    return table


def plan_switch(
    catalog: StreamCatalog,
    capacity: int,
    old_subscription: frozenset[str],
    start_slots: tuple[str | None, str | None],
    new_subscription: frozenset[str],
    target_slots: tuple[str | None, str | None],
) -> tuple[SwitchAction, ...] | None:
    """Return the shortest legal device-action path, or None when unreachable.

    Every reachable state must cover the subscription in force at that moment and
    stay within the frozen capacity. Each edge changes exactly one slot, marks the
    cutover alone, or does both in one indivisible device action.
    """
    start = (start_slots[0], start_slots[1], False)
    goal = (target_slots[0], target_slots[1], True)
    if _valid_table(catalog, capacity, old_subscription, start_slots) is None:
        return None

    predecessors: dict[
        tuple[str | None, str | None, bool],
        tuple[tuple[str | None, str | None, bool], SwitchAction] | None,
    ] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            break
        first, second, done = node
        slots = (first, second)
        for slot in (0, 1):
            for value in _ordered_values(catalog, slots[slot]):
                if value == slots[slot]:
                    continue
                changed = list(slots)
                changed[slot] = value
                changed_slots = (changed[0], changed[1])
                options = (False,) if done else (False, True)
                for cutover in options:
                    post_done = done or cutover
                    subscription = new_subscription if post_done else old_subscription
                    if _valid_table(catalog, capacity, subscription, changed_slots) is None:
                        continue
                    following = (changed_slots[0], changed_slots[1], post_done)
                    if following in predecessors:
                        continue
                    predecessors[following] = (
                        node, SwitchAction(slot, value, cutover, None),
                    )
                    queue.append(following)
        if not done and _valid_table(catalog, capacity, new_subscription, slots) is not None:
            following = (first, second, True)
            if following not in predecessors:
                predecessors[following] = (node, SwitchAction(None, None, True, None))
                queue.append(following)

    if goal not in predecessors:
        return None
    plan: list[SwitchAction] = []
    node = goal
    while True:
        step = predecessors[node]
        if step is None:
            break
        previous, action = step
        plan.append(action)
        node = previous
    plan.reverse()
    return tuple(plan)

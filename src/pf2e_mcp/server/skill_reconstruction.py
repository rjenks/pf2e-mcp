"""Working out which skill was trained or increased at which level.

A Pathbuilder export records each skill's *final* rank and nothing about how it
got there -- not the level, not the grant that paid for it. The character file
needs exactly that: a `skillTraining` or `skillIncrease` per spend, so replay
can derive the ranks and a later level-up knows what is still available.

Pathbuilder only lets a rank exist if some grant paid for it, so the final
ranks are a constraint satisfaction problem over a known set of grants:

- **training slots** -- the class's free picks and the Intelligence modifier at
  1st level, a further one at each later Intelligence increase, and any
  heritage that trains "a skill of your choice";
- **increase slots** -- the class's skill-increase levels, plus feats that raise
  ranks outright ("from expert to master and ... from trained to expert") and
  a heritage that makes its chosen skill expert at a later level.

An increase to expert needs 3rd level, to master 7th, to legendary 15th, and
each step needs the one before it. This module only does the assignment; the
caller gathers the slots and writes the result into the plan.

The assignment is not unique -- a player who took Athletics at 3rd and
Acrobatics at 5th looks the same as the reverse -- so what comes back is *a*
legal history that reproduces the export, not necessarily the one played.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Lowest character level at which a skill increase can reach each rank
#: (index into trained=1, expert=2, master=3, legendary=4).
MIN_LEVEL = {2: 3, 3: 7, 4: 15}


@dataclass
class Slot:
    level: int
    #: None for the class's own; 'intelligence', or the slug of the feat or
    #: heritage that grants it.
    source: str | None = None
    #: An increase slot a feat fixes to one step ("expert to master" is 3).
    pin_target: int | None = None
    #: For a heritage's later increase: the training slot it follows.
    link: "Slot | None" = None


@dataclass
class Assignment:
    slot: Slot
    skill: str


def feat_increase_steps(description: str) -> list[int]:
    """Rank targets a feat's text raises, e.g. `[3, 2]` for Skill Mastery.

    "Increase your proficiency rank in one of your skills from expert to master
    and in another of your skills from trained to expert." is two increases,
    to master and to expert. Only the first such sentence counts: Skill
    Mastery repeats it once per class it supports.
    """
    ranks = {"trained": 1, "expert": 2, "master": 3, "legendary": 4}
    match = re.search(r"[^.]*\bIncrease your proficiency rank in\b[^.]*\.", description)
    if not match:
        return []
    return [
        ranks[after]
        for before, after in re.findall(
            r"from (trained|expert|master) to (expert|master|legendary)", match.group(0)
        )
    ]


def heritage_training(description: str) -> tuple[bool, int | None]:
    """A heritage that trains one chosen skill, and the level it becomes expert.

    Skilled Human: "You become trained in one skill of your choice. At 5th
    level, you become an expert in the chosen skill."
    """
    if not re.search(r"trained in (?:one|a) skill of your choice", description, re.IGNORECASE):
        return False, None
    later = re.search(
        r"At (\d+)(?:st|nd|rd|th) level,? you become an expert in the chosen skill",
        description,
        re.IGNORECASE,
    )
    return True, int(later.group(1)) if later else None


def solve(
    final: dict[str, int],
    derived: dict[str, int],
    trained_at: dict[str, int],
    training_slots: list[Slot],
    increase_slots: list[Slot],
) -> tuple[list[Assignment], list[Assignment], dict[str, int]]:
    """Assign each missing training and rank step to a slot.

    A heritage that trains a chosen skill and later makes it expert ties one
    skill to two slots, and which skill is a real decision (it can strand
    another skill's chain), so every candidate is tried and the one that leaves
    the least unpaid wins.
    """
    needs = [s for s, want in final.items() if want >= 2 and want > derived.get(s, 0) and derived.get(s, 0) == 0]
    has_heritage = any(
        s.source and s.source != "intelligence" and _linked(s, increase_slots)
        for s in training_slots
    )
    options: list[str | None] = sorted(needs) if has_heritage and needs else [None]
    best = None
    for choice in options:
        result = _solve_once(final, derived, dict(trained_at), training_slots, increase_slots, choice)
        if best is None or sum(result[2].values()) < sum(best[2].values()):
            best = result
        if not result[2]:
            break
    return best


def _solve_once(
    final: dict[str, int],
    derived: dict[str, int],
    trained_at: dict[str, int],
    training_slots: list[Slot],
    increase_slots: list[Slot],
    heritage_pick: str | None,
) -> tuple[list[Assignment], list[Assignment], dict[str, int]]:
    """One assignment, with the heritage's chosen skill fixed to `heritage_pick`.

    `final` and `derived` are rank indices (0 untrained .. 4 legendary) per
    skill. `trained_at` is the level an already-derived skill was trained at.
    Returns the training assignments, the increase assignments, and the rank
    each skill is still short by -- anything the slots could not pay for.
    """
    steps: dict[str, list[int]] = {}
    needs_training: list[str] = []
    for skill, want in final.items():
        have = derived.get(skill, 0)
        if want <= have:
            continue
        if have == 0:
            needs_training.append(skill)
        steps[skill] = list(range(max(have, 1) + 1, want + 1))

    trainings: list[Assignment] = []
    free = sorted(training_slots, key=lambda s: s.level)
    remaining = list(needs_training)

    # A heritage's skill must reach expert later, so it has to be one that does.
    for slot in [s for s in free if s.source and s.source != "intelligence" and _linked(s, increase_slots)]:
        if heritage_pick not in remaining:
            continue
        pick = heritage_pick
        trainings.append(Assignment(slot, pick))
        remaining.remove(pick)
        free.remove(slot)
        trained_at[pick] = slot.level

    # Skills with the most steps go to the earliest slots: they have the most
    # to fit in afterwards.
    remaining.sort(key=lambda s: (-len(steps.get(s, [])), s))
    for slot in list(free):
        if not remaining:
            break
        pick = remaining.pop(0)
        trainings.append(Assignment(slot, pick))
        trained_at[pick] = slot.level
        free.remove(slot)

    # Increases, highest rank first so the late-only steps get the late slots.
    heritage_skill = {id(a.slot): a.skill for a in trainings}
    todo = sorted(
        ((skill, r) for skill, rs in steps.items() for r in rs if skill not in remaining),
        key=lambda t: (-t[1], t[0]),
    )
    def legal_slots(skill: str, rank: int, slots: list[Slot], placed_: dict) -> list[Slot]:
        ceiling = placed_.get((skill, rank + 1), 99)
        legal = [
            s for s in slots
            if s.level >= MIN_LEVEL.get(rank, 1)
            and s.level > trained_at.get(skill, 0)
            and s.level < ceiling
            and s.pin_target in (None, rank)
            and (s.link is None or heritage_skill.get(id(s.link)) == skill)
        ]
        # Slots that only fit one kind of step first, then the latest.
        return sorted(legal, key=lambda s: (0 if s.link else 1 if s.pin_target else 2, -s.level))

    # Depth-first with backtracking: a greedy "latest slot" choice can strand a
    # lower step of the same skill (Nature trained -> master needs an early
    # slot for its expert step once the master step has taken a late one).
    # The problem is a dozen steps, so exhaustive search is cheap; when no
    # complete assignment exists the best partial one is kept.
    best: list[tuple[str, int, Slot]] = []
    budget = [20000]

    def search(index: int, slots: list[Slot], placed_: dict, chosen: list, skipping: bool) -> bool:
        nonlocal best
        budget[0] -= 1
        if len(chosen) > len(best):
            best = list(chosen)
        if index == len(todo):
            return len(chosen) == len(todo)
        if budget[0] < 0:
            return False
        skill, rank = todo[index]
        for slot in legal_slots(skill, rank, slots, placed_):
            placed_[(skill, rank)] = slot.level
            chosen.append((skill, rank, slot))
            if search(index + 1, [s for s in slots if s is not slot], placed_, chosen, skipping):
                return True
            chosen.pop()
            del placed_[(skill, rank)]
        # With no complete assignment to be had, leave this step unpaid and
        # carry on, keeping the largest partial assignment.
        return skipping and search(index + 1, slots, placed_, chosen, skipping)

    if not search(0, list(increase_slots), {}, [], False):
        best = []
        search(0, list(increase_slots), {}, [], True)
    placed = {(skill, rank): slot.level for skill, rank, slot in best}
    increases = [Assignment(slot, skill) for skill, rank, slot in best]

    shortfall: dict[str, int] = {}
    for skill, rs in steps.items():
        got = sum(1 for r in rs if (skill, r) in placed) if skill not in remaining else 0
        missing = len(rs) - got + (1 if skill in remaining else 0)
        if missing:
            shortfall[skill] = missing
    return trainings, increases, shortfall


def _linked(slot: Slot, increase_slots: list[Slot]) -> bool:
    return any(s.link is slot for s in increase_slots)


def describe(assignments: list[Assignment]) -> list[dict[str, Any]]:
    """Plain rows, for tests and import notes."""
    return [{"level": a.slot.level, "skill": a.skill, "source": a.slot.source} for a in assignments]

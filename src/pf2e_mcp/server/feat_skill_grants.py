"""What skill training a feat hands out, read from the feat's own text.

Nearly every archetype dedication trains a skill, and almost none of them say
so in a rule element: Foundry carries "You become trained in Stealth or
Thievery plus one skill of your choice" as prose (Rogue Dedication has a
ChoiceSet for the Stealth/Thievery half and nothing for the rest; Dandy
Dedication has nothing at all). The ingestion therefore has no skill rows for
feats, and replay used to drop every one of these grants -- an imported build
came back with a pile of `proficiencyOverrides` for skills a dedication had
trained.

The phrasing is formulaic, so this reads it. A grant is one of:

- `fixed`   -- "trained in Arcana". Always applies; `if_trained` says what
  happens when the character already was: `"expert"` ("you become an expert in
  it instead"), `"free"` ("you instead become trained in a skill of your
  choice"), or `None`.
- `choice`  -- "your choice of Acrobatics or Athletics". Needs a pick from
  `options`; `if_trained` as above for when every option is already known.
- `free`    -- "a skill of your choice". Needs a pick from anything.

Only `fixed` can be applied without the character file saying more; `choice`
and `free` are the player's decision and live in the plan as a
`skillTraining` choice with `grantedBy` naming the feat. Grants that hang off
a subclass the text does not name ("your deity's associated skill") are
counted in `unresolved` and otherwise left alone, as are Lores: they are
handled by their own machinery and this reads skills only.
"""

from __future__ import annotations

import re
from typing import Any

SKILL_NAMES = (
    "acrobatics", "arcana", "athletics", "crafting", "deception", "diplomacy",
    "intimidation", "medicine", "nature", "occultism", "performance", "religion",
    "society", "stealth", "survival", "thievery",
)
_SKILL_RE = re.compile(r"\b(" + "|".join(SKILL_NAMES) + r")\b(?!\s+Lore)", re.IGNORECASE)

#: Words that mean "the skill depends on a subclass choice this text can't see".
_SUBCLASS_WORDS = re.compile(
    r"associated|bloodline|deity|order|patron|mystery|eidolon|apparition|"
    r"\bway\b|\bstyle\b|\bthat skill\b|\bthose skills\b",
    re.IGNORECASE,
)

_FREE_MARKER = re.compile(
    r"(?:(?:plus|and|,)\s+)?(?:one|another|an additional|a)?\s*(?:other\s+|additional\s+)?"
    r"skill of your choice",
    re.IGNORECASE,
)


def clean(description: str) -> str:
    """Feat description HTML as plain sentences."""
    text = re.sub(r"<[^>]+>", " ", description or "")
    # @UUID[...]{Label} / [[/act ...]]{Label} -> Label; bare ones vanish.
    text = re.sub(r"@\w+\[[^\]]*\]\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\[\[[^\]]*\]\]\{([^}]*)\}", r"\1", text)
    text = re.sub(r"@\w+\[[^\]]*\]|\[\[[^\]]*\]\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _skills_in(text: str) -> list[str]:
    seen: list[str] = []
    for match in _SKILL_RE.finditer(text):
        name = match.group(1).lower()
        if name not in seen:
            seen.append(name)
    return seen


def _if_trained(window: str) -> str | None:
    """What the text says happens when the granted skill was already known."""
    match = re.search(r"already (?:trained|an expert)|were already|are already", window, re.IGNORECASE)
    if not match:
        return None
    after = window[match.start():]
    if re.search(r"skill of your choice|another skill|additional skill", after, re.IGNORECASE):
        return "free"
    if re.search(r"\bexpert\b", after, re.IGNORECASE):
        return "expert"
    return None


def parse(description: str) -> dict[str, Any]:
    """Skill grants in a feat's description.

    Returns `{"grants": [...], "unresolved": n}`. `grants` is empty for a feat
    that trains no skill (or whose skill depends entirely on a subclass).
    """
    text = clean(description)
    grants: list[dict[str, Any]] = []
    unresolved = 0

    for match in re.finditer(r"\b(?:you )?(?:become|are) trained in\b", text, re.IGNORECASE):
        # "...if you were already trained in X, you instead become trained in a
        # skill of your choice" restates the grant above as its fallback; it is
        # read as `if_trained` there and is not a grant of its own.
        sentence_start = max(text.rfind(".", 0, match.start()), text.rfind(";", 0, match.start())) + 1
        if re.search(r"already|instead", text[sentence_start:match.start()], re.IGNORECASE):
            continue
        start = match.end()
        end = text.find(".", start)
        end = len(text) if end == -1 else end
        # A trailing "; if you were already trained..." belongs to the same grant,
        # and so does an immediately following "If you were already trained..."
        # sentence.
        clause_end = text.find(";", start)
        clause_end = end if clause_end == -1 or clause_end > end else clause_end
        clause = text[start:clause_end]
        # "..., if you are already trained in both, you instead..." is the
        # fallback, not part of what is granted.
        comma_if = re.search(r",\s+if (?:you|they)\b", clause, re.IGNORECASE)
        if comma_if:
            clause = clause[: comma_if.start()]
        window = text[start:end]
        following = text[end + 1:].lstrip()
        if re.match(r"if (?:you|they)\b", following, re.IGNORECASE):
            sentence_end = following.find(".")
            window += " " + following[: sentence_end if sentence_end != -1 else len(following)]

        if _SUBCLASS_WORDS.search(clause) and not _skills_in(clause):
            unresolved += 1
            continue

        # A grant that can also raise an existing skill ("trained in Astronomy
        # Lore or an expert in Occultism") is not a plain training grant.
        if re.search(r"\bexpert in\b", clause, re.IGNORECASE):
            continue

        free_match = _FREE_MARKER.search(clause)
        # "a Lore skill of your choice" is a Lore, which this does not read.
        if free_match and re.search(r"Lore\s+skill of your choice", clause, re.IGNORECASE):
            continue
        head = clause[: free_match.start()] if free_match else clause
        # "your choice of Acrobatics or Athletics" -> the same list, minus filler.
        head = re.sub(r"\byour choice of\b", "", head, flags=re.IGNORECASE)
        skills = _skills_in(head)
        has_subclass_part = bool(_SUBCLASS_WORDS.search(head))
        if has_subclass_part:
            unresolved += 1
        fallback = _if_trained(window)

        if skills:
            if re.search(r"\bor\b", head, re.IGNORECASE):
                # "Acrobatics or the skill associated with your style": one of
                # these two, and one of them is unknowable from here.
                if has_subclass_part:
                    grants.append({"kind": "free", "options": None, "if_trained": None})
                    unresolved -= 1
                else:
                    grants.append({"kind": "choice", "options": skills, "if_trained": fallback})
            else:
                for skill in skills:
                    grants.append(
                        {"kind": "fixed", "options": [skill], "if_trained": fallback}
                    )
        if free_match:
            grants.append({"kind": "free", "options": None, "if_trained": None})

    return {"grants": grants, "unresolved": unresolved}

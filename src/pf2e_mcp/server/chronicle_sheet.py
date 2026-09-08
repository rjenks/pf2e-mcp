"""Render a character's Organized Play record as a printable HTML document.

This is the chronicle-log counterpart to `sheet.render_character_sheet`, and it
deliberately borrows that module's stylesheet, fonts, spiral section mark and
print plumbing wholesale rather than growing a second look. A player who prints
both should get two documents that read as one set, and the CSS that makes the
character sheet print cleanly -- the `@page` size, the toolbar that disappears,
the break-inside rules -- is exactly the CSS this needs.

What it adds is the shape of a ledger. A stack of chronicle sheets is not a list
of independent sessions; it is a running account, and the useful document is the
one that shows both the individual entries and what they add up to. So the first
page is the summary a player actually needs at a table -- current level, gold on
hand, Reputation with each faction, downtime banked, and what has already been
played (which is what determines whether a given scenario is still available).
Each chronicle then gets its own page, in the order it was applied, with its
starting values shown alongside its ending ones so the chain is visible.

Validation findings are rendered inline rather than being left in tool output,
because the person who needs to see that chronicle 7's starting gold does not
match chronicle 6's ending gold is the person holding the printout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import chronicle as ch
from .db import get_connection
from .sheet import (
    _LOGO_INK_HEIGHT,
    _PAPER,
    _esc,
    _foot,
    _logo_data_uri,
    _png_alpha_bounds,
    _section,
    _stylesheet,
)

# Chronicle-specific styling, appended to the character sheet's stylesheet so
# both documents share one set of variables, fonts and print rules.
_CHRONICLE_CSS = r"""
/* Provenance strip at the head of each chronicle: the event/date/GM block. */
.prov{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--rule);
  background:var(--warm);margin:0 0 10px;}
.prov .f{padding:4px 7px 5px;border-right:1px solid var(--hair);}
.prov .f:last-child{border-right:0;}
.prov .v{font-size:8.4pt;font-weight:620;line-height:1.25;margin-top:1px;}
.prov .v.none{color:var(--faint);font-weight:400;}

/* The two ledgers sit side by side; each is start -> movement -> end. */
.ledgers{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:11px;}
.ledger{border:1px solid var(--rule);}
.ledger h3{font-family:'SheetSans',sans-serif;font-size:6.4pt;letter-spacing:.09em;
  text-transform:uppercase;color:var(--paper);background:var(--accent);
  padding:3px 7px;font-weight:700;}
table.led{width:100%;border-collapse:collapse;}
table.led td{padding:2.4px 7px;font-size:8.4pt;border-bottom:.6px dotted var(--hair);}
table.led tr:last-child td{border-bottom:0;}
table.led td.r{text-align:right;font-weight:640;white-space:nowrap;}
table.led tr.tot td{border-top:1.1px solid var(--rule);font-weight:750;
  background:var(--accent-soft);}
table.led tr.sub td{color:var(--muted);font-size:7.4pt;padding-left:16px;}
table.led tr.sub td.r{font-weight:500;}

/* Reputation, checkboxes and downtime share a row of small panels. */
.panels{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:11px;}
.panel{border:1px solid var(--rule);padding:5px 8px 6px;}
.panel .lbl{margin-bottom:3px;}
.rep-line{display:flex;justify-content:space-between;font-size:8.4pt;
  padding:1.4px 0;border-bottom:.6px dotted var(--hair);}
.rep-line:last-child{border-bottom:0;}
.rep-line b{font-weight:700;}
.big{font-size:15pt;font-weight:700;line-height:1.1;color:var(--accent);}
.boxes{display:flex;flex-wrap:wrap;gap:4px;margin-top:2px;}
.box{width:15px;height:15px;border:1.1px solid var(--rule);display:flex;
  align-items:center;justify-content:center;font-size:7.4pt;font-weight:700;
  color:var(--faint);}
.box.on{background:var(--accent);border-color:var(--accent);color:var(--paper);}

/* Item access: purchased lines are struck through, as on the paper sheet. */
table.items{width:100%;border-collapse:collapse;margin-bottom:10px;}
table.items th{font-family:'SheetSans',sans-serif;font-size:5.9pt;font-weight:650;
  letter-spacing:.085em;text-transform:uppercase;color:var(--muted);
  text-align:left;padding:0 6px 2px 0;border-bottom:1px solid var(--rule);}
table.items td{padding:2.4px 6px 2.4px 0;font-size:8.3pt;
  border-bottom:.6px dotted var(--hair);vertical-align:top;}
table.items td.lv{width:34px;text-align:center;}
table.items td.pr{width:74px;text-align:right;white-space:nowrap;}
table.items tr.bought td{color:var(--faint);text-decoration:line-through;}

.boon{border-left:2.4px solid var(--accent-line);padding:2px 0 3px 8px;
  margin-bottom:6px;break-inside:avoid;}
.boon .bn{font-size:8.8pt;font-weight:680;}
.boon .bd{font-size:8pt;color:var(--ink-soft);line-height:1.36;}
.boon.spent .bn{color:var(--muted);text-decoration:line-through;}

/* Played-adventures table on the summary page. */
table.played{width:100%;border-collapse:collapse;}
table.played th{font-family:'SheetSans',sans-serif;font-size:5.9pt;font-weight:650;
  letter-spacing:.085em;text-transform:uppercase;color:var(--muted);
  text-align:left;padding:0 6px 2px 0;border-bottom:1px solid var(--rule);}
table.played td{padding:2.6px 6px 2.6px 0;font-size:8.4pt;
  border-bottom:.6px dotted var(--hair);}
table.played td.c{width:52px;font-weight:700;white-space:nowrap;}
table.played td.n{width:34px;text-align:center;color:var(--muted);}
table.played td.d{width:74px;color:var(--muted);white-space:nowrap;}
table.played .gm{font-family:'SheetSans',sans-serif;font-size:5.6pt;font-weight:700;
  letter-spacing:.07em;color:var(--accent);border:1px solid var(--accent-line);
  padding:0 3px;border-radius:2px;}

/* Findings. Errors are contradictions; warnings are deviations. */
.finding{font-size:8pt;line-height:1.4;padding:3px 8px;margin-bottom:3px;
  border-left:2.4px solid var(--rule);background:var(--warm);break-inside:avoid;}
.finding.error{border-left-color:var(--hp);background:var(--hp-soft);}
.finding .who{font-family:'SheetSans',sans-serif;font-size:5.8pt;font-weight:700;
  letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
  margin-right:5px;}
.finding.error .who{color:var(--hp);}

.chron-head{display:flex;align-items:baseline;gap:9px;margin-bottom:2px;}
.chron-head .code{font-size:15pt;font-weight:750;color:var(--accent);
  line-height:1.05;}
.chron-head .ttl{font-size:13pt;font-weight:620;line-height:1.1;}
.chron-sub{font-family:'SheetSans',sans-serif;font-size:6.4pt;letter-spacing:.05em;
  text-transform:uppercase;color:var(--muted);margin-bottom:9px;}
.nameplate{display:flex;align-items:flex-end;justify-content:space-between;
  gap:14px;border-bottom:1.6px solid var(--accent);padding-bottom:5px;
  margin-bottom:11px;}
.nameplate h1{font-size:23pt;letter-spacing:.01em;line-height:1;}
.nameplate .who{font-family:'SheetSans',sans-serif;font-size:6.6pt;
  letter-spacing:.07em;text-transform:uppercase;color:var(--muted);
  margin-top:3px;}
/* The gold journal: one running column of every coin in and out. The balance
   column is the one a player reads, so it is the only bold one. */
table.journal{width:100%;border-collapse:collapse;}
table.journal th{font-family:'SheetSans',sans-serif;font-size:5.9pt;font-weight:650;
  letter-spacing:.085em;text-transform:uppercase;color:var(--muted);
  text-align:left;padding:0 6px 2px 0;border-bottom:1px solid var(--rule);}
table.journal th.num,table.journal td.num{text-align:right;padding-right:0;
  width:76px;white-space:nowrap;}
table.journal th.bal,table.journal td.bal{text-align:right;padding-right:0;
  width:96px;white-space:nowrap;}
table.journal td{padding:2.5px 6px 2.5px 0;font-size:8.4pt;
  border-bottom:.6px dotted var(--hair);vertical-align:top;}
table.journal td.bal{font-weight:700;background:var(--accent-soft);
  padding-left:7px;}
table.journal td.lv{width:30px;text-align:center;color:var(--muted);font-size:7.4pt;}
table.journal td.dt{width:70px;color:var(--muted);white-space:nowrap;font-size:7.6pt;}
table.journal td.in{color:var(--accent);font-weight:640;}
table.journal td.out{color:var(--ink-soft);}
table.journal tr.opening td{background:var(--warm);font-weight:640;}
table.journal tr.award td{border-top:.9px solid var(--hair);}
table.journal .sub{display:block;font-size:7.2pt;color:var(--muted);
  font-style:italic;line-height:1.3;}
table.journal tr.final td{border-top:1.4px solid var(--accent);
  border-bottom:0;font-weight:750;padding-top:4px;}

/* Fill-in rows. A chronicle arrives by email a day after the session, so at a
   convention a player is several games ahead of their records -- these are for
   writing the next sessions in by hand and transcribing them later. Taller than
   sheet.py's 21px blank rows: that height is sized for a short number in a
   narrow column, and these have to take an adventure title in ink. Column rules
   are carried through the blanks so handwriting stays in its column. */
table.played tr.fill td,table.journal tr.fill td{
  height:26px;border-bottom:.7px solid var(--hair);}
table.played tr.fill td + td,table.journal tr.fill td + td{
  border-left:.6px solid var(--hair);}
table.journal tr.fill td.bal{background:var(--accent-soft);}
table.journal tr.carried td{border-top:1.4px solid var(--accent);
  border-bottom:0;font-weight:750;padding-top:4px;padding-bottom:5px;}

.notes-txt{font-size:8.5pt;line-height:1.45;white-space:pre-wrap;}
.empty{color:var(--faint);font-style:italic;font-size:8.2pt;}
"""

_OPTIONAL_SECTIONS = [
    ("journal", "Gold journal"),
    ("findings", "Validation"),
    ("chronicles", "Chronicles"),
    ("attribution", "Notices"),
]

_TOGGLE_SCRIPT = """
<script>
document.querySelectorAll('.toolbar input[type=checkbox]').forEach(function (box) {
  box.addEventListener('change', function () {
    document.querySelectorAll('[data-sec="' + box.value + '"]').forEach(
      function (page) { page.classList.toggle('off', !box.checked); });
  });
});
</script>
"""


def _toolbar(sections: list[str]) -> str:
    picks = "".join(
        f'<label><input type="checkbox" checked value="{key}">{label}</label>'
        for key, label in _OPTIONAL_SECTIONS
        if key in sections
    )
    if picks:
        picks = f'<div class="picks"><b>Print:</b>{picks}</div>'
    return f"""
<div class="toolbar">
  <button onclick="window.print()">Print / Save as PDF</button>
  <span class="tip">Enable &ldquo;background graphics&rdquo; in the print dialog
    so the tinted panels come through.</span>
  {picks}
</div>
"""


def _field(label: str, value: Any) -> str:
    """One labelled cell in a provenance strip, greyed when unrecorded."""
    if value in (None, "", []):
        return (f'<div class="f"><div class="lbl">{_esc(label)}</div>'
                f'<div class="v none">&mdash;</div></div>')
    return (f'<div class="f"><div class="lbl">{_esc(label)}</div>'
            f'<div class="v">{_esc(value)}</div></div>')


def _findings_html(issues: list[dict[str, Any]], index: int | None) -> str:
    """Render the findings attached to one chronicle, or (index=None) the
    file-level ones that belong to no single chronicle."""
    mine = [i for i in issues if i.get("chronicle") == index]
    if not mine:
        return ""
    return "".join(
        f'<div class="finding {i["level"]}"><span class="who">{i["level"]}</span>'
        f'{_esc(i["message"])}</div>'
        for i in mine
    )


def _character_number(log: dict[str, Any]) -> str | None:
    """The '123456-2001' identifier printed on every chronicle."""
    op_id, number = log.get("organizedPlayId"), log.get("characterNumber")
    if op_id and number:
        return f"{op_id}-{number}"
    return op_id or (str(number) if number else None)


def _page_summary(
    log: dict[str, Any],
    result: dict[str, Any],
    adventures: list[dict[str, Any] | None],
    logo_html: str,
    blank_rows: int = 6,
) -> str:
    derived = result["derived"]
    entries = log.get("chronicles") or []

    reputation = derived["reputation"]
    # Always show the character's own faction, even at zero -- its absence from
    # the list is information, not a reason to omit the row.
    own = log.get("faction")
    if own and own not in reputation:
        reputation = {own: 0, **reputation}
    rep_html = "".join(
        f'<div class="rep-line"><span>{_esc(faction)}</span><b>{amount:g}</b></div>'
        for faction, amount in reputation.items()
    ) or '<div class="empty">No Reputation recorded.</div>'

    xp_to_next = derived["xp_to_next_level"]
    next_html = (
        "Retired at 20th" if xp_to_next is None
        else f"{xp_to_next:g} XP to level {derived['level'] + 1}"
    )

    rows = ""
    for i, (entry, adventure) in enumerate(zip(entries, adventures)):
        name = (adventure or {}).get("name") or entry.get("adventureName") or "&mdash;"
        badge = ' <span class="gm">GM</span>' if entry.get("playedAs") == "gm" else ""
        replay = ' <span class="gm">REPLAY</span>' if entry.get("replay") else ""
        rows += (
            f'<tr><td class="n">{i + 1}</td>'
            f'<td class="c">{_esc(ch.normalize_code(entry.get("adventure", "")))}</td>'
            f"<td>{_esc(name) if adventure else name}{badge}{replay}</td>"
            f'<td class="n">{_esc(entry.get("characterLevel") or "")}</td>'
            f'<td class="d">{_esc(entry.get("date") or "")}</td></tr>'
        )
    # The sequence number is the one column that can be known in advance, so it
    # is printed into the blanks rather than left to be hand-counted down a page
    # of ruled lines.
    fills = "".join(
        f'<tr class="fill"><td class="n">{len(entries) + n}</td>'
        '<td class="c"></td><td></td><td class="n"></td><td class="d"></td></tr>'
        for n in range(1, max(0, blank_rows) + 1)
    )
    played = (
        '<table class="played"><tr><th></th><th>Code</th><th>Adventure</th>'
        f"<th>Lvl</th><th>Date</th></tr>{rows}{fills}</table>"
        if rows or fills
        else '<div class="empty">No chronicles recorded yet.</div>'
    )

    who = " &middot; ".join(
        _esc(part) for part in (
            _character_number(log),
            log.get("faction"),
            "Slow advancement" if derived["advancement"] == "slow" else None,
        ) if part
    )

    gm = derived["gm_credits"]
    return f"""
<section class="page" data-sec="summary">
  <div class="nameplate">
    <div><h1>{_esc(log.get("character") or "Unnamed")}</h1>
      <div class="who">{who or "Pathfinder Society record"}</div></div>
    {logo_html}
  </div>

  {_section("Organized Play record", f"{derived['chronicles']} chronicle"
            f"{'s' if derived['chronicles'] != 1 else ''}"
            + (f" &middot; {gm} run as GM" if gm else ""))}
  <div class="panels">
    <div class="panel"><div class="lbl">Level</div>
      <div class="big">{derived["level"]}</div>
      <div class="lbl" style="margin-top:3px">{derived["total_xp"]:g} XP total</div>
      <div class="lbl">{next_html}</div></div>
    <div class="panel"><div class="lbl">Currency on hand</div>
      <div class="big">{_esc(derived["currency"])}</div>
      <div class="lbl" style="margin-top:3px">
        {derived["income_earned"]} earned as income</div>
      <div class="lbl">last unit {(f"{derived['downtime_days_last_unit']:g} downtime days"
        if derived["downtime_days_last_unit"] is not None else "&mdash;")} &middot; not
        accruable</div></div>
    <div class="panel"><div class="lbl">Reputation</div>{rep_html}</div>
  </div>

  {_section("Adventures played")}
  {played}
  {_foot(log.get("character") or "", "Organized Play record")}
</section>
"""


def _page_journal(log: dict[str, Any], result: dict[str, Any], blank_rows: int = 12) -> str:
    """The gold journal: every transaction in order, with a running balance.

    This is the page a player audits against. A chronicle's currency block says
    what one session did, but a stack of chronicles hides every purchase inside
    a lump `spent` figure -- so the question "where did my money go" has no
    answer until all of it is flattened into one column.
    """
    journal = result.get("journal") or []
    if not journal:
        return ""

    rows = ""
    for row in journal:
        note = row.get("note")
        sub = f'<span class="sub">{_esc(note)}</span>' if note else ""
        level = row.get("level")
        rows += (
            f'<tr class="{_esc(row["kind"])}">'
            f'<td class="dt">{_esc(row.get("date") or "")}</td>'
            f'<td class="lv">{_esc(level if level is not None else "")}</td>'
            f'<td>{_esc(row["description"])}{sub}</td>'
            f'<td class="num in">'
            f'{_esc(ch.format_currency(row["in_cp"])) if row["in_cp"] else ""}</td>'
            f'<td class="num out">'
            f'{("&minus;" + _esc(ch.format_currency(row["out_cp"]))) if row["out_cp"] else ""}</td>'
            f'<td class="bal">{_esc(row["balance"])}</td></tr>'
        )

    total_in = sum(r["in_cp"] for r in journal)
    total_out = sum(r["out_cp"] for r in journal)
    # With blanks below, this is the figure a handwritten continuation starts
    # from, so it is labelled as carried forward rather than as a final total --
    # anything written under it supersedes it.
    label = "Balance carried forward" if blank_rows > 0 else "Balance on hand"
    rows += (
        f'<tr class="{"carried" if blank_rows > 0 else "final"}">'
        '<td class="dt"></td><td class="lv"></td>'
        f"<td>{label}</td>"
        f'<td class="num in">{_esc(ch.format_currency(total_in))}</td>'
        f'<td class="num out">&minus;{_esc(ch.format_currency(total_out))}</td>'
        f'<td class="bal">{_esc(journal[-1]["balance"])}</td></tr>'
    )
    rows += ('<tr class="fill"><td class="dt"></td><td class="lv"></td><td></td>'
             '<td class="num"></td><td class="num"></td>'
             '<td class="bal"></td></tr>') * max(0, blank_rows)

    return f"""
<section class="page" data-sec="journal">
  {_section("Gold journal", f"{len(journal)} entries recorded" + (" &middot; blanks to continue by hand" if blank_rows else ""))}
  <table class="journal">
    <tr><th>Date</th><th>Lvl</th><th>Transaction</th>
        <th class="num">In</th><th class="num">Out</th><th class="bal">Balance</th></tr>
    {rows}
  </table>
  {_starting_items_html(log)}
  {_foot(log.get("character") or "", "Gold journal")}
</section>
"""


def _starting_items_html(log: dict[str, Any]) -> str:
    """Permanent items granted at creation cost nothing, so they never appear as
    a journal line -- but leaving them off entirely would make the character's
    gear unexplainable from the record."""
    items = log.get("startingItems") or []
    if not items:
        return ""
    lines = "".join(f"<div>{_esc(item)}</div>" for item in items)
    return (
        '<div class="lbl" style="margin-top:11px">Permanent items granted at creation '
        "(no cost)</div>"
        f'<div class="notes-txt">{lines}</div>'
    )


def _page_findings(result: dict[str, Any]) -> str:
    issues = result["issues"]
    if not issues:
        return ""
    body = _findings_html(issues, None)
    for i in sorted({x["chronicle"] for x in issues if x["chronicle"] is not None}):
        body += f'<div class="lbl" style="margin-top:7px">Chronicle {i + 1}</div>'
        body += _findings_html(issues, i)
    note = f"{result['errors']} error{'s' if result['errors'] != 1 else ''}, " \
           f"{result['warnings']} warning{'s' if result['warnings'] != 1 else ''}"
    return f"""
<section class="page" data-sec="findings">
  {_section("Validation", note)}
  {body}
  <div class="empty" style="margin-top:9px">{_esc(result["caveat"])}</div>
</section>
"""


def _ledger_tables(entry: dict[str, Any]) -> str:
    xp = entry.get("xp") or {}
    money = entry.get("currency") or {}

    def cell(value: Any, fmt_money: bool = False) -> str:
        if value in (None, ""):
            return "&mdash;"
        return _esc(ch.format_currency(ch.to_cp(value)) if fmt_money else f"{value:g}")

    sub_rows = ""
    bundles = money.get("treasureBundles")
    if money.get("treasureBundleValue") is not None or bundles is not None:
        label = "Treasure bundles"
        if bundles is not None:
            label += f" (&times;{bundles:g})"
        sub_rows += (f'<tr class="sub"><td>{label}</td>'
                     f'<td class="r">{cell(money.get("treasureBundleValue"), True)}</td></tr>')
    if money.get("incomeEarned") is not None:
        sub_rows += ('<tr class="sub"><td>Earn Income</td>'
                     f'<td class="r">{cell(money.get("incomeEarned"), True)}</td></tr>')

    return f"""
<div class="ledgers">
  <div class="ledger"><h3>Experience</h3>
    <table class="led">
      <tr><td>Starting XP</td><td class="r">{cell(xp.get("start"))}</td></tr>
      <tr><td>XP gained</td><td class="r">{cell(xp.get("gained"))}</td></tr>
      <tr class="tot"><td>Total XP</td><td class="r">{cell(xp.get("end"))}</td></tr>
    </table>
  </div>
  <div class="ledger"><h3>Currency</h3>
    <table class="led">
      <tr><td>Starting</td><td class="r">{cell(money.get("start"), True)}</td></tr>
      {sub_rows}
      <tr><td>Gained</td><td class="r">{cell(money.get("gained"), True)}</td></tr>
      <tr><td>Spent</td><td class="r">{cell(money.get("spent"), True)}</td></tr>
      <tr class="tot"><td>Total</td><td class="r">{cell(money.get("end"), True)}</td></tr>
    </table>
  </div>
</div>
"""


def _page_chronicle(
    entry: dict[str, Any],
    adventure: dict[str, Any] | None,
    index: int,
    log: dict[str, Any],
    result: dict[str, Any],
) -> str:
    code = ch.normalize_code(entry.get("adventure", ""))
    name = (adventure or {}).get("name") or entry.get("adventureName") or "Unknown adventure"

    bits = []
    if adventure:
        bits.append(adventure["kind"].capitalize())
        bits.append(f"Tier {adventure['tier']}")
        if adventure.get("series"):
            bits.append(adventure["series"])
        bits += adventure.get("tags", [])
    if entry.get("playedAs") == "gm":
        bits.append("GM credit")
    if entry.get("replay"):
        bits.append("Replay")

    rep = entry.get("reputation") or []
    rep_html = "".join(
        f'<div class="rep-line"><span>{_esc(r.get("faction"))}</span>'
        f'<b>+{r.get("amount", 0):g}</b></div>'
        for r in rep
    ) or '<div class="empty">None recorded.</div>'

    checks = set(entry.get("summaryCheckboxes") or [])
    highest = max(checks | {5})
    boxes = "".join(
        f'<div class="box{" on" if n in checks else ""}">{n}</div>'
        for n in range(1, highest + 1)
    )

    boons = "".join(
        f'<div class="boon{" spent" if b.get("used") else ""}">'
        f'<div class="bn">{_esc(b.get("name"))}</div>'
        + (f'<div class="bd">{_esc(b["description"])}</div>'
           if b.get("description") else "")
        + "</div>"
        for b in entry.get("boons") or []
    )
    boons_block = (
        f'{_section("Boons")}{boons}' if boons else ""
    )

    items = entry.get("itemAccess") or []
    item_rows = "".join(
        f'<tr class="{"bought" if it.get("purchased") else ""}">'
        f'<td class="lv">{_esc(it.get("level") if it.get("level") is not None else "")}</td>'
        f'<td>{_esc(it.get("name"))}'
        + (f' <span class="empty">{_esc(it["source"])}</span>' if it.get("source") else "")
        + f'</td><td class="pr">{_esc(it.get("price") or "")}</td></tr>'
        for it in items
    )
    items_block = (
        _section("Treasure access", "Struck-through lines have been purchased")
        + '<table class="items"><tr><th>Lvl</th><th>Item</th><th>Price</th></tr>'
        + item_rows + "</table>"
        if item_rows else ""
    )

    notes = entry.get("notes")
    notes_block = (
        _section("Notes") + f'<div class="notes-txt">{_esc(notes)}</div>'
        if notes else ""
    )

    findings = _findings_html(result["issues"], index)
    findings_block = (
        _section("Flagged on this chronicle") + findings if findings else ""
    )

    downtime = entry.get("downtimeDays")
    return f"""
<section class="page" data-sec="chronicles">
  <div class="chron-head"><span class="code">{_esc(code)}</span>
    <span class="ttl">{_esc(name)}</span></div>
  <div class="chron-sub">{" &middot; ".join(_esc(b) for b in bits)}</div>

  <div class="prov">
    {_field("Character", log.get("character"))}
    {_field("Organized Play #", _character_number(log))}
    {_field("Played at level", entry.get("characterLevel"))}
    {_field("Date", entry.get("date"))}
    {_field("Event", entry.get("event"))}
    {_field("Event code", entry.get("eventCode"))}
    {_field("GM Organized Play #", entry.get("gmOrganizedPlayId"))}
    {_field("Partner code", entry.get("partnerCode"))}
  </div>

  {_ledger_tables(entry)}

  <div class="panels">
    <div class="panel"><div class="lbl">Reputation gained</div>{rep_html}</div>
    <div class="panel"><div class="lbl">Downtime days</div>
      <div class="big">{f"{downtime:g}" if downtime is not None else "&mdash;"}</div></div>
    <div class="panel"><div class="lbl">Adventure summary</div>
      <div class="boxes">{boxes}</div></div>
  </div>

  {boons_block}
  {items_block}
  {notes_block}
  {findings_block}
  {_foot(log.get("character") or "", f"Chronicle {index + 1} \u00b7 {code}")}
</section>
"""


_COMMUNITY_USE = (
    "This document uses trademarks and/or copyrights owned by Paizo Inc., used "
    "under Paizo's Community Use Policy (paizo.com/licenses/communityuse). It is "
    "not published, endorsed, or specifically approved by Paizo. For more "
    "information about Paizo Inc. and Paizo products, visit paizo.com."
)


def _page_notices(index: dict[str, Any]) -> str:
    return f"""
<section class="page" data-sec="attribution">
  {_section("Notices")}
  <div class="notes-txt">{_esc(_COMMUNITY_USE)}</div>
  <div class="lbl" style="margin-top:11px">Adventure index</div>
  <div class="notes-txt">Adventure titles, tiers and release data are drawn from
PathfinderWiki's Facts namespace ({_esc(index.get("total") or 0)} PF2 adventures,
seasons {_esc(index.get("seasons") or "?")}), used under the same Community Use
Policy. Award rates and Treasure Bundle values follow the Guide to Organized
Play. Neither is authoritative -- consult the current Guide and your chronicle
sheets for a real game.</div>
  {_foot("", "Notices")}
</section>
"""


def _logo(logo_path: str | None) -> tuple[str, list[str]]:
    """Embed the Pathfinder wordmark, sized from its ink like the character
    sheet does, so the two documents' nameplates line up."""
    if not logo_path:
        return "", []
    uri, warnings = _logo_data_uri(logo_path)
    style = ""
    bounds = _png_alpha_bounds(Path(logo_path).expanduser().read_bytes())
    if bounds:
        pad_top, pad_bottom = bounds
        ink = max(1e-3, 1.0 - pad_top - pad_bottom)
        box = _LOGO_INK_HEIGHT / ink
        style = (f'height:{box:.1f}px;margin-top:{-box * pad_top:.1f}px;'
                 f'margin-bottom:{3 - box * pad_bottom:.1f}px;')
    return (f'<img class="logo" src="{uri}" alt=""'
            f'{f" style=\"{style}\"" if style else ""}>', warnings)


def render_chronicle_sheet(
    chronicle_log: dict[str, Any],
    output_path: str,
    paper: str = "letter",
    logo_path: str | None = None,
    blank_adventure_rows: int = 6,
    blank_journal_rows: int = 12,
) -> dict[str, Any]:
    """Render a Pathfinder Society chronicle log as a printable HTML document.

    Produces a self-contained file -- fonts and any logo are embedded, nothing is
    fetched at view time -- laid out as: a summary page (current level and XP,
    currency on hand, Reputation per faction, downtime banked, and every
    adventure played), a validation page if anything was flagged, one page per
    chronicle in the order they were applied, and a notices page.

    `chronicle_log` is a chronicle log object matching this server's chronicle
    schema (call `pfs_chronicle_schema` for the full JSON Schema, or read it
    from a `characters/<Name>.chronicles.json` sidecar). At minimum it needs
    `schemaVersion`, `character`, and a `chronicles` array; every chronicle
    needs `adventure`, `characterLevel`, and its `xp` and `currency` ledgers.

    Each chronicle is validated as it renders and the findings are printed
    alongside the entry that produced them, because a broken ledger chain is
    something the person holding the paper needs to see. Errors mark internal
    contradictions -- a ledger that does not add up, a starting value that does
    not match the previous chronicle's ending value, an unrecognised adventure
    code, a non-repeatable adventure recorded twice. Warnings mark deviations
    from the Guide's standard awards, which individual adventures are entitled
    to make.

    Args:
        chronicle_log: The chronicle log object described above.
        output_path: Where to write the HTML. Must end in .html or .htm, and its
            parent directory must already exist. Writing to
            `characters/<Name> - Chronicles.html` alongside the character sheet
            keeps a character's files together.
        paper: 'letter' (default) or 'a4'. Sets the @page size only.
        logo_path: Optional path to the Pathfinder wordmark PNG from Paizo's
            Community Use Package, embedded verbatim in the nameplate and scaled
            proportionally, as that policy requires.
        blank_adventure_rows: Ruled blank rows to leave under Adventures Played
            (default 6). A chronicle sheet arrives by email a day after the
            session, so at a convention a player runs several games ahead of
            their records; these are for writing them in by hand and
            transcribing later. Set 0 for a records-only printout.
        blank_journal_rows: Ruled blank rows under the gold journal (default 12).
            Higher than the adventure count on purpose -- one session usually
            produces an award line, an Earn Income line and often a purchase.

    Returns a summary of what was written -- path, byte count, page count,
    the validation result and the character's derived totals -- rather than the
    HTML itself, which is far too large for a tool response.
    """
    if not isinstance(chronicle_log, dict) or not chronicle_log:
        raise ValueError("chronicle_log must be a non-empty chronicle log object")
    log = chronicle_log.get("log", chronicle_log)
    if paper not in _PAPER:
        raise ValueError(f"paper must be one of {sorted(_PAPER)}, got {paper!r}")
    out = Path(output_path).expanduser()
    if out.suffix.lower() not in (".html", ".htm"):
        raise ValueError(f"output_path must end in .html or .htm, got {out.name!r}")
    if not out.parent.is_dir():
        raise ValueError(f"output directory does not exist: {out.parent}")

    logo_html, warnings = _logo(logo_path)

    conn = get_connection()
    try:
        result = ch.validate_chronicle_log(log, conn)
        entries = log.get("chronicles") or []
        adventures = [ch.lookup_adventure(conn, e.get("adventure", "")) for e in entries]
        index = ch.index_summary(conn)
    finally:
        conn.close()

    pages = [_page_summary(log, result, adventures, logo_html, blank_adventure_rows)]
    journal_page = _page_journal(log, result, blank_journal_rows)
    if journal_page:
        pages.append(journal_page)
    findings_page = _page_findings(result)
    if findings_page:
        pages.append(findings_page)
    for i, (entry, adventure) in enumerate(zip(entries, adventures)):
        pages.append(_page_chronicle(entry, adventure, i, log, result))
    pages.append(_page_notices(index))

    sections = ["summary"]
    if journal_page:
        sections.append("journal")
    if findings_page:
        sections.append("findings")
    if entries:
        sections.append("chronicles")
    sections.append("attribution")

    name = log.get("character") or "Chronicles"
    doc = (
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_esc(name)} &mdash; Organized Play record</title>"
        f"<style>{_stylesheet(paper)}{_CHRONICLE_CSS}</style></head><body>"
        f"{_toolbar(sections)}"
        f'<div class="sheet">{"".join(pages)}</div>'
        f"{_TOGGLE_SCRIPT}</body></html>\n"
    )
    out.write_text(doc, encoding="utf-8")

    return {
        "path": str(out),
        "bytes": len(doc.encode("utf-8")),
        "paper": paper,
        "pages": len(pages),
        "sections": sections,
        "chronicles": len(entries),
        "blank_rows": {"adventures": blank_adventure_rows, "journal": blank_journal_rows},
        "journal_entries": len(result.get("journal") or []),
        "unresolved_adventures": [
            e.get("adventure") for e, a in zip(entries, adventures) if a is None
        ],
        "validation": {
            "valid": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "issues": result["issues"],
        },
        "derived": result["derived"],
        "logo": {"embedded": bool(logo_html), "source": logo_path},
        "warnings": warnings,
        "caveat": result["caveat"],
    }

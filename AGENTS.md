# Overview

This project is to build an MCP for Pathfinder 2nd Edition Remastered MCP server

This project can also be used to build characters which should be stored in pathbuilder compatible JSON files in the characters/ folder.

Please rely on the guides referenced from Zenith Games Guide to the Guides and don't always trust RPGBOT. https://zenithgames.blogspot.com/2019/09/pathfinder-2nd-edition-guide-to-guides.html

# Paizo Community Use Package assets

Artwork for character sheets comes from Paizo's Community Use Package. The
package page (https://paizo.com/community/communityuse/package) requires a
Paizo sign-in, but the archives themselves are downloadable directly:

| Pack | URL |
| --- | --- |
| Logos & Branding | https://downloads.paizo.com/Logos_and_Branding.zip |
| Pathfinder Religious Symbols | https://downloads.paizo.com/Pathfinder_Religious_Symbols.zip |
| Pathfinder Organizations | https://downloads.paizo.com/Pathfinder_Organizations.zip |
| Pathfinder Regional Symbols | https://downloads.paizo.com/Pathfinder_Regional_Symbols.zip |
| Pathfinder Runes | https://downloads.paizo.com/Pathfinder_Runes.zip |
| Pathfinder Maps | https://downloads.paizo.com/Pathfinder_Maps.zip |
| PF2e Iconic Heroes Portraits | https://downloads.paizo.com/Pathfinder_Second_Edition_Iconic_Heroes_Portraits.zip |

Extracted assets live under `.data/logos/`, which is gitignored — the same
place this project already keeps downloaded third-party content. **Do not commit
these files.** `NOTICE.md` states that the repository contains no Paizo artwork,
and that should stay true; only `sheet_assets.py`'s OFL fonts are vendored.

Files worth knowing about:

- `.data/logos/PF2Logo.png` — the Pathfinder Second Edition wordmark, 500×175
  RGBA, 66 KB. This is the one to pass as `logo_path`.
- `.data/logos/P-mark.png` — the tall "P" mark, 500×684. Portrait aspect, so it
  does not suit the nameplate slot.
- `.data/logos/religious/` — 61 deity symbols named `<Deity>.png` with
  underscores for spaces (`Sun_Wukong.png`). Pass the directory as `symbol_dir`.
- `.data/logos/organizations/`, `regional/`, `runes/` — extracted but not yet
  wired into the sheet renderer; nothing in the character JSON schema records an
  organization, region, or rune affiliation to key them off.

`Logos_and_Branding.zip` is 580 MB and mostly `.tif` plus historic/1E logos;
only a handful of PNGs are useful. Re-download it only if you need something
beyond the two files above.

## Usage rules that constrain the code

Per Paizo's Community Use Policy (https://paizo.com/licenses/communityuse),
which the FAQ confirms permits a Pathfinder logo on free fan material:

- Logos may **not** be altered in colour, typography, design, or proportions.
  Proportional resizing is allowed, so `sheet.py` embeds image bytes verbatim
  and scales with `height` + `width: auto` — never a filter, recolour, crop, or
  blend mode.
- Greyscale renderings are allowed **only if the entire work is greyscale**. The
  sheets are not, so assets go in full colour.
- Only assets from the package may be used; don't substitute a logo found
  elsewhere.
- Every rendered sheet already carries the required Community Use notice, and
  states it is not published, endorsed, or approved by Paizo.
- EPS/AI/TIF files in the packs cannot be embedded in HTML. Use the PNGs.

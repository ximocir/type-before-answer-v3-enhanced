# Type Before Answer v3 — Enhanced (unofficial fork)

An Anki add-on that adds an optional multiline typing box to the reviewer:
**type your answer on the front, then correct it in red on the back.**

Unofficial fork of [Type Before Answer v3](https://ankiweb.net/shared/info/208639570)
by Jack Thompson (MIT). Published on AnkiWeb as
[add-on 530971709](https://ankiweb.net/shared/info/530971709).

## How it works

1. **Type your answer on the front.** Write your answer in the box before
   revealing the card. You can type as much as you need, in several lines, and
   *Show Answer* always works even if the box is empty.
2. **Select white text to strike it.** On the answer side, if you select white
   text, it is struck through with a red line. Select it again to remove the
   strike. This works with the mouse (drag over the text, or double-click a
   word) and with `Shift` plus the arrow keys.
3. **Correct in red on the answer side.** After revealing, the answer you typed
   stays in place, in white and read-only. Click anywhere in that text — even
   in the middle of a word — and type: whatever you add is written in red
   exactly at that point. Every line also keeps a red writing area at its end.
   The original white text can never be edited or deleted.

`Tab` and `Shift+Tab` leave the box and return the keyboard focus to the
reviewer, so `Space` still rates the card as usual (Good) instead of
pressing a focused answer button.

## Install

- From AnkiWeb: <https://ankiweb.net/shared/info/530971709>
- Or download the `.ankiaddon` from the repository releases and use
  **Tools → Add-ons → Install from file…**, then restart Anki.

## Compatibility

Requires Anki 2.1.54 or newer. Do not run it together with the original
*Type Before Answer v3*: this fork declares a conflict with `208639570` and
`type-before-answer-v3`, so Anki disables the original automatically. Disable
or uninstall any older variant before enabling this one.

## Configuration

**Tools → Add-ons → Type Before Answer v3 — Enhanced → Config:**

```json
{
  "focus_text_box": false,
  "font_size_pt": 12
}
```

- `font_size_pt`: integer from `6` to `72` (default `12`).
- `focus_text_box`: focus the box automatically on each new card (default
  `false`).

The `Type` menu toggles the box and auto-focus. The last box size and the
on/off state are stored in `user_files/state.json`.

## Privacy

The add-on makes no network requests. Typed review text stays in the current
reviewer session and resets on the next question. Only the on/off setting and
the last box dimensions are stored locally.

## Source layout

This repository is the source used to build the published package:

- `__init__.py` — the whole add-on.
- `config.json`, `config.md` — defaults and configuration help.
- `manifest.json` — package metadata used when installing outside AnkiWeb.

Installing from AnkiWeb stores the add-on under its numeric ID, so the
`package` field in `manifest.json` only matters for manual installation.

## Made with AI

The modifications in this fork were written with the help of an AI assistant,
then tested by me.

## Licensing

Original *Type Before Answer v3* © 2025–2026 Jack Thompson, released under the
MIT license; the original notice is preserved in
[`LICENSE-MIT`](LICENSE-MIT).

The modifications in this fork (2026) are released under the
**GNU Affero General Public License v3.0 or later**
([`LICENSE`](LICENSE)), as required for add-ons shared on AnkiWeb. The package
published on AnkiWeb keeps the MIT notice as `LICENSE` and the AGPL notice as
`LICENSE-AGPL3.txt`.

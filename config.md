# Type Before Answer v3 Configuration

`font_size_pt` controls the review typing box font size in points. It must be an integer from `6` through `72`.

`focus_text_box` controls whether the typing box grabs the keyboard focus automatically when a new card is shown. Set it to `true` to start typing right away; the default is `false`, so the box stays unfocused until you click it.

```json
{
  "focus_text_box": false,
  "font_size_pt": 12
}
```

The default font size is `12`. Values outside the supported range are clamped, and invalid values fall back to the default. Invalid or missing `focus_text_box` values fall back to `false`. Saving the add-on configuration refreshes the active review UI.

Enable/disable is intentionally not configured here. Use **Type → Type box: On/Off** in Anki's top menu bar instead. The same menu offers **Type → Auto-focus box: On/Off**, which toggles `focus_text_box` without editing the configuration by hand.

The default font stack starts with `Malgun Gothic` on Windows and falls back to common macOS Korean fonts. The toggle and last dragged box dimensions are stored separately in `user_files/state.json`.

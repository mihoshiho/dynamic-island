# Design: Sakura Fox Island

Candy-sweet, cherry-blossom pink and a little pixel fox, on a calm night ground of `#191919`. Cute in the details,
calm in the whole. The full design system, with tokens, type, the brand book and component previews, is
[Sakura Fox Island](https://claude.ai/artifact/FD33GHcL4Qe9LzhL559Jxj).

## Where the theme lives

- **`THEME` in `island.py`** is the single source of colour, with the same token names as the design system. The
  island's Qt widgets read it, and the phone web app gets it as CSS custom properties (`--ground`, `--sakura`, …),
  filled in when the page is served. Change a colour there and both follow.
- Never write a hex colour anywhere else. Pick the token by its role:

| Role | Token |
| --- | --- |
| Background (island pill, phone page) | `ground` #191919 |
| Cards, panels, list groups, sheet | `surface` |
| Inputs, idle chips, secondary buttons | `surface-2` |
| Hairlines | `line` |
| Text: main, secondary, inactive | `ink`, `muted`, `faint` |
| Accent: primary button, selection, focus, active | `sakura` (+ `on-sakura` text, `sakura-deep` pressed, `sakura-tint` soft fill) |
| Fox, timers, countdowns, alerts | `fox` (+ `fox-cream`, `fox-ink` in the pixel fox) |
| Money in · money out / delete / error | `mint` · `coral`, always with `+` / `−` or a word |
| CPU · RAM graphs, petals · rare sparkles | `sky` · `lavender` · `butter` |

## Rules for new features

- **Type:** body text in `FONT` (Noto Sans, falling back to Kiwi Maru for Japanese and Noto Sans Thai for Thai).
  Big numbers and titles in `ROUND` (Kiwi Maru): the timer, money totals, the phone page title.
- **Shape:** generous radii. Inputs and small buttons 10 px, cards 14 px, the sheet 20 px, the open island 34 px.
  Chips, the closed island and the pill cover are full pills or circles.
- **One pink per view:** `sakura` marks the one action or selection. Tab icons are `ink` when active, `faint`
  otherwise, never pink.
- **One cute touch per view:** the fox in the idle pill, a sakura petal beside the phone title. No more animals,
  no falling-petal effects, no emoji except the money category marks.
- **Motion:** the island opens in 550 ms out-back and closes in 380 ms out-cubic. The fox steps every 90 ms. Nothing
  else animates by default.
- **Contrast:** text people must read is `ink` or `muted` (at least 4.5:1 on every surface). `faint` is only for
  inactive icons, placeholders and fading lyric lines.
- **No** light theme, gradients, glass blur or drop shadows (only the sakura focus ring).

When a new view needs a colour or a component the system doesn't have, add the token to `THEME` *and* to the
design system together, then use it.

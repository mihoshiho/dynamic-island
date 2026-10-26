# Dynamic Island for Windows

A cute, macOS-style Dynamic Island at the top of your screen, in sakura pink with a little pixel fox. Hover it to
open.

## What it does

- **Music**: cover, title, progress and controls for whatever plays, YouTube included.
- **Lyrics**: time-synced lyrics with romaji and translation side by side.
- **Shelf**: drop files on the island to keep them, drag them out to use them.
- **Money**: income and expenses from KBank SMS, emails and slips, sent from your iPhone, by pay cycle, with a
  calendar and how much you can spend per day until payday.
- **System**: CPU and RAM graphs, and the programs using the most CPU.
- **Claude**: your Claude Code usage from its logs on this PC: the 5-hour window (how full, when it resets),
  today and the last 7 days, with the value at API prices.
- **Timer** and **Calendar** reminders.
- **Idle fox**: runs in the island when nothing plays. In fullscreen games the island shrinks to a thin bar.

## What it needs

- Windows 10 or 11.
- For Money: an iPhone on the same Wi-Fi, or [Tailscale](https://tailscale.com) on both to use it from anywhere.
- Optional, for reading Thai slips: Windows' Thai OCR pack (run in an admin PowerShell):
  `Add-WindowsCapability -Online -Name "Language.OCR~~~th-TH~0.0.1.0"`

## Install

Download `DynamicIsland-Setup-x.y.z.exe` from [Releases](https://github.com/mihoshiho/dynamic-island/releases) and
run it. It updates itself.

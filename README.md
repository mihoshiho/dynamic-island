# Dynamic Island for Windows

A cute, macOS-style Dynamic Island at the top of your screen, in sakura pink with a little pixel fox. Hover it to
open.

## What it does

- **Music**: cover, title, progress and controls for whatever plays, YouTube included.
- **Lyrics**: time-synced lyrics with romaji and translation side by side.
- **Shelf**: drop files on the island to keep them, drag them out to use them.
- **Money**: income and expenses by pay cycle (your payday, your currency), with a calendar, a yearly history and
  how much you can spend per day until payday. Bank SMS, emails and slips are read for you (made for KBank; test
  your bank's messages in the phone app's Settings). A phone app for iPhone and Android goes with it.
- **System**: CPU and RAM graphs, and the programs using the most CPU.
- **Claude**: your Claude Code usage from its logs on this PC: the 5-hour window (how full, when it resets),
  today and the last 7 days, with the value at API prices.
- **Timer** and **Calendar** reminders.
- **Idle fox**: runs in the island when nothing plays. In fullscreen games the island shrinks to a thin bar.

## What it needs

- Windows 10 or 11.
- For Money: an iPhone or Android phone on the same Wi-Fi, or [Tailscale](https://tailscale.com) on both to use it
  from anywhere.

## Install

Download `DynamicIsland-Setup-x.y.z.exe` from [Releases](https://github.com/mihoshiho/dynamic-island/releases) and
run it. It updates itself.

## How to use

1. On first start a short guide opens in the island. You can see it again in Settings → Show guide.
2. Hover the island to open it, move away to close it, right-click it to quit.
3. For Money on your phone: Money tab → QR code. Turn on the phone page and scan the code. iPhone: open it in
   Safari, Share → Add to Home Screen. Android: Chrome menu → Add to Home screen. If Windows asks, click Allow.
   The phone app's Settings shows how to send bank messages to it automatically.
4. Set your payday and currency in Settings or on the phone page.
5. Two PCs, one Money: on the second PC, paste the main PC's phone link in Settings → Link to main PC.
   Both need Tailscale (or the same Wi-Fi), and the main PC keeps iPhone sync on.
6. Discord mute / deafen on the pill: at [discord.com/developers](https://discord.com/developers/applications) make
   a New Application, add `http://127.0.0.1` under OAuth2 → Redirects, then paste its Client ID and Client Secret
   in Settings → Discord and click Authorize in Discord. Click the badge to unmute, right-click to undeafen.
7. Lyrics sync with no daily limit (optional, needs an NVIDIA GPU): install
   [Everyric2](https://github.com/onpe5679/Everyric2) in `D:\code\Everyric2` with its `.venv`, then turn on
   Settings → Lyrics → Sync on my PC. Songs it can't do fall back to the public Everyric.

# Dynamic Island for Windows

A macOS-style Dynamic Island that sits at the top of your screen. Hover it to open.

- **Music**: album art, title, progress, play / pause / skip for whatever plays (YouTube in the browser, Spotify, ...).
- **Lyrics**: time-synced lyrics in a split view, with the original on the left and romaji and translation on the right.
  When [Everyric](https://github.com/onpe5679/Everyric2) has the video, each line fills letter by letter in time
  with the singing, and **✨ Sync to this video** asks Everyric to align a new one. Otherwise lyrics come from
  lrclib and NetEase. Missing translations (Thai, for one) and romaji come from Google Translate.
- **Shelf**: drop files on the island to park them, drag them back out into any app, double-click to open.
- **Money**: income and expenses from your KBank SMS, alert emails and slips, sent from your iPhone (see below).
- **System**: CPU and RAM graphs for the last minute, plus the 5 programs using the most CPU.
- **Timer**: presets and a big countdown.
- **Calendar**: reminders on any day and time, kept between restarts.
- **Settings**: start with Windows, a tiny island in fullscreen games, lyric options, alert sound, screen, auto-update.

## Install

Download `DynamicIsland-Setup-x.y.z.exe` from [Releases](https://github.com/mihoshiho/dynamic-island/releases)
and run it. It installs for your user only, so it needs no admin rights. After that it updates itself: it checks
for a new release a minute after starting and then every 6 hours. You can turn this off in Settings.

Your settings and reminders are stored in `%APPDATA%\DynamicIsland`.

## Money with an iPhone

iOS doesn't let a PC read your bank app's notifications, so iPhone Shortcuts send the bank texts to the island
instead.

1. In the island, open the Money tab and press **Connect iPhone**. If Windows asks, allow the firewall prompt for
   private networks.
2. Open the link it shows in Safari on the iPhone, then Share → **Add to Home Screen**. That page shows your totals
   and history, lets you add or fix entries, and has the Shortcut steps with your URL and key filled in.
3. Make the three Shortcuts from that page:
   - a Message automation for KBank SMS
   - an Email automation for KBank alerts
   - a Share Sheet shortcut for slips, which reads the text on the phone itself
   Or skip the slip Shortcut: tap **Import slips** in the web app and pick slips from Photos. The PC reads
   them with Windows' built-in OCR. For Thai text and slip dates, install Windows' Thai OCR pack once, from an
   admin PowerShell: `Add-WindowsCapability -Online -Name "Language.OCR~~~th-TH~0.0.1.0"`. Without it the
   amount is still read, and the photo's time is used as the date.
4. To reach the island from outside home Wi-Fi, install [Tailscale](https://tailscale.com) on both the PC and the
   iPhone. The island then shows its Tailscale address.

The receiver only accepts connections from private networks and Tailscale. Every request needs the pairing key.
Transactions are kept in `%APPDATA%\DynamicIsland\money.json`. When the same transfer arrives as an SMS, an email
and a slip, the copies are merged into one entry.

## Fonts

The `fonts` folder holds the bundled fonts, all under the SIL Open Font License (see the `OFL-*.txt` files):

- [Noto Sans](https://github.com/notofonts/latin-greek-cyrillic) for English and most text
- [Noto Sans Thai](https://github.com/notofonts/thai) for Thai
- [Kiwi Maru](https://github.com/Kiwi-KawagotoKajiru/Kiwi-Maru) for Japanese

## Run from source

```
pip install -r requirements.txt
pythonw island.py
python island.py test
```

The last command runs the self-check.

## Release

1. Bump `VERSION` in `island.py`.
2. Commit, then push a matching tag, for example `git tag v1.0.1 && git push --tags`.
3. GitHub Actions builds the installer (PyInstaller and Inno Setup) and publishes the release. Installed islands
   update to it on their own.

To build locally, install Inno Setup 6 and run `./build.ps1`.

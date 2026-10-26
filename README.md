# Dynamic Island for Windows

A macOS-style Dynamic Island that sits at the top of your screen. Hover it to open.

- **Music**: album art, title, progress, play / pause / skip for whatever plays (YouTube in the browser, Spotify, ...).
- **Lyrics**: time-synced lyrics in a split view, with the original on the left and romaji and translation on the right.
  When [Everyric](https://github.com/onpe5679/Everyric2) has the video, each line fills letter by letter in time
  with the singing, and **✨ Sync to this video** asks Everyric to align a new one. Otherwise lyrics come from
  lrclib and NetEase. Missing translations (Thai, for one) and romaji come from Google Translate.
- **System**: CPU and RAM graphs for the last minute, plus the 5 programs using the most CPU.
- **Timer**: presets and a big countdown.
- **Calendar**: reminders on any day and time, kept between restarts.
- **Settings**: start with Windows, a tiny island in fullscreen games, lyric options, alert sound, screen, auto-update.

## Install

Download `DynamicIsland-Setup-x.y.z.exe` from [Releases](https://github.com/mihoshiho/dynamic-island/releases)
and run it. It installs for your user only, so it needs no admin rights. After that it updates itself: it checks
for a new release a minute after starting and then every 6 hours. You can turn this off in Settings.

Your settings and reminders are stored in `%APPDATA%\DynamicIsland`.

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

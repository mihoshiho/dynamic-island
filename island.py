"""Dynamic Island for Windows, macOS style. Tabs: music (album art, synced lyrics, controls),
system (CPU/RAM graphs, top programs), timer, calendar reminders.

Run: pythonw island.py      (right-click the island to quit)
Self-check: python island.py test
"""
import asyncio, bisect, collections, ctypes, datetime as dt, json, os, pathlib, random, re, sys, threading, time, urllib.parse
import shutil, subprocess, urllib.request, winreg, winsound

import psutil
from PySide6.QtCore import Qt, QDate, QPointF, QRect, QRectF, QTime, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QTextCharFormat
from PySide6.QtWidgets import (QAbstractButton, QApplication, QCalendarWidget, QComboBox, QGraphicsOpacityEffect, QHBoxLayout, QLineEdit,
                               QListWidget, QMenu, QPushButton, QStackedWidget, QTimeEdit, QVBoxLayout, QWidget)
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager
from winrt.windows.storage.streams import Buffer, InputStreamOptions

BG, PANEL, FG, DIM, GREEN, ORANGE, RED = '#000000', '#1c1c1e', '#ffffff', '#8e8e93', '#30d158', '#ff9f0a', '#ff453a'
BLUE, PURPLE = '#0a84ff', '#bf5af2'
FONT = 'Segoe UI'
PREV, PLAY, PAUSE, NEXT = '\uE892', '\uE768', '\uE769', '\uE893'
MUSIC, SYSTEM, TIMER, CALENDAR = '\uE8D6', '\uE9D9', '\uE916', '\uE787'
PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
COMPACT_H, TOP, WIN = 36, 8, (760, 360)
GEAR, LYRICS = '', ''
TABS = ['music', 'lyrics', 'system', 'timer', 'calendar', 'settings']
SIZES = {'music': (460, 200), 'lyrics': (560, 260), 'system': (540, 220), 'timer': (400, 180),
         'calendar': (580, 260), 'settings': (460, 300), 'alert': (400, 160)}
LEAD = 0.3  # seconds a lyric line shows before it is sung
TINY = (110, 8)  # island while a fullscreen app or game is in front
VERSION = '1.0.0'
REPO = 'mihoshiho/dynamic-island'  # auto-update reads this repo's latest GitHub release
FROZEN = getattr(sys, 'frozen', False)  # running as the installed .exe
DATA = pathlib.Path(os.environ.get('APPDATA') or pathlib.Path.home()) / 'DynamicIsland'  # Program files is read-only
DATA.mkdir(exist_ok=True)
for _name in ('reminders.json', 'settings.json'):  # move data from older versions that kept it next to the script
    _old = pathlib.Path(__file__).with_name(_name)
    if _old.exists() and not (DATA / _name).exists():
        shutil.move(_old, DATA / _name)  # script may sit on another drive than AppData
SAVE = DATA / 'reminders.json'
SETTINGS_FILE = DATA / 'settings.json'
DEFAULTS = {'tiny_fullscreen': True, 'compact_lyrics': True, 'sound': True, 'screen': 0,
            'translation': None, 'pronunciation': None, 'auto_update': True}
TRANSLATIONS = [('Off', None), ('ไทย', 'th'), ('English', 'en'), ('日本語', 'ja')]
PRONUNCIATIONS = [('Off', None), ('Romaji', 'romaji')]
RUN_KEY, RUN_NAME = r'Software\Microsoft\Windows\CurrentVersion\Run', 'DynamicIsland'

media = {}  # written by the media thread, read by the UI
media_loop_ref = None  # media thread's asyncio loop, used for play/pause/skip
top_procs = []  # [(name, cpu %, ram MB)], written by the process thread
procs_wanted = threading.Event()  # set while the system tab is visible
prefs = {'lang': None, 'romaji': False}  # what the lyric loader adds to each line


class UnicodeString(ctypes.Structure):
    _fields_ = [('Length', ctypes.c_ushort), ('MaximumLength', ctypes.c_ushort), ('Buffer', ctypes.c_void_p)]


class ProcInfo(ctypes.Structure):  # SYSTEM_PROCESS_INFORMATION, only the fields up to WorkingSetSize
    _fields_ = [('NextEntryOffset', ctypes.c_ulong), ('NumberOfThreads', ctypes.c_ulong),
                ('WorkingSetPrivateSize', ctypes.c_longlong), ('HardFaultCount', ctypes.c_ulong),
                ('NumberOfThreadsHighWatermark', ctypes.c_ulong), ('CycleTime', ctypes.c_ulonglong),
                ('CreateTime', ctypes.c_longlong), ('UserTime', ctypes.c_longlong), ('KernelTime', ctypes.c_longlong),
                ('ImageName', UnicodeString), ('BasePriority', ctypes.c_long), ('UniqueProcessId', ctypes.c_void_p),
                ('InheritedFromUniqueProcessId', ctypes.c_void_p), ('HandleCount', ctypes.c_ulong),
                ('SessionId', ctypes.c_ulong), ('UniqueProcessKey', ctypes.c_void_p), ('PeakVirtualSize', ctypes.c_size_t),
                ('VirtualSize', ctypes.c_size_t), ('PageFaultCount', ctypes.c_ulong),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t)]


def snapshot():
    """{pid: (name, cpu time in 100 ns, private RAM bytes)} in one kernel call.
    psutil needs ~6 s for this on a busy PC because every protected process triggers its own full scan."""
    ntdll = ctypes.windll.ntdll
    ntdll.NtQuerySystemInformation.restype = ctypes.c_long
    size, needed = 1 << 20, ctypes.c_ulong()
    while True:
        buf = ctypes.create_string_buffer(size)
        status = ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(needed))  # SystemProcessInformation
        if status == 0:
            break
        if status & 0xFFFFFFFF != 0xC0000004:  # anything but STATUS_INFO_LENGTH_MISMATCH
            raise OSError(f'NtQuerySystemInformation failed: {status & 0xFFFFFFFF:#x}')
        size = needed.value + 65536
    out, off = {}, 0
    while True:
        p = ProcInfo.from_buffer(buf, off)
        name = ctypes.wstring_at(p.ImageName.Buffer, p.ImageName.Length // 2) if p.ImageName.Buffer else 'Idle'
        out[p.UniqueProcessId or 0] = (name, p.UserTime + p.KernelTime, p.WorkingSetPrivateSize)
        if not p.NextEntryOffset:
            return out
        off += p.NextEntryOffset


def scan_processes():
    """Group processes by program name (Opera = all its helper processes) every 2 s,
    only while the system tab is open: the scan was most of the island's CPU use."""
    global top_procs
    cores = psutil.cpu_count()
    while True:
        procs_wanted.wait()
        prev, prev_t, delay = snapshot(), time.time(), 0.5  # quick first sample so the list fills fast
        while procs_wanted.is_set():
            time.sleep(delay)
            delay = 2
            cur, now = snapshot(), time.time()
            usage = collections.defaultdict(lambda: [0.0, 0.0])
            for pid, (name, cpu, ram) in cur.items():
                if pid == 0:  # Idle
                    continue
                u = usage[name.removesuffix('.exe')]
                if prev.get(pid, ('',))[0] == name:  # same pid and name: not a reused pid
                    u[0] += (cpu - prev[pid][1]) / ((now - prev_t) * 1e7 * cores) * 100
                u[1] += ram / 2**20
            top_procs = sorted(((n, c, m) for n, (c, m) in usage.items()), key=lambda x: (-x[1], -x[2]))[:5]
            prev, prev_t = cur, now


def load_reminders():
    try:
        return sorted((t, m) for t, m in json.loads(SAVE.read_text('utf-8')))
    except (OSError, ValueError):
        return []


def load_settings():
    try:
        return {**DEFAULTS, **json.loads(SETTINGS_FILE.read_text('utf-8'))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def autostart(on=None):
    """Read or set the HKCU Run entry that starts the island at sign-in."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
        if on is None:
            try:
                return bool(winreg.QueryValueEx(k, RUN_NAME))
            except FileNotFoundError:
                return False
        if on:
            if FROZEN:
                cmd = f'"{sys.executable}"'
            else:  # pythonw: no console window
                cmd = f'"{pathlib.Path(sys.executable).with_name("pythonw.exe")}" "{pathlib.Path(__file__).resolve()}"'
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass


def newer_release():
    """(version, installer url) of a newer GitHub release, else None."""
    rel = get_json(f'https://api.github.com/repos/{REPO}/releases/latest')
    tag = rel['tag_name'].lstrip('v')
    as_tuple = lambda v: tuple(int(x) for x in re.findall(r'\d+', v))
    asset = next((a for a in rel.get('assets', []) if a['name'].lower().endswith('.exe')), None)
    return (tag, asset['browser_download_url']) if asset and as_tuple(tag) > as_tuple(VERSION) else None


def install_update(url):
    """Download the installer and run it silently; it replaces the files and starts the new island."""
    path = pathlib.Path(os.environ.get('TEMP', DATA)) / url.rsplit('/', 1)[-1]
    req = urllib.request.Request(url, headers={'User-Agent': 'dynamic-island-pc'})
    with urllib.request.urlopen(req, timeout=60) as r, open(path, 'wb') as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    subprocess.Popen([str(path), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'])


def single_instance():
    """False when another island already runs (autostart plus a manual start)."""
    ctypes.windll.kernel32.CreateMutexW(None, False, 'DynamicIslandSingleInstance')
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


class Rect(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', Rect), ('rcWork', Rect), ('dwFlags', ctypes.c_ulong)]


def fullscreen_in_front(own_hwnd):
    """True when the foreground window covers the whole monitor the island is on (game, fullscreen video)."""
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    hwnd = user32.GetForegroundWindow()
    if not hwnd or hwnd == own_hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(ctypes.c_void_p(hwnd), cls, 64)
    if cls.value in ('Progman', 'WorkerW', 'Shell_TrayWnd'):  # desktop and taskbar also span the screen
        return False
    mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    if mon != user32.MonitorFromWindow(own_hwnd, 2):
        return False
    info, r = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo)), Rect()
    user32.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(info))
    user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
    m = info.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


# ---------- lyrics ----------
def clean_title(t):
    t = re.sub(r'[(\[【].*?[)\]】]', '', t)
    t = re.sub(r'(?i)\b(official|music video|lyrics?|mv|audio|4k)\b.*', '', t)
    return t.split('|')[0].strip(' -')


def parse_lrc(text):
    out = []
    for line in text.splitlines():
        m = re.match(r'\[(\d+):(\d+(?:\.\d+)?)\](.*)', line)
        if m and not re.match(r'\s*(作词|作詞|作曲|编曲|編曲|词|曲)\s*[:：]', m[3]):  # NetEase credit lines
            out.append((int(m[1]) * 60 + float(m[2]), m[3].strip()))
    return out


def clean_artist(a):  # "ArtistVEVO", "Artist - Topic", "V.W.P -Virtual Witch Phenomenon-" -> core name
    a = re.sub(r'(?i)vevo|official| - topic', '', a)
    return re.split(r'\s+-|\s*[(\[]', a)[0].strip()


def get_json(url, **headers):
    req = urllib.request.Request(url, headers={'User-Agent': 'dynamic-island-pc', **headers})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


# each source returns [(duration s, function returning LRC text)]
def lrclib(q):
    return [(h.get('duration') or 0, lambda h=h: h['syncedLyrics'])
            for h in get_json('https://lrclib.net/api/search?' + urllib.parse.urlencode({'q': q}))
            if h.get('syncedLyrics')]


def netease(q):
    """NetEase Cloud Music's unofficial web API: far more J-pop, anime and VOCALOID synced lyrics than lrclib."""
    web = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://music.163.com'}
    url = 'https://music.163.com/api/search/get?' + urllib.parse.urlencode({'s': q, 'type': 1, 'limit': 8})
    songs = get_json(url, **web).get('result', {}).get('songs', [])
    return [(s['duration'] / 1000,
             lambda s=s: get_json(f'https://music.163.com/api/song/lyric?id={s["id"]}&lv=1', **web)
             .get('lrc', {}).get('lyric', ''))
            for s in songs]


def lyric_queries(title, artist):
    """YouTube titles: "Artist - Song", "Song - extra", "『Song』/ Artist"... so try several readings."""
    q, artist = clean_title(title), clean_artist(artist)
    parts = [re.sub(r'(?i)\s*\b(feat|ft)\..*', '', p).strip() for p in re.split(r'\s+-\s+|\s*/\s*', q)]
    parts = [p for p in parts if p] or [q]
    queries = [q, f'{parts[0]} {artist}', f'{parts[-1]} {artist}'] if len(parts) > 1 else [f'{q} {artist}', q]
    quoted = re.search(r'[『「](.+?)[』」]', title)  # Japanese titles quote the song name
    if quoted:
        queries.insert(0, f'{quoted[1]} {artist}')
    return list(dict.fromkeys(queries))  # drop duplicates, keep order


def fetch_lyrics(title, artist, length):
    """Line-synced lyrics from lrclib/NetEase -> (lines, plain text). Plain text, kept even when no
    version's length matches the video, is what Everyric needs to build a sync for this exact video."""
    failed, plain = False, None
    for source in (lrclib, netease):
        for query in lyric_queries(title, artist):
            try:
                hits = source(query)
                if hits and plain is None:
                    plain = '\n'.join(t for _, t in parse_lrc(hits[0][1]()) if t) or None
                if length > 0:  # duration far off the video's means another song or version
                    hits = sorted((h for h in hits if abs(h[0] - length) <= 15), key=lambda h: abs(h[0] - length))
                for _, text in hits[:2]:
                    lines = parse_lrc(text())
                    if lines:
                        return lines, plain
            except Exception:  # one source down must not hide the other
                failed = True
    if failed and plain is None:
        raise ConnectionError('lyrics source unreachable')  # load_lyrics retries
    return [], plain


# ---------- Everyric: lyrics aligned to the exact YouTube video, letter timing, pronunciation, translation ----------
EVERYRIC = 'https://everyric.moref.co'
BROWSERS = ('chrome', 'msedge', 'firefox', 'opera', 'brave', 'vivaldi')
VIDEO_RENDERER = re.compile(r'"videoRenderer":\{"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"')


def find_video(title):
    """Windows media info has no URL, so find the YouTube video whose title matches exactly."""
    q = '"' + title.replace('"', ' ') + '"'  # exact phrase; a bare "-word" would mean "exclude word"
    req = urllib.request.Request('https://www.youtube.com/results?' + urllib.parse.urlencode({'search_query': q}),
                                 headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode('utf-8', 'replace')
    for vid, t in VIDEO_RENDERER.findall(html):
        if json.loads(f'"{t}"') == title:
            return vid
    return None


def everyric(vid, title, artist, lang):
    """-> (lines, offset) or None. Each line: (start, text, {'chars': [(start, end, char)], 'pron': {...}, 'trans': {...}})."""
    q = {'title': title[:200], 'artist': artist[:200], **({'lang': lang} if lang else {})}
    d = get_json(f'{EVERYRIC}/api/sync/{vid}?' + urllib.parse.urlencode(q))
    if not d.get('found') or not d.get('timestamps'):
        return None
    trans = {k: v for k, v in (d.get('translations_by_lang') or {}).items() if isinstance(v, list)}
    lines = [(ts['start'], ts['text'], {
        'chars': [(w['start'], w['end'], w['word']) for w in ts.get('words') or []],
        'pron': ts.get('pron') or {},
        'trans': {k: v[i] for k, v in trans.items() if i < len(v) and v[i]},
    }) for i, ts in enumerate(d['timestamps'])]
    return lines, d.get('user_offset') or 0  # the extension shows lyrics at video time + offset


def gtx(text, tl, dt):
    body = urllib.parse.urlencode({'q': text}).encode()
    req = urllib.request.Request(
        f'https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={tl}&dt={dt}', body,
        headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def enrich(lines, lang, romaji):
    """Add what Everyric lacks (Thai is never there, line-sync sources have nothing) from Google Translate's
    free web endpoint: translation keeps one line per line; romaji comes back joined, so lines go in split by ' | '.
    shortcut: one request per kind, very long lyrics may hit the endpoint's size limit; chunk if that shows up."""
    lines = [(ln[0], ln[1], ln[2] if len(ln) > 2 else {'chars': [], 'pron': {}, 'trans': {}}) for ln in lines]
    todo = [i for i, ln in enumerate(lines) if ln[1]]
    src = [lines[i][1].replace('|', '/') for i in todo]
    try:
        if todo and lang and not all(lang in lines[i][2]['trans'] for i in todo):
            d = gtx('\n'.join(src), lang, 't')
            out = ''.join(seg[0] for seg in d[0] if seg[0]).split('\n')
            if len(out) == len(todo):
                for i, tr in zip(todo, out):
                    lines[i][2]['trans'][lang] = tr.strip()
        if todo and romaji and not any(lines[i][2]['pron'].get('romaji') for i in todo):
            d = gtx(' | '.join(src), 'en', 'rm')
            rm = d[0][-1][3] if d[0] and len(d[0][-1]) > 3 and d[0][-1][3] else ''
            parts = rm.split('|')
            if len(parts) == len(todo):
                for i, r in zip(todo, parts):
                    lines[i][2]['pron']['romaji'] = r.strip()
    except Exception:  # lyrics still show without the extra rows
        pass
    return lines


def post_json(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), method='POST',
                                 headers={'User-Agent': 'dynamic-island-pc', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def same_song(key):
    return (media.get('title'), media.get('artist')) == key


def load_lyrics(key, length, from_browser):
    found = dict(lyrics=[], offset=0, video_id=None, plain=None, source=None)
    for _ in range(3):  # lyric servers sometimes answer 503
        try:
            vid = find_video(key[0]) if from_browser else None
            ev = everyric(vid, *key, everyric_lang()) if vid else None
            if ev:
                found.update(lyrics=ev[0], offset=ev[1], video_id=vid, source='everyric')
            else:
                lines, plain = fetch_lyrics(*key, length)
                found.update(lyrics=lines, video_id=vid, plain=plain, source='line' if lines else None)
            break
        except Exception:
            time.sleep(5)
    if found['lyrics'] and (prefs['lang'] or prefs['romaji']):
        found['lyrics'] = enrich(found['lyrics'], prefs['lang'], prefs['romaji'])
    if same_song(key):  # song may have changed while fetching
        media.update(found)


def everyric_lang():
    return prefs['lang'] if prefs['lang'] in ('en', 'ja') else None  # Everyric translates to ko/en/ja only


def generate_sync(key, vid, plain, lang):
    """Ask Everyric to align these lyrics to this video (its daily limit is 15 per user), then load the result."""
    def status(text):
        if same_song(key):
            media['gen'] = text
    try:
        status('Sending to Everyric…')
        job = post_json(f'{EVERYRIC}/api/sync/generate', {
            'video_id': vid, 'lyrics': plain, 'title': key[0][:200], 'artist': key[1][:200],
            **({'target_lang': lang} if lang else {})})
        for _ in range(400):  # up to ~20 min
            j = get_json(f'{EVERYRIC}/api/job/{job["job_id"]}')
            if j.get('status') == 'completed':
                break
            if j.get('status') == 'failed' or j.get('gone'):
                return status(f'Sync failed {j.get("error") or ""}'.strip())
            p = j.get('progress') or 0
            status(f'{(j.get("stage") or j.get("status") or "working").capitalize()}  {p * 100 if p <= 1 else p:.0f}%')
            time.sleep(3)
        ev = everyric(vid, *key, lang)
        if ev and same_song(key):
            media.update(lyrics=enrich(ev[0], prefs['lang'], prefs['romaji']), offset=ev[1], source='everyric',
                         gen=None)
    except Exception as e:
        status(f'Sync failed: {e}')


def position():
    return media.get('pos', 0) + (time.time() - media['pos_at'] if media.get('playing') else 0)


def lyric_time():
    return position() + media.get('offset', 0)


def lyric_index():
    """Index of the line being sung, -1 before the first. Lines switch LEAD s early, as eyes read ahead."""
    return bisect.bisect_right(media.get('lyrics') or [], lyric_time() + LEAD, key=lambda line: line[0]) - 1


def current_lyric():
    i = lyric_index()
    return media['lyrics'][i][1] if i >= 0 and media.get('playing') else ''


# ---------- media session ----------
async def read_art(ref):
    st = await ref.open_read_async()
    buf = Buffer(st.size)
    await st.read_async(buf, buf.capacity, InputStreamOptions.READ_AHEAD)
    return bytes(buf)


async def media_loop():
    global media_loop_ref
    media_loop_ref = asyncio.get_running_loop()
    mgr = await MediaManager.request_async()
    key = None
    while True:
        s = mgr.get_current_session()
        try:
            if not s:
                raise LookupError
            p = await s.try_get_media_properties_async()
            tl = s.get_timeline_properties()
            playing = s.get_playback_info().playback_status == PLAYING
            pos = tl.position.total_seconds()
            if playing:  # browsers report position rarely; extrapolate from the last update
                pos += (dt.datetime.now(dt.timezone.utc) - tl.last_updated_time).total_seconds()
            if (p.title, p.artist) != key or media.pop('reload', False):
                key = (p.title, p.artist)
                media.update(title=p.title, artist=p.artist, lyrics=[], art=None, art_key=None, gen=None,
                             source=None, video_id=None, plain=None, offset=0)
                from_browser = any(b in s.source_app_user_model_id.lower() for b in BROWSERS)
                threading.Thread(target=load_lyrics, args=(key, tl.end_time.total_seconds(), from_browser),
                                 daemon=True).start()
            if p.thumbnail and media.get('art_key') != key:
                media.update(art=await read_art(p.thumbnail), art_key=key)
            media.update(session=s, playing=playing, pos=pos, pos_at=time.time(), length=tl.end_time.total_seconds())
        except Exception:  # no session, or it closed mid-poll
            media.clear()
            key = None
        await asyncio.sleep(1)


def control(name):
    s = media.get('session')
    if s and media_loop_ref:
        async def call():
            await getattr(s, name)()
        asyncio.run_coroutine_threadsafe(call(), media_loop_ref)


# ---------- widgets ----------
def mmss(sec):
    sec = max(0, int(sec))
    return f'{sec // 60}:{sec % 60:02d}'


def tint(pix):  # art's average color, kept bright, like the macOS island's waveform
    c = pix.toImage().scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).pixelColor(0, 0)
    h, s, _, _ = c.getHsvF()
    return QColor.fromHsvF(max(h, 0), min(1, s * 1.3), 0.95)


class Text(QWidget):  # one elided line; a QLabel would widen the island on long titles
    def __init__(self, size=10, color=FG, bold=False, align=Qt.AlignLeft):
        super().__init__()
        self.setFont(QFont(FONT, size, QFont.Bold if bold else QFont.Normal))
        self.color, self.align, self.text = QColor(color), align, ''
        self.setFixedHeight(self.fontMetrics().height() + 2)

    def set(self, text, color=None):
        if text != self.text or (color and QColor(color) != self.color):
            self.text, self.color = text, QColor(color or self.color)
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(self.color)
        p.drawText(self.rect(), self.align | Qt.AlignVCenter,
                   self.fontMetrics().elidedText(self.text, Qt.ElideRight, self.width()))


class Art(QWidget):
    def __init__(self, size, radius):
        super().__init__()
        self.setFixedSize(size, size)
        self.radius, self.pix = radius, None

    def set(self, pix):
        self.pix = pix
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self.radius, self.radius)
        p.setClipPath(path)
        if self.pix:  # YouTube thumbnails are 16:9, crop the center square
            w, h = self.pix.width(), self.pix.height()
            s = min(w, h)
            p.drawPixmap(QRectF(self.rect()), self.pix, QRectF((w - s) / 2, (h - s) / 2, s, s))
        else:
            p.fillPath(path, QColor('#2c2c2e'))


class Wave(QWidget):  # decorative equalizer bars
    def __init__(self, w, h):
        super().__init__()
        self.setFixedSize(w, h)
        self.levels, self.on, self.color = [0.25] * 4, False, QColor(GREEN)
        self.timer = QTimer(self, timeout=self.step, interval=130)

    def set_on(self, on):
        if on != self.on:
            self.on = on
            self.timer.start() if on else self.timer.stop()
            self.step()

    def step(self):
        if self.isVisible():
            self.levels = [random.uniform(0.3, 1) if self.on else 0.25 for _ in self.levels]
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        bw = self.width() / 7
        for i, k in enumerate(self.levels):
            bh = self.height() * k
            p.drawRoundedRect(QRectF(i * bw * 2, (self.height() - bh) / 2, bw, bh), bw / 2, bw / 2)


class Bar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(6)
        self.v = 0

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 50))
        p.drawRoundedRect(QRectF(0, 1, self.width(), 4), 2, 2)
        p.setBrush(QColor(FG))
        p.drawRoundedRect(QRectF(0, 1, self.width() * min(1, self.v), 4), 2, 2)


class Graph(QWidget):  # last 60 s of a percentage, filled line chart
    def __init__(self, name, color):
        super().__init__()
        self.setFixedHeight(66)
        self.name, self.color, self.values = name, QColor(color), collections.deque([0.0] * 60, maxlen=60)

    def add(self, v):
        self.values.append(v)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h, top = self.width(), self.height(), 18
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(PANEL))
        p.drawRoundedRect(QRectF(0, top, w, h - top), 8, 8)
        line = QPainterPath()
        for i, v in enumerate(self.values):
            pt = QPointF(w * i / 59, h - 2 - (h - top - 6) * v / 100)
            line.lineTo(pt) if i else line.moveTo(pt)
        area = QPainterPath(line)
        area.lineTo(w, h)
        area.lineTo(0, h)
        grad = QLinearGradient(0, top, 0, h)
        fill = QColor(self.color)
        fill.setAlpha(110)
        grad.setColorAt(0, fill)
        fill.setAlpha(0)
        grad.setColorAt(1, fill)
        p.fillPath(area, grad)
        p.setPen(QPen(self.color, 1.6))
        p.drawPath(line)
        p.setFont(QFont(FONT, 8, QFont.Bold))
        p.setPen(QColor(FG))
        p.drawText(QRectF(0, 0, w, 16), Qt.AlignLeft | Qt.AlignVCenter, self.name)
        p.setPen(self.color)
        p.drawText(QRectF(0, 0, w, 16), Qt.AlignRight | Qt.AlignVCenter, f'{self.values[-1]:.0f}%')


class Lyrics(QWidget):
    """Everyric / Apple Music style: lines scroll up smoothly, the sung line sits big in the middle and
    fills letter by letter when the source has letter timing; pronunciation and translation under each line."""
    LINE_H, EXTRA_H = 30, 18

    def __init__(self):
        super().__init__()
        self.lines, self.idx, self.scroll, self.color = [], -1, -1.0, QColor(FG)
        self.pron = self.trans = None  # chosen pronunciation ('romaji', ...) and translation ('en', ...)
        self.rows = []  # which extra rows this song has: 'pron', 'trans'
        self.fonts = {'big': QFont(FONT, 13, QFont.Bold), 'small': QFont(FONT, 11), 'tiny': QFont(FONT, 9),
                      'romaji': QFont(FONT, 12, QFont.DemiBold), 'romaji_now': QFont(FONT, 13, QFont.Bold)}
        self.metrics = {k: QFontMetricsF(f) for k, f in self.fonts.items()}
        self.elided = {}  # (text, font, width) -> elided text; measuring every frame was most of the paint cost
        self.cur = None  # current line's layout: (idx, width, font, text, cumulative letter widths)
        self.cur_rect = QRect()
        self.anim = QTimer(self, timeout=self.step, interval=16)  # runs only while scrolling or filling

    def extra(self, line, row):
        info = line[2] if len(line) > 2 else {}
        text = info.get(row, {}).get(self.pron if row == 'pron' else self.trans) if info else None
        same = text and re.sub(r'\W', '', text.lower()) == re.sub(r'\W', '', line[1].lower())
        return None if not text or same else text  # romaji of an English line is the line itself

    def set_options(self, pron, trans):
        self.pron, self.trans = pron, trans
        self.rows = [r for r in ('pron', 'trans') if any(self.extra(line, r) for line in self.lines)]
        self.elided.clear()
        self.update()

    def resizeEvent(self, e):
        self.elided.clear()
        self.cur = None

    def elide(self, text, font, width):
        key = (text, font, int(width))
        if key not in self.elided:
            self.elided[key] = self.metrics[font].elidedText(text, Qt.ElideRight, width)
        return self.elided[key]

    def block_h(self):  # both columns share a row per lyric line; romaji + translation need more height
        return self.LINE_H + (16 if len(self.rows) > 1 else 0)

    def karaoke(self):
        line = self.lines[self.idx] if 0 <= self.idx < len(self.lines) else None
        return bool(line and len(line) > 2 and line[2]['chars'] and media.get('playing'))

    def show_lines(self, lines, idx):
        if lines is not self.lines:  # new song: jump, don't scroll through it
            self.lines, self.idx, self.scroll = lines, idx, float(idx)
            self.set_options(self.pron, self.trans)
        elif idx != self.idx:
            self.idx = idx
        if (self.scroll != self.idx or self.karaoke()) and not self.anim.isActive():
            self.anim.start()

    def step(self):
        if not self.isVisible():
            return self.anim.stop()
        d = self.idx - self.scroll
        if d:  # scrolling: whole view at 60 fps
            self.scroll = float(self.idx) if abs(d) < 0.01 or abs(d) > 5 else self.scroll + d * 0.2  # tiny or seek: jump
            self.anim.setInterval(16)
            self.update()
        elif self.karaoke():  # only the letter fill moves: just that line, 30 fps
            self.anim.setInterval(33)
            self.update(self.cur_rect)
        else:
            self.anim.stop()
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, mid, bh = self.width(), self.height() / 2, self.block_h()
        if not self.lines:
            p.setPen(QColor(DIM))
            p.setFont(self.small)
            p.drawText(self.rect(), Qt.AlignCenter, 'No synced lyrics for this song')
            return
        # split view: original on the left, romaji / translation on the right, same row per line
        split = w * 0.5 if self.rows else w
        if self.rows:
            div = QColor(FG)
            div.setAlphaF(0.12)
            p.fillRect(QRectF(split, 8, 1, self.height() - 16), div)
        left = QRectF(0, 0, split - 10, 0) if self.rows else QRectF(0, 0, w, 0)
        right_x, right_w = split + 14, w - split - 14
        only_current = self.cur_rect.contains(e.rect())  # a karaoke frame: skip the other lines
        reach = int(mid / bh) + 2
        for i in range(max(0, int(self.scroll) - reach), min(len(self.lines), int(self.scroll) + reach + 1)):
            line, current = self.lines[i], i == self.idx
            if only_current and not current:
                continue
            y = mid + (i - self.scroll) * bh
            edge = max(0.0, 1 - abs(y - mid) / (mid - self.LINE_H / 3))  # 1 in the middle, 0 at the edges
            fade = 1 if current else 0.5 * edge * edge
            box = QRectF(left.x(), y - self.LINE_H / 2, left.width(), self.LINE_H)
            if current:
                self.paint_current(p, line, box)
                if only_current:
                    return
            else:
                c = QColor(FG)
                c.setAlphaF(fade)
                p.setPen(c)
                p.setFont(self.fonts['small'])
                p.drawText(box, Qt.AlignCenter, self.elide(line[1] or '♪', 'small', box.width()))
            # right column: romaji big and in the song's color (easiest to read along), translation small below
            texts = [(row, t) for row in self.rows if (t := self.extra(line, row))]
            heights = [22 if row == 'pron' else self.EXTRA_H for row, _ in texts]
            top = y - sum(heights) / 2
            for (row, text), h in zip(texts, heights):
                font = ('romaji_now' if current else 'romaji') if row == 'pron' else 'small' if current else 'tiny'
                c = QColor(self.color if row == 'pron' else FG if current else DIM)
                c.setAlphaF(1 if current else fade * (1.6 if row == 'pron' else 1))
                p.setPen(c)
                p.setFont(self.fonts[font])
                p.drawText(QRectF(right_x, top, right_w, h), Qt.AlignLeft | Qt.AlignVCenter,
                           self.elide(text, font, right_w))
                top += h

    def paint_current(self, p, line, r):
        chars = line[2]['chars'] if len(line) > 2 else []
        if not self.cur or self.cur[:2] != (self.idx, int(r.width())):  # lay the line out once, not per frame
            text = ''.join(ch for _, _, ch in chars) if chars else (line[1] or '♪')
            font = QFont(self.fonts['big'])
            while font.pointSize() > 9 and QFontMetricsF(font).horizontalAdvance(text) > r.width() - 8:
                font.setPointSize(font.pointSize() - 1)  # long lines shrink instead of being cut
            fm = QFontMetricsF(font)
            text = fm.elidedText(text, Qt.ElideRight, r.width()) if not chars else text
            widths, acc = [0.0], ''
            for _, _, ch in chars:
                acc += ch
                widths.append(fm.horizontalAdvance(acc))
            self.cur = (self.idx, int(r.width()), font, text, widths)
        _, _, font, text, widths = self.cur
        self.cur_rect = r.toAlignedRect().adjusted(0, -2, 0, 2)
        p.setFont(font)
        if not chars:
            p.setPen(self.color)
            p.drawText(r, Qt.AlignCenter, text)
            return
        x0 = r.left() + (r.width() - widths[-1]) / 2
        base = QColor(FG)
        base.setAlphaF(0.35)
        p.setPen(base)
        p.drawText(r, Qt.AlignCenter, text)
        t, fill = lyric_time(), widths[-1]  # karaoke: color sweeps through each letter on its timing
        for k, (s, e, _) in enumerate(chars):
            if t < e:
                fill = widths[k] + max(0.0, min(1.0, (t - s) / (e - s) if e > s else 0)) * (widths[k + 1] - widths[k])
                break
        p.save()
        p.setClipRect(QRectF(x0, r.top(), fill, r.height()))
        p.setPen(self.color)
        p.drawText(r, Qt.AlignCenter, text)
        p.restore()


class Switch(QAbstractButton):  # macOS-style toggle
    def __init__(self, on, cb):
        super().__init__(checkable=True, checked=on)
        self.setFixedSize(38, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.toggled.connect(cb)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(GREEN if self.isChecked() else '#3a3a3c'))
        p.drawRoundedRect(QRectF(self.rect()), 11, 11)
        p.setBrush(QColor(FG))
        p.drawEllipse(QRectF(18 if self.isChecked() else 2, 2, 18, 18))


def icon_button(glyph, size, cb):
    b = QPushButton(glyph)
    f = QFont()
    f.setFamilies(['Segoe Fluent Icons', 'Segoe MDL2 Assets'])
    f.setPointSize(size)
    f.setStyleStrategy(QFont.NoSubpixelAntialias)  # ClearType leaves color fringes on a translucent window
    b.setFont(f)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)  # a focused button would keep the island open after the mouse leaves
    b.setStyleSheet(ICON_STYLE.format(color=FG))
    b.clicked.connect(cb)
    return b


ICON_STYLE = 'QPushButton{{color:{color};background:transparent;border:none;padding:4px 10px}}' \
             'QPushButton:hover{{color:#bbb}}'


def pill_button(text, cb):
    b = QPushButton(text, objectName='pill')
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    b.clicked.connect(cb)
    return b


def row(*items, spacing=8):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(it)
        else:
            lay.addWidget(it)
    return w


def column(*items, spacing=2):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for it in items:
        lay.addStretch(1) if it is None else lay.addWidget(it)
    return w


STYLE = f'''
QLineEdit, QTimeEdit {{ background:{PANEL}; color:white; border:none; border-radius:8px; padding:4px 8px;
                       font:10pt "{FONT}"; }}
QTimeEdit::up-button, QTimeEdit::down-button {{ width:0; }}
QPushButton#pill {{ background:{PANEL}; color:white; border:none; border-radius:10px; padding:4px 12px; font:9pt "{FONT}"; }}
QPushButton#pill:hover {{ background:#3a3a3c; }}
QMenu {{ background:{PANEL}; color:white; border:none; }}
QListWidget {{ background:{PANEL}; color:white; border:none; border-radius:8px; padding:2px; font:9pt "{FONT}";
               outline:0; }}
QListWidget::item:selected {{ background:{BLUE}; border-radius:4px; }}
QCalendarWidget QWidget {{ background:{BG}; color:white; alternate-background-color:{BG}; }}
QCalendarWidget QAbstractItemView {{ selection-background-color:{BLUE}; selection-color:white; font:9pt "{FONT}";
                                     outline:0; }}
QCalendarWidget QAbstractItemView:disabled {{ color:#48484a; }}
QCalendarWidget QToolButton {{ color:white; background:transparent; border:none; font:bold 10pt "{FONT}"; padding:2px 6px; }}
QCalendarWidget QToolButton:hover {{ background:{PANEL}; border-radius:6px; }}
QCalendarWidget QToolButton::menu-indicator {{ image:none; }}
QCalendarWidget QSpinBox {{ background:{PANEL}; color:white; border:none; }}
QComboBox {{ background:{PANEL}; color:white; border:none; border-radius:8px; padding:3px 10px; font:9pt "{FONT}"; }}
QComboBox::drop-down {{ border:none; width:0; }}
QComboBox QAbstractItemView {{ background:{PANEL}; color:white; selection-background-color:{BLUE}; border:none; }}
'''


# ---------- island ----------
class Island(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        first_run = not SETTINGS_FILE.exists()
        self.settings, self.tiny, self.fs_at = load_settings(), False, 0
        self.updating, self.quit_now = None, False  # set by the update thread, acted on in tick
        if first_run and FROZEN:  # freshly installed: start with Windows by default (toggle in Settings)
            autostart(True)
            self.set_setting('auto_update', True)
        for key, options in (('translation', TRANSLATIONS), ('pronunciation', PRONUNCIATIONS)):
            if self.settings[key] not in [v for _, v in options]:  # e.g. Korean from an older version
                self.settings[key] = None
        prefs.update(lang=self.settings['translation'], romaji=bool(self.settings['pronunciation']))
        self.place()
        self.expanded, self.tab, self.alert, self.alert_at = False, None, None, 0
        self.timer_end, self.reminders, self.art_key, self.goal = None, load_reminders(), False, None
        self.shape = QRectF()
        self.anim = QVariantAnimation(self, duration=550)
        self.anim.valueChanged.connect(self.set_shape)
        self.anim.finished.connect(lambda: self.setMask(self.shape.toAlignedRect()))
        self.build()
        self.shape = self.target()
        self.morph()
        self.tick_timer = QTimer(self, timeout=self.tick, interval=100)
        self.tick_timer.start()
        QTimer(self, timeout=self.stats, interval=1000).start()
        self.stats()
        if FROZEN:  # source runs update with git; the installed .exe checks GitHub a minute after start, then every 6 h
            QTimer.singleShot(60_000, self.check_update)
            QTimer(self, timeout=self.check_update, interval=6 * 3600_000).start()

    def check_update(self):
        def run():
            try:
                found = newer_release()
                if found:
                    self.updating = found[0]
                    install_update(found[1])
                    self.quit_now = True  # the installer starts the new version
            except Exception:  # offline or GitHub down: try again next time
                self.updating = None
        if self.settings['auto_update'] and not self.updating:
            threading.Thread(target=run, daemon=True).start()

    def build(self):
        # compact: art | text | waveform or countdown
        self.c_art = Art(24, 7)
        self.c_text = Text(10, bold=True, align=Qt.AlignCenter)
        self.c_wave = Wave(20, 14)
        self.c_timer = Text(10, ORANGE, bold=True, align=Qt.AlignRight)
        self.c_timer.setFixedWidth(46)
        self.compact = row(self.c_art, self.c_text, self.c_wave, self.c_timer, spacing=8)
        self.compact.setParent(self)
        self.compact.layout().setContentsMargins(7, 6, 14, 6)
        self.compact.layout().setStretch(1, 1)

        # expanded header: date left, tabs right
        self.f_date = Text(8, DIM)
        self.tab_buttons = [icon_button(g, 10, lambda _=False, t=t: self.set_tab(t))
                            for g, t in zip((MUSIC, LYRICS, SYSTEM, TIMER, CALENDAR, GEAR), TABS)]
        header = row(self.f_date, None, *self.tab_buttons, spacing=0)

        # media page
        self.f_art = Art(64, 14)
        self.f_title = Text(12, bold=True)
        self.f_artist = Text(10, DIM)
        self.f_lyric = Text(11, GREEN)
        self.f_wave = Wave(26, 20)
        self.f_pos, self.f_left = Text(8, DIM), Text(8, DIM, align=Qt.AlignRight)
        self.f_pos.setFixedWidth(40)
        self.f_left.setFixedWidth(40)
        self.f_bar = Bar()
        self.play = icon_button(PLAY, 16, lambda: control('try_toggle_play_pause_async'))
        info = column(self.f_title, self.f_artist, self.f_lyric, None)
        media_page = column(
            row(self.f_art, info, self.f_wave, spacing=14),
            row(self.f_pos, self.f_bar, self.f_left),
            row(None, icon_button(PREV, 14, lambda: control('try_skip_previous_async')), self.play,
                icon_button(NEXT, 14, lambda: control('try_skip_next_async')), None, spacing=18),
            spacing=8)
        media_page.layout().setStretch(0, 1)

        # lyrics page: song line, scrolling lyrics
        self.l_song = Text(9, DIM, align=Qt.AlignCenter)
        self.lyrics = Lyrics()
        self.lyrics.set_options(self.settings['pronunciation'], self.settings['translation'])
        self.l_source = Text(8, DIM)
        self.b_sync = pill_button('✨ Sync to this video', self.request_sync)
        self.b_sync.setToolTip('Everyric aligns the lyrics to this exact video (letter timing, 15 per day)')
        bottom = row(self.l_source, self.b_sync, spacing=6)
        bottom.layout().setStretch(0, 1)
        lyrics_page = column(self.l_song, self.lyrics, bottom, spacing=4)
        lyrics_page.layout().setStretch(1, 1)

        # system page: graphs | top programs
        self.g_cpu, self.g_ram = Graph('CPU', BLUE), Graph('RAM', PURPLE)
        self.s_extra = Text(8, DIM)
        self.procs = [(Text(9), Text(9, BLUE, align=Qt.AlignRight), Text(9, DIM, align=Qt.AlignRight))
                      for _ in range(5)]
        for _, cpu, ram in self.procs:
            cpu.setFixedWidth(44)
            ram.setFixedWidth(64)
        heading = Text(8, DIM)
        heading.set('TOP PROGRAMS')
        system_page = row(column(self.g_cpu, self.g_ram, self.s_extra, spacing=4),
                          column(heading, *(row(*r, spacing=4) for r in self.procs), None, spacing=3), spacing=18)
        for i in (0, 1):
            system_page.layout().setStretch(i, 1)

        # timer page: big countdown, presets
        self.t_big = Text(30, ORANGE, bold=True, align=Qt.AlignCenter)
        self.e_timer = QLineEdit('5')
        self.e_timer.setFixedWidth(44)
        self.e_timer.returnPressed.connect(self.toggle_timer)
        self.b_timer = pill_button('Start', self.toggle_timer)
        unit = Text(9, DIM)
        unit.set('min')
        unit.setFixedWidth(24)
        presets = [pill_button(f'{m}m', lambda _=False, m=m: self.start_timer(m)) for m in (1, 5, 10, 25)]
        timer_page = column(None, self.t_big, row(None, *presets, None, spacing=6),
                            row(None, self.e_timer, unit, self.b_timer, None, spacing=6), None, spacing=8)

        # calendar page: pick a day | time + text, that day's reminders
        self.cal = QCalendarWidget(gridVisible=False, verticalHeaderFormat=QCalendarWidget.NoVerticalHeader,
                                   minimumDate=QDate.currentDate())
        self.cal.setFixedSize(260, 190)
        self.cal.setFirstDayOfWeek(Qt.Monday)
        plain = QTextCharFormat()
        plain.setForeground(QColor(FG))
        for day in (Qt.Saturday, Qt.Sunday):  # default paints weekends red
            self.cal.setWeekdayTextFormat(day, plain)
        head = QTextCharFormat()
        head.setForeground(QColor(DIM))
        self.cal.setHeaderTextFormat(head)
        self.cal.selectionChanged.connect(self.refresh_day)
        self.c_day = Text(10, bold=True)
        self.e_time = QTimeEdit(displayFormat='HH:mm')
        self.e_time.setFixedWidth(64)
        self.e_rem = QLineEdit(placeholderText='What to remind?')
        self.e_rem.returnPressed.connect(self.add_reminder)
        self.t_rem = Text(8, DIM)
        self.day_list = QListWidget()
        calendar_page = row(self.cal, column(
            self.c_day,
            row(self.e_time, self.e_rem, pill_button('Add', self.add_reminder), spacing=6),
            self.day_list,
            row(self.t_rem, pill_button('Remove', self.remove_reminder), spacing=6), spacing=6), spacing=16)
        self.refresh_day()

        # settings page
        def setting(text, widget):
            t = Text(10)
            t.set(text)
            return row(t, widget, spacing=12)

        def toggle(key):
            return Switch(self.settings[key], lambda on: self.set_setting(key, on))

        self.screen_box = QComboBox()
        self.screen_box.addItems([f'{i + 1}: {s.name()}  {s.size().width()}×{s.size().height()}'
                                  for i, s in enumerate(QApplication.screens())])
        self.screen_box.setCurrentIndex(min(self.settings['screen'], self.screen_box.count() - 1))
        self.screen_box.currentIndexChanged.connect(lambda i: (self.set_setting('screen', i), self.place()))
        def choice(key, options):
            box = QComboBox()
            box.addItems([label for label, _ in options])
            box.setCurrentIndex([v for _, v in options].index(self.settings[key]))
            box.currentIndexChanged.connect(lambda i: self.set_lyric_option(key, options[i][1]))
            return box

        settings_page = column(
            setting('Start with Windows', Switch(autostart(), autostart)),
            setting('Tiny island in fullscreen apps and games', toggle('tiny_fullscreen')),
            setting('Lyrics in small island', toggle('compact_lyrics')),
            setting('Lyrics translation', choice('translation', TRANSLATIONS)),
            setting('Lyrics pronunciation', choice('pronunciation', PRONUNCIATIONS)),
            setting('Alert sound', toggle('sound')),
            setting(f'Auto update  ·  v{VERSION}', toggle('auto_update')),
            setting('Screen', self.screen_box),
            row(None, pill_button('Quit island', QApplication.quit)), spacing=6)

        # alert page
        self.a_text = Text(14, ORANGE, bold=True, align=Qt.AlignCenter)
        alert_page = column(None, self.a_text, row(None, pill_button('Dismiss', self.dismiss), None), None,
                            spacing=12)

        self.pages = QStackedWidget()
        for name, page in zip(TABS + ['alert'], (media_page, lyrics_page, system_page, timer_page, calendar_page,
                                                 settings_page, alert_page)):
            page.setObjectName(name)
            self.pages.addWidget(page)
        self.pages.setCurrentWidget(alert_page)  # first tick then switches page and colors the tabs
        self.full = column(header, self.pages, spacing=4)
        self.full.setParent(self)
        self.full.layout().setContentsMargins(22, 10, 22, 14)

        self.c_fx, self.f_fx = QGraphicsOpacityEffect(self.compact), QGraphicsOpacityEffect(self.full)
        self.compact.setGraphicsEffect(self.c_fx)
        self.full.setGraphicsEffect(self.f_fx)

    # --- shape + animation ---
    def place(self):
        screens = QApplication.screens()
        scr = screens[min(self.settings['screen'], len(screens) - 1)].geometry()
        self.setGeometry(scr.x() + (scr.width() - WIN[0]) // 2, scr.y(), *WIN)

    def set_setting(self, key, value):
        self.settings[key] = value
        SETTINGS_FILE.write_text(json.dumps(self.settings), 'utf-8')

    def set_lyric_option(self, key, value):
        self.set_setting(key, value)
        self.lyrics.set_options(self.settings['pronunciation'], self.settings['translation'])
        prefs.update(lang=self.settings['translation'], romaji=bool(self.settings['pronunciation']))
        media['reload'] = True  # fetch again with the new translation / romaji

    def request_sync(self):
        key, vid, plain = (media.get('title'), media.get('artist')), media.get('video_id'), media.get('plain')
        if vid and plain and not media.get('gen'):
            media['gen'] = 'Starting…'
            threading.Thread(target=generate_sync, args=(key, vid, plain, everyric_lang()), daemon=True).start()

    def page(self):
        return 'alert' if self.alert else self.tab or ('music' if media.get('title') else 'system')

    def target(self):
        if self.expanded:
            w, h = SIZES[self.page()]
            if self.page() == 'lyrics' and self.lyrics.rows:  # split view needs the width
                w, h = 720, h + 14 * len(self.lyrics.rows)
        elif self.tiny and not self.alert:
            w, h = TINY
            return QRectF((self.width() - w) / 2, 2, w, h)
        else:
            w, h = (320 if self.alert or media.get('playing') or self.timer_end else 150), COMPACT_H
        return QRectF((self.width() - w) / 2, TOP, w, h)

    def morph(self):
        t = self.target()
        if t == self.goal:
            return
        self.goal = t
        if self.expanded:
            self.full.setGeometry(t.toRect())
        self.anim.stop()
        self.anim.setStartValue(self.shape)
        self.anim.setEndValue(t)
        grow = t.height() >= self.shape.height()
        self.anim.setEasingCurve(QEasingCurve.OutBack if grow else QEasingCurve.OutCubic)
        self.anim.setDuration(550 if grow else 380)
        # one mask for the whole animation (room for the spring overshoot) instead of a new one every frame
        self.setMask(self.shape.united(t).adjusted(-30, 0, 30, 30).toAlignedRect())
        self.anim.start()

    def set_shape(self, r):
        self.shape = r
        k = max(0.0, min(1.0, (r.height() - COMPACT_H) / (160 - COMPACT_H)))  # 0 compact .. 1 expanded
        self.compact.setGeometry(r.toRect())
        self.c_fx.setOpacity(max(0.0, 1 - k * 4))
        self.f_fx.setOpacity(max(0.0, k * 2 - 1) if self.expanded else 0)
        self.compact.setVisible(k < 0.25 and r.height() > COMPACT_H - 8)  # hidden in the tiny pill
        self.full.setVisible(self.expanded and k > 0.5)
        if self.anim.state() != QVariantAnimation.Running:  # mid-animation mask is set once in morph
            self.setMask(r.toAlignedRect())  # clicks outside the pill reach the windows below
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(BG))
        r = min(self.shape.height() / 2, 34)
        p.drawRoundedRect(self.shape, r, r)

    # --- hover ---
    def enterEvent(self, e):
        self.expanded = True
        self.tick()  # fill the content now; collapsed ticks can be 0.5 s apart

    def leaveEvent(self, e):
        QTimer.singleShot(350, self.maybe_collapse)

    def changeEvent(self, e):
        if e.type() == e.Type.ActivationChange and not self.isActiveWindow():
            self.maybe_collapse()

    def maybe_collapse(self):
        inside = self.shape.contains(QPointF(self.mapFromGlobal(QCursor.pos())))
        focus = QApplication.focusWidget()
        typing = self.isActiveWindow() and focus is not None and self.full.isAncestorOf(focus)
        if not inside and not typing and not self.alert:
            self.expanded, self.tab = False, None
            self.morph()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction('Quit', QApplication.quit)
        m.exec(e.globalPos())

    def set_tab(self, tab):
        self.tab = tab
        self.tick()

    # --- features ---
    def toggle_timer(self):
        if self.timer_end:
            self.timer_end = None
        else:
            try:
                self.timer_end = time.time() + float(self.e_timer.text()) * 60
            except ValueError:
                pass

    def start_timer(self, minutes):
        self.e_timer.setText(str(minutes))
        self.timer_end = time.time() + minutes * 60

    # --- calendar reminders ---
    def save_reminders(self):
        SAVE.write_text(json.dumps(self.reminders), 'utf-8')
        self.refresh_day()

    def day_reminders(self):
        day = self.cal.selectedDate().toPython()
        return [r for r in self.reminders if dt.date.fromtimestamp(r[0]) == day]

    def refresh_day(self):
        day = self.cal.selectedDate()
        self.c_day.set(day.toString('dddd d MMMM'))
        if day == QDate.currentDate():  # suggest the next round hour today
            self.e_time.setTime(QTime(min(23, QTime.currentTime().hour() + 1), 0))
        self.day_list.clear()
        for t, msg in self.day_reminders():
            self.day_list.addItem(f'{time.strftime("%H:%M", time.localtime(t))}   {msg}')
        self.cal.setDateTextFormat(QDate(), QTextCharFormat())  # clears every marked day
        mark = QTextCharFormat()
        mark.setForeground(QColor(ORANGE))
        mark.setFontWeight(QFont.Bold)
        for t, _ in self.reminders:
            self.cal.setDateTextFormat(QDate(dt.date.fromtimestamp(t)), mark)
        self.t_rem.set(f'{len(self.reminders)} upcoming' if self.reminders else 'No reminders', DIM)

    def add_reminder(self):
        due = dt.datetime.combine(self.cal.selectedDate().toPython(), self.e_time.time().toPython())
        if due <= dt.datetime.now():
            self.t_rem.set('That time already passed', RED)
            return
        self.reminders.append((due.timestamp(), self.e_rem.text().strip() or 'Reminder'))
        self.reminders.sort()
        self.e_rem.clear()
        self.save_reminders()

    def remove_reminder(self):
        i = self.day_list.currentRow()
        if i >= 0:
            self.reminders.remove(self.day_reminders()[i])
            self.save_reminders()

    def fire(self, msg):
        self.alert, self.alert_at = msg, time.time()
        self.expanded = True
        self.goal = None  # force re-morph even if already expanded
        for _ in range(3 if self.settings['sound'] else 0):
            winsound.MessageBeep(winsound.MB_ICONASTERISK)

    def dismiss(self):
        self.alert = None
        QTimer.singleShot(1200, self.maybe_collapse)

    def stats(self):
        ram = psutil.virtual_memory()
        self.g_cpu.add(psutil.cpu_percent())
        self.g_ram.add(ram.percent)
        if not (self.expanded and self.page() == 'system'):  # graphs keep their history, the rest can wait
            return
        bat = psutil.sensors_battery()
        self.s_extra.set(f'RAM {ram.used / 2**30:.1f} / {ram.total / 2**30:.0f} GB   '
                         f'DISK C {psutil.disk_usage("C:\\").percent:.0f}%'
                         + (f'   BAT {bat.percent:.0f}%{" ⚡" if bat.power_plugged else ""}' if bat else ''))
        for (name, cpu, ram_t), proc in zip(self.procs, top_procs + [None] * 5):
            name.set(proc[0] if proc else '')
            cpu.set(f'{proc[1]:.1f}%' if proc else '')
            ram_t.set(f'{proc[2]:,.0f} MB' if proc else '')

    def tick(self):
        now = time.time()
        if self.quit_now:
            return QApplication.quit()
        if self.updating and not self.alert:
            self.alert, self.alert_at = f'Updating to v{self.updating}…', now  # quiet, no beep
        if self.timer_end and now >= self.timer_end:
            self.timer_end = None
            self.fire('Timer done')
        while self.reminders and now >= self.reminders[0][0]:
            self.fire(self.reminders.pop(0)[1])
            self.save_reminders()
        if self.alert and now - self.alert_at > 20:
            self.dismiss()
        if now - self.fs_at >= 0.5:  # fullscreen check twice a second
            self.fs_at = now
            self.tiny = self.settings['tiny_fullscreen'] and fullscreen_in_front(int(self.winId()))

        if media.get('art_key') != self.art_key:
            self.art_key = media.get('art_key')
            pix = QPixmap()
            if media.get('art'):
                pix.loadFromData(media['art'])
            pix = None if pix.isNull() else pix
            color = tint(pix) if pix else QColor(GREEN)
            for w in (self.c_art, self.f_art):
                w.set(pix)
            self.c_wave.color = self.f_wave.color = self.lyrics.color = color
            self.f_lyric.set(self.f_lyric.text, color)

        title, playing, lyric = media.get('title', ''), bool(media.get('playing')), current_lyric()
        left = int(self.timer_end - now) if self.timer_end else None
        timer = mmss(left) if left is not None else ''

        # compact
        self.c_art.setVisible(bool(title) and not self.alert)
        self.c_wave.set_on(playing)
        self.f_wave.set_on(playing)
        self.c_wave.setVisible(playing and not timer)
        self.c_timer.setVisible(bool(timer))
        self.c_timer.set(timer)
        if self.alert:
            self.c_text.set(self.alert, ORANGE)
        elif lyric and self.settings['compact_lyrics']:
            self.c_text.set(lyric, self.c_wave.color)
        else:
            self.c_text.set(title if playing else time.strftime('%H:%M'), FG)

        # fast ticks only while something moves: synced lyrics, the open island, a countdown
        fast = self.expanded or (lyric and self.settings['compact_lyrics'])
        self.tick_timer.setInterval(100 if fast else 250 if timer else 500)
        page = self.page()
        want = self.expanded and page == 'system'
        if want != procs_wanted.is_set():
            procs_wanted.set() if want else procs_wanted.clear()
        if not self.expanded:
            self.morph()
            return

        # expanded
        self.f_date.set(time.strftime('%a %d %b  %H:%M'))
        self.f_title.set(title or 'Nothing playing')
        self.f_artist.set(media.get('artist', ''))
        self.f_lyric.set(lyric)
        if page == 'lyrics':
            self.l_song.set(f'{title} · {media.get("artist", "")}' if title else 'Nothing playing')
            self.lyrics.show_lines(media.get('lyrics') or [], lyric_index())
            src = media.get('source')
            self.l_source.set(media.get('gen') or ('Everyric · synced to this video' if src == 'everyric' else
                                                    'Line sync · lrclib / NetEase' if src == 'line' else ''))
            self.b_sync.setVisible(bool(media.get('video_id') and media.get('plain') and src != 'everyric'
                                        and not media.get('gen')))
        if self.play.text() != (PAUSE if playing else PLAY):
            self.play.setText(PAUSE if playing else PLAY)
        length, pos = media.get('length', 0), position()
        v = pos / length if length > 0 else 0
        if v != self.f_bar.v:
            self.f_bar.v = v
            self.f_bar.update()
        self.f_pos.set(mmss(pos) if length > 0 else '')
        self.f_left.set('-' + mmss(length - pos) if length > 0 else ('LIVE' if title else ''),
                        DIM if length > 0 else RED)
        if self.b_timer.text() != ('Stop' if self.timer_end else 'Start'):
            self.b_timer.setText('Stop' if self.timer_end else 'Start')
        self.t_big.set(timer or '0:00', ORANGE if timer else DIM)
        if page != self.pages.currentWidget().objectName():
            self.pages.setCurrentWidget(self.pages.findChild(QWidget, page))
            for b, t in zip(self.tab_buttons, TABS):
                b.setStyleSheet(ICON_STYLE.format(color=FG if t == page else '#636366'))
        self.a_text.set(self.alert or '')
        self.morph()


def selftest():
    assert clean_title('Artist - Song (Official Music Video) [4K]') == 'Artist - Song'
    assert clean_title('YOASOBI「アイドル」 Official Music Video') == 'YOASOBI「アイドル」'
    assert clean_title('Song | Live at X') == 'Song'
    assert parse_lrc('[00:12.50] hello\n[01:02.00]world\nno tag') == [(12.5, 'hello'), (62.0, 'world')]
    assert parse_lrc('[00:00.00] 作词 : X\n[00:10.92]動かない') == [(10.92, '動かない')]
    assert lyric_queries('【鳴潮】『風の名前』/ MIMI feat.可不', 'MIMI')[0] == '風の名前 MIMI'
    assert '魔女（真） V.W.P' in lyric_queries('【Live映像】魔女（真） - CHRONICLE MOVIE -【V.W.P】', 'V.W.P -X-')
    assert '夏が僕らを呼ぶのなら *Luna' in lyric_queries('*Luna - 夏が僕らを呼ぶのなら (If Summer) feat.ねんね | 2025', '*Luna')
    assert clean_artist('V.W.P -Virtual Witch Phenomenon-') == 'V.W.P'
    assert clean_artist('AdeleVEVO') == 'Adele' and clean_artist('Aimer - Topic') == 'Aimer'
    media.update(lyrics=[(10.0, 'a'), (20.0, 'b')], pos=15.0, pos_at=time.time(), playing=False)
    assert lyric_index() == 0 and current_lyric() == ''  # paused: index kept, compact line hidden
    assert enrich([(1.0, 'a')], None, False) == [(1.0, 'a', {'chars': [], 'pron': {}, 'trans': {}})]
    media.update(offset=6.0)  # Everyric's per-video offset shifts lyric time forward
    assert lyric_index() == 1
    media.update(offset=0)
    media.update(pos=5.0)
    assert lyric_index() == -1
    media.clear()
    assert mmss(125.7) == '2:05' and mmss(-3) == '0:00'
    assert snapshot()[os.getpid()][0].lower().startswith('python')
    assert single_instance()  # first holder of the mutex in this process
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['test']:
        selftest()
    elif single_instance():
        threading.Thread(target=lambda: asyncio.run(media_loop()), daemon=True).start()
        threading.Thread(target=scan_processes, daemon=True).start()
        app = QApplication(sys.argv)
        app.setStyleSheet(STYLE)
        island = Island()
        island.show()
        app.exec()

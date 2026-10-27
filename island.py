"""Dynamic Island for Windows, macOS style. Tabs: music (album art, synced lyrics, controls),
system (CPU/RAM graphs, top programs), timer, calendar reminders.

Run: pythonw island.py      (right-click the island to quit)
Self-check: python island.py test
"""
import asyncio, bisect, calendar, collections, ctypes, datetime as dt, faulthandler, functools, json, math, os, pathlib, random, re, sys, threading, time, urllib.parse
import http.server, ipaddress, secrets, shutil, socket, subprocess, urllib.request, winreg, winsound

import psutil
import segno
from PySide6.QtCore import (Qt, QBuffer, QDate, QFileInfo, QLocale, QMimeData, QPoint, QPointF, QRect, QRectF, QSize, QTime, QTimer, QUrl,
                            QVariantAnimation, QEasingCurve)
from PySide6.QtGui import QColor, QCursor, QFont, QFontDatabase, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QTextCharFormat
from PySide6.QtWidgets import (QAbstractButton, QApplication, QCalendarWidget, QComboBox, QFileIconProvider,
                               QGraphicsOpacityEffect, QHBoxLayout, QLineEdit, QListView, QListWidget, QListWidgetItem,
                               QLabel, QMenu, QPushButton, QStackedWidget, QTimeEdit, QVBoxLayout, QWidget)
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapDecoder, BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import Buffer, DataWriter, InMemoryRandomAccessStream, InputStreamOptions

# Sakura Fox Island theme: same names and values as the design system (see DESIGN.md). Change colours here only.
THEME = {'ground': '#191919', 'surface': '#231f21', 'surface-2': '#2e282b', 'line': '#3d3438', 'ink': '#fff4f8',
         'muted': '#b3a1aa', 'faint': '#7d6e75', 'sakura': '#ff9ec8', 'sakura-deep': '#f06aa6',
         'sakura-tint': '#3a2430', 'on-sakura': '#2a1520', 'fox': '#ffa86b', 'fox-cream': '#fff4e8',
         'fox-ink': '#2b1d14', 'mint': '#86ebc0', 'coral': '#ff7f98', 'lavender': '#c4a8ff', 'sky': '#8ccfff',
         'butter': '#ffe29a'}
BG, PANEL, FG, DIM, FAINT = THEME['ground'], THEME['surface-2'], THEME['ink'], THEME['muted'], THEME['faint']
SAKURA, ON_SAKURA, LINE = THEME['sakura'], THEME['on-sakura'], THEME['line']
GREEN, ORANGE, RED = THEME['mint'], THEME['fox'], THEME['coral']  # money in · timers and alerts · money out
BLUE, PURPLE = THEME['sky'], THEME['lavender']  # CPU and RAM graphs
ROUND = 'Kiwi Maru'  # the round face for big numbers: timer, money totals
FONT = 'Noto Sans'  # Japanese falls back to Kiwi Maru, Thai to Noto Sans Thai (see load_fonts)
FONT_DIR = pathlib.Path(getattr(sys, '_MEIPASS', pathlib.Path(__file__).parent)) / 'fonts'  # bundled in the .exe
PREV, PLAY, PAUSE, NEXT = '\uE892', '\uE768', '\uE769', '\uE893'
MUSIC, SYSTEM, TIMER, CALENDAR = '\uE8D6', '\uE9D9', '\uE916', '\uE787'
PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
COMPACT_H, TOP, WIN = 36, 8, (820, 470)
GEAR, LYRICS = '', ''
SHELF, MONEY, CLAUDE = '', '', ''
TABS = ['music', 'lyrics', 'shelf', 'money', 'system', 'claude', 'timer', 'calendar', 'settings']
SIZES = {'music': (460, 200), 'lyrics': (560, 260), 'shelf': (540, 210), 'money': (720, 434), 'system': (540, 220), 'claude': (700, 220),
         'timer': (400, 180), 'calendar': (620, 284), 'settings': (800, 350), 'alert': (400, 160),
         'guide': (600, 300)}
LEAD = 0.3  # seconds a lyric line shows before it is sung
TINY = (110, 8)  # island while a fullscreen app or game is in front
VERSION = '1.4.0'
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
SHELF_FILE = DATA / 'shelf.json'
MONEY_FILE = DATA / 'money.json'
DELETED_FILE = DATA / 'money-deleted.json'
MONEY_PORT = 8765
if FROZEN:  # a windowed .exe has no console: keep errors in a file so bugs can be traced
    _log = open(DATA / 'error.log', 'a', encoding='utf-8', buffering=1)
    sys.stderr = _log
    faulthandler.enable(_log)
    threading.excepthook = lambda a: sys.excepthook(a.exc_type, a.exc_value, a.exc_traceback)
DEFAULTS = {'tiny_fullscreen': True, 'compact_lyrics': True, 'sound': True, 'screen': 0,
            'translation': None, 'pronunciation': None, 'auto_update': True, 'phone_sync': False, 'phone_key': None,
            'fox': True, 'fox_sleep': 7200, 'payday': 26, 'currency': '฿', 'prefs_mt': 0,
            'sync_url': None, 'sync_key': None}
FOX_SLEEP_AFTER = [('30 min', 1800), ('1 hour', 3600), ('2 hours', 7200), ('3 hours', 10800)]
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


# ---------- money: KBank SMS / email / slip text from the iPhone -> income and expense ----------
AMOUNT = r'(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{1,2}|\d+)'
IN_WORDS = ('เงินเข้า', 'รับโอน', 'รับเงิน', 'ได้รับ', 'ฝากเงิน', 'โอนเข้า', 'received', 'deposit', 'incoming',
            'transfer in', 'credited')
OUT_WORDS = ('เงินออก', 'ถอน', 'โอนเงิน', 'โอนออก', 'ชำระ', 'จ่าย', 'หักบัญชี', 'ซื้อ', 'paid', 'payment', 'withdraw',
             'debited', 'transfer to', 'purchase')
THAI_MONTHS = ('ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.')
money, money_lock = [], threading.Lock()  # [{id, ts, amount, kind 'in'|'out', note, source, raw, mt}]
money_deleted = {}  # id -> when deleted, so a sync from the other PC doesn't bring it back
sync_state, sync_wake = {'at': 0, 'error': None}, threading.Event()  # last sync with the main PC
money_last = {'at': 0}  # newest added entry, for the pill flash


def thai_year(y):
    """K PLUS writes Buddhist years, often two digits: 69 -> 2026, 2569 -> 2026."""
    y = int(y)
    if y < 100:
        return 2500 + y - 543 if y >= 60 else 2000 + y
    return y - 543 if y > 2400 else y


def parse_tx(text, source):
    """Read a KBank SMS, alert email or slip (OCR text) -> {'ts', 'amount', 'kind'} or None.
    shortcut: keyword rules from common K PLUS wording; unknown wording falls back to "out", fixable in the app."""
    text = re.sub(r'(?<=[฀-๿]) +(?=[฀-๿])', '', text)  # OCR puts spaces inside Thai words
    low = text.lower()
    # balances and fees are amounts too, but not the transaction's
    body = re.sub(r'(คงเหลือ|ยอดเงินคงเหลือ|balance|available|ค่าธรรมเนียม|fee)[^\d]{0,20}' + AMOUNT, ' ', text,
                  flags=re.I)
    m = (re.search(r'(?:จำนวน(?:เงิน)?|amount(?:\s*\(thb\))?|ยอดเงิน)\s*[:：]?\s*' + AMOUNT, body, re.I)
         or re.search(AMOUNT + r'\s*(?:บาท|บ\.?(?![ก-๙])|thb|baht)', body, re.I)
         or re.search(r'\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2}', body))  # keeps "1,280.50" whole
    if not m:
        return None
    amount = float((m.group(1) if m.groups() else m.group(0)).replace(',', ''))
    if amount <= 0:
        return None
    kind = 'in' if any(w in low for w in IN_WORDS) else 'out'
    ts = time.time()
    d = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})\D{0,3}(\d{1,2})[:.](\d{2})', text)
    t = re.search(r'(\d{1,2})\s*(' + '|'.join(map(re.escape, THAI_MONTHS)) + r')\s*(\d{2,4})\D{0,6}(\d{1,2})[:.](\d{2})',
                  text)
    dated = False
    try:
        if d:
            ts, dated = dt.datetime(thai_year(d[3]), int(d[2]), int(d[1]), int(d[4]), int(d[5])).timestamp(), True
        elif t:
            ts, dated = dt.datetime(thai_year(t[3]), THAI_MONTHS.index(t[2]) + 1, int(t[1]), int(t[4]),
                                    int(t[5])).timestamp(), True
    except ValueError:  # impossible date in the text: keep "now"
        pass
    return {'ts': ts, 'amount': amount, 'kind': kind, 'dated': dated}


def load_money():
    try:
        money[:] = json.loads(MONEY_FILE.read_text('utf-8'))
    except (OSError, ValueError):
        money[:] = []
    for t in money:  # older versions named entries after the bank
        t['note'] = {'KBank slip': 'Slip', 'KBank': 'SMS'}.get(t.get('note'), t.get('note', ''))
    try:
        money_deleted.update(json.loads(DELETED_FILE.read_text('utf-8')))
    except (OSError, ValueError):
        pass


def save_money():
    for path, data in ((MONEY_FILE, money), (DELETED_FILE, money_deleted)):
        tmp = path.with_suffix('.tmp')  # write then swap: a crash mid-write must not lose the history
        tmp.write_text(json.dumps(data, ensure_ascii=False), 'utf-8')
        tmp.replace(path)


def merge_money(items, deleted):
    """Take entries and deletes from the other PC: a delete always wins, otherwise the newer edit (mt).
    True when something changed here."""
    changed = False
    with money_lock:
        for i, when in deleted.items():
            if i not in money_deleted:
                money_deleted[i], changed = when, True
        mine = {t['id']: t for t in money}
        for t in items:
            old = mine.get(t.get('id'))
            if t.get('id') in money_deleted or (old and old.get('mt', 0) >= t.get('mt', 0)):
                continue
            mine[t['id']], changed = {**t, 'raw': t.get('raw') or (old or {}).get('raw', '')}, True
        if changed:
            money[:] = sorted((t for t in mine.values() if t['id'] not in money_deleted), key=lambda x: x['ts'])
            save_money()
    return changed


def add_tx(tx, source, raw=''):
    """Store a transaction; the same transfer often arrives as SMS, email and slip, so merge those."""
    with money_lock:
        for old in money:
            if old['kind'] == tx['kind'] and abs(old['amount'] - tx['amount']) < 0.005 \
                    and abs(old['ts'] - tx['ts']) < 180:
                if source not in old['source']:
                    old['source'] += f'+{source}'
                    old['mt'] = time.time()
                    save_money()
                return old
        new = {'id': secrets.token_hex(4), 'ts': tx['ts'], 'amount': round(tx['amount'], 2), 'kind': tx['kind'],
               'note': tx.get('note', ''), 'cat': tx.get('cat', ''), 'source': source, 'raw': raw[:500],
               'mt': time.time()}
        money.append(new)
        money.sort(key=lambda x: x['ts'])
        save_money()
        money_last.update(tx=new, at=time.time())  # the island flashes it on the pill
        return new


# payday: with no 💼 Salary entry yet, cycles start on this day of the month. Set in Settings or on the phone.
money_prefs = {'payday': 26, 'currency': '฿'}
CURRENCIES = ['฿', '$', '€', '£', '¥', '₩', '₹', '₱', '₫', 'RM', 'Rp', 'S$']


def on_day(d, day):  # the 31st in a 30-day month is its last day
    return d.replace(day=min(day, calendar.monthrange(d.year, d.month)[1]))


def shift_month(d, months):
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    return d.replace(year=y, month=m + 1, day=min(d.day, calendar.monthrange(y, m + 1)[1]))


def pay_cycle(now):
    """(start, next payday) as midnights. A cycle starts on the day the latest 💼 Salary came in; without one in
    the last 40 days, on the payday. The next payday is counted as the payday of the next month (the earliest
    the money comes), so "can spend per day" never assumes money that isn't there yet."""
    today, payday = now.replace(hour=0, minute=0, second=0, microsecond=0), money_prefs['payday']
    with money_lock:
        paid = [t['ts'] for t in money if t['kind'] == 'in' and 'Salary' in (t.get('cat') or '')
                and t['ts'] <= now.timestamp()]
    if paid and now.timestamp() - max(paid) < 40 * 86400:
        start = dt.datetime.fromtimestamp(max(paid)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = on_day(today, payday)
        if start > today:
            start = on_day(shift_month(today.replace(day=1), -1), payday)
    # pay is late (past payday, no new Salary yet): payday is tomorrow, never a day already gone
    return start, max(on_day(shift_month(start.replace(day=1), 1), payday), today + dt.timedelta(days=1))


def money_totals(now=None):
    now = dt.datetime.fromtimestamp(now or time.time())
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start, payday = pay_cycle(now)
    out = {'today_in': 0.0, 'today_out': 0.0, 'cycle_in': 0.0, 'cycle_out': 0.0}
    with money_lock:
        for t in money:
            for since, period in ((today.timestamp(), 'today'), (start.timestamp(), 'cycle')):
                if since <= t['ts'] <= now.timestamp() + 60:
                    out[f'{period}_{t["kind"]}'] += t['amount']
    days_so_far = (today - start).days + 1
    days_left = max(1, (payday - today).days)  # today included, payday not
    out.update(cycle_start=start.timestamp(), payday=payday.timestamp(), days_left=days_left,
               avg_day=out['cycle_out'] / days_so_far,
               can_day=max(0.0, out['cycle_in'] - out['cycle_out']) / days_left)
    return out


def phone_addresses():
    """URLs the iPhone can use: home Wi-Fi, and Tailscale (100.64.0.0/10) to reach the PC from anywhere."""
    ips = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(('10.255.255.255', 1))  # no packet sent: just asks which interface routes outward
            ips.add(s.getsockname()[0])
        except OSError:
            pass
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ips.add(info[4][0])
    tail = [ip for ip in ips if ipaddress.ip_address(ip) in TAILSCALE]
    lan = [ip for ip in ips if ip not in tail and not ip.startswith('127.') and ipaddress.ip_address(ip).is_private]
    return lan[:1], tail[:1]


TAILSCALE = ipaddress.ip_network('100.64.0.0/10')


class MoneyHandler(http.server.BaseHTTPRequestHandler):
    """Receiver for the iPhone: Shortcuts POST bank texts, the web app reads and edits. Only private networks
    and Tailscale may connect, and every call carries the pairing key."""
    key, icon, on_prefs, on_sync = None, b'', None, None  # the island's set_money_pref and handle_sync

    def log_message(self, *a):  # no console in the packaged app
        pass

    def allowed(self):
        ip = ipaddress.ip_address(self.client_address[0])
        return ip.is_private or ip.is_loopback or ip in TAILSCALE

    def send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False) if ctype ==
                                                     'application/json' else body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if 'json' in ctype or 'html' in ctype else ''))
        self.send_header('Content-Length', str(len(data)))
        # fonts (5 MB for Kiwi Maru) and the icon never change: let the phone keep them; data is always fresh
        self.send_header('Cache-Control', 'public, max-age=604800' if ctype in ('font/ttf', 'image/png') else 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {'error': 'not allowed'})
        url = urllib.parse.urlparse(self.path)
        k = urllib.parse.parse_qs(url.query).get('k', [''])[0]
        if url.path == f'/app/{self.key}':
            theme = ';'.join(f'--{k}:{v}' for k, v in THEME.items())  # the island's colours, one source
            page = MONEY_APP.replace('__KEY__', self.key).replace('__THEME__', theme)
            page = page.replace('__CURRENCIES__', json.dumps(CURRENCIES, ensure_ascii=False)).replace('__ICONS__', json.dumps(ICONS))
            return self.send(200, page, 'text/html')
        if url.path == f'/manifest/{self.key}.json':
            return self.send(200, {'name': 'Island Money', 'short_name': 'Money', 'display': 'standalone',
                                   'start_url': f'/app/{self.key}', 'background_color': BG,
                                   'theme_color': BG, 'icons': [{'src': '/icon.png', 'sizes': '180x180',
                                                                        'type': 'image/png'}]})
        if url.path == '/icon.png':
            return self.send(200, self.icon, 'image/png')
        if url.path.startswith('/fonts/'):  # only the bundled files, never a path the client makes up
            name = url.path[len('/fonts/'):]
            if name in {f.name for f in FONT_DIR.glob('*.ttf')}:
                return self.send(200, (FONT_DIR / name).read_bytes(), 'font/ttf')
        if url.path == '/api/tx' and secrets.compare_digest(k, self.key):
            with money_lock:
                # shortcut: the whole history on every 30 s poll (fine for years of entries); page it if it gets slow
                items = [{k2: v for k2, v in t.items() if k2 != 'raw'} for t in money]
            return self.send(200, {'tx': items, 'totals': money_totals(), 'prefs': money_prefs})
        self.send(404, {'error': 'not found'})

    def do_POST(self):
        if not self.allowed():
            return self.send(403, {'error': 'not allowed'})
        url = urllib.parse.urlparse(self.path)
        if url.path == '/api/slip':  # slip image from "Import slips" in the phone app
            q = urllib.parse.parse_qs(url.query)
            if not secrets.compare_digest(q.get('k', [''])[0], self.key):
                return self.send(403, {'error': 'wrong key'})
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 10 * 2**20:
                return self.send(413, {'error': 'image too big'})
            try:
                text, lang = asyncio.run(read_slip(self.rfile.read(size)))
            except Exception:  # not an image Windows can decode
                return self.send(422, {'error': 'not a readable image'})
            tx = parse_tx(text, 'slip')
            if not tx:
                return self.send(422, {'error': 'no amount found', 'lang': lang})
            if not tx['dated'] and q.get('ts'):  # date unreadable (English OCR drops the Thai month): photo's time
                tx['ts'] = float(q['ts'][0]) / 1000
            tx['note'] = 'Slip'
            if q.get('preview'):  # the app shows it for checking first, then saves through /api/tx
                return self.send(200, {**tx, 'text': text[:500], 'lang': lang})
            return self.send(200, {**add_tx(tx, 'slip', text), 'lang': lang})
        limit = 2**23 if url.path == '/api/sync' else 65536  # sync carries the whole history
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length', 0)), limit)) or b'{}')
        except ValueError:
            return self.send(400, {'error': 'bad json'})
        if not secrets.compare_digest(str(body.get('key', '')), self.key):
            return self.send(403, {'error': 'wrong key'})
        path = url.path
        if path == '/api/tx':
            source = str(body.get('source') or 'manual')[:12]
            if body.get('amount') not in (None, ''):  # typed in the app
                try:
                    tx = {'ts': float(body.get('ts') or time.time()), 'amount': abs(float(body['amount'])),
                          'kind': 'in' if body.get('kind') == 'in' else 'out', 'note': str(body.get('note', ''))[:80],
                          'cat': str(body.get('cat', ''))[:20]}
                except (TypeError, ValueError):
                    return self.send(400, {'error': 'bad amount'})
            else:  # bank text from a Shortcut
                text = str(body.get('text', ''))
                tx = parse_tx(text, source)
                if not tx:
                    return self.send(422, {'error': 'no amount found'})
                tx['note'] = 'SMS'
            return self.send(200, add_tx(tx, source, str(body.get('text', ''))))
        if path == '/api/parse':  # Settings → Test a bank message: what would be read, nothing is saved
            tx = parse_tx(str(body.get('text', '')), 'sms')
            return self.send(200, tx) if tx else self.send(422, {'error': 'no amount found'})
        if path == '/api/edit':
            for field in ('ts', 'amount'):  # check before changing anything
                if field in body:
                    try:
                        body[field] = abs(float(body[field]))
                    except (TypeError, ValueError):
                        return self.send(400, {'error': f'bad {field}'})
            with money_lock:
                t = next((t for t in money if t['id'] == body.get('id')), None)
                if not t:
                    return self.send(404, {'error': 'no such entry'})
                if body.get('delete'):
                    money.remove(t)
                    money_deleted[t['id']] = time.time()
                else:
                    t['mt'] = time.time()
                    if body.get('kind') in ('in', 'out'):
                        t['kind'] = body['kind']
                    if 'note' in body:
                        t['note'] = str(body['note'])[:80]
                    if 'cat' in body:
                        t['cat'] = str(body['cat'])[:20]
                    if body.get('amount'):
                        t['amount'] = round(body['amount'], 2)
                    if 'ts' in body:  # when it was really received / spent
                        t['ts'] = body['ts']
                        money.sort(key=lambda x: x['ts'])
                save_money()
            return self.send(200, {'ok': True})
        if path == '/api/prefs':  # payday / currency changed on the phone
            try:
                self.on_prefs(**{k: v for k, v in body.items() if k in money_prefs})
            except (TypeError, ValueError):
                return self.send(400, {'error': 'bad payday or currency'})
            return self.send(200, money_prefs)
        if path == '/api/sync':  # the island on another PC: takes its changes, answers with everything here
            try:
                return self.send(200, self.on_sync(body))
            except (TypeError, ValueError, KeyError, AttributeError):
                return self.send(400, {'error': 'bad sync data'})
        self.send(404, {'error': 'not found'})


# MynaUI icons (MIT, github.com/praveenjuge/mynaui-icons): the inside of each 24 x 24 outline SVG
ICONS = {
    'home': '<path d="M6.133 21C4.955 21 4 20.02 4 18.81v-8.802c0-.665.295-1.295.8-1.71l5.867-4.818a2.09 2.09 0 0 1 2.666 0l5.866 4.818c.506.415.801 1.045.801 1.71v8.802c0 1.21-.955 2.19-2.133 2.19z"/><path d="M9.5 21v-5.5a2 2 0 0 1 2-2h1a2 2 0 0 1 2 2V21"/>',
    'calendar': '<path d="M16.5 5V3m-9 2V3M3.25 8h17.5M3 10.044c0-2.115 0-3.173.436-3.981a3.9 3.9 0 0 1 1.748-1.651C6.04 4 7.16 4 9.4 4h5.2c2.24 0 3.36 0 4.216.412.753.362 1.364.94 1.748 1.65.436.81.436 1.868.436 3.983v4.912c0 2.115 0 3.173-.436 3.981a3.9 3.9 0 0 1-1.748 1.651C17.96 21 16.84 21 14.6 21H9.4c-2.24 0-3.36 0-4.216-.412a3.9 3.9 0 0 1-1.748-1.65C3 18.128 3 17.07 3 14.955z"/>',
    'plus': '<path d="M18 12h-6m0 0H6m6 0V6m0 6v6"/>',
    'cog': '<path d="M10.11 3.9a1 1 0 0 1 .995-.9h1.79a1 1 0 0 1 .995.9l.033.333a8 8 0 0 1 2.209.915l.259-.212a1 1 0 0 1 1.34.067l1.266 1.266a1 1 0 0 1 .067 1.34l-.212.26c.409.676.72 1.419.915 2.208l.332.033a1 1 0 0 1 .901.995v1.79a1 1 0 0 1-.9.995l-.333.033a8 8 0 0 1-.915 2.209l.212.259a1 1 0 0 1-.067 1.34l-1.266 1.266a1 1 0 0 1-1.34.067l-.26-.212a8 8 0 0 1-2.208.915l-.033.332a1 1 0 0 1-.995.901h-1.79a1 1 0 0 1-.995-.9l-.033-.333a8 8 0 0 1-2.209-.915l-.259.212a1 1 0 0 1-1.34-.067L5.003 17.73a1 1 0 0 1-.067-1.34l.212-.26a8 8 0 0 1-.915-2.208L3.9 13.89a1 1 0 0 1-.9-.995v-1.79a1 1 0 0 1 .9-.995l.333-.033a8 8 0 0 1 .915-2.209l-.212-.259a1 1 0 0 1 .067-1.34L6.27 5.003a1 1 0 0 1 1.34-.067l.26.212a8 8 0 0 1 2.208-.915z"/><path d="M14.5 12a2.5 2.5 0 1 0-5 0 2.5 2.5 0 0 0 5 0"/>',
    'chevron-left': '<path d="m15 6-6 6 6 6"/>',
    'chevron-right': '<path d="m9 18 6-6-6-6"/>',
    'x': '<path d="M18 6 6 18M6 6l12 12"/>',
    'image': '<path d="M6.5 8a2 2 0 1 0 4 0 2 2 0 0 0-4 0m14.427 1.99c-6.61-.908-12.31 4-11.927 10.51"/><path d="M3 13.066c2.78-.385 5.275.958 6.624 3.1"/><path d="M3 9.4c0-2.24 0-3.36.436-4.216a4 4 0 0 1 1.748-1.748C6.04 3 7.16 3 9.4 3h5.2c2.24 0 3.36 0 4.216.436a4 4 0 0 1 1.748 1.748C21 6.04 21 7.16 21 9.4v5.2c0 2.24 0 3.36-.436 4.216a4 4 0 0 1-1.748 1.748C17.96 21 16.84 21 14.6 21H9.4c-2.24 0-3.36 0-4.216-.436a4 4 0 0 1-1.748-1.748C3 17.96 3 16.84 3 14.6z"/>',
    'message-dots': '<path d="M12 11v-.5m4 .5v-.5M8 11v-.5m-4.536 6.328C2 15.657 2 14.771 2 11s0-5.657 1.464-6.828C4.93 3 7.286 3 12 3s7.071 0 8.535 1.172S22 7.229 22 11s0 4.657-1.465 5.828C19.072 18 16.714 18 12 18c-2.51 0-3.8 1.738-6 3v-3.212c-1.094-.163-1.899-.45-2.536-.96"/>',
    'arrow-down-circle': '<path d="M12 7.5v9m3.5-3.5L12 16.5 8.5 13"/><path d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0"/>',
    'arrow-up-circle': '<path d="M12 16.5v-9M8.5 11 12 7.5l3.5 3.5"/><path d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0"/>',
    'bowl': '<path d="M15.017 21c.555 0 1.005-.448 1.005-1v-.45c0-.307.164-.563.433-.715a9 9 0 0 0 1.944-1.471 8.95 8.95 0 0 0 2.595-5.366c.061-.549-.395-.998-.95-.998H3.956c-.555 0-1.011.45-.95.998A8.95 8.95 0 0 0 5.6 17.364a9 9 0 0 0 1.833 1.408c.33.193.55.537.548.918v.307A1.003 1.003 0 0 0 8.986 21zM6 5v2m12-2v2m-6-4v4"/>',
    'train': '<path d="M16 18H8m8 0 2 3m-2-3a3 3 0 0 0 3-3v-4M8 18l-2 3m2-3a3 3 0 0 1-3-3v-4m3 3v1m8-1v1M5 11h7m-7 0V6m7 5h7m-7 0V6m7 5V6m-7 0H5m7 0h7M5 6a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3"/>',
    'shopping-bag': '<path d="M8.935 7H7.773c-1.072 0-1.962.81-2.038 1.858l-.73 10C4.921 20.015 5.858 21 7.043 21h9.914c1.185 0 2.122-.985 2.038-2.142l-.73-10C18.189 7.81 17.299 7 16.227 7h-1.162m-6.13 0V5c0-1.105.915-2 2.043-2h2.044c1.128 0 2.043.895 2.043 2v2m-6.13 0h6.13"/>',
    'lightning': '<path d="M10.564 13.708a.504.504 0 0 0-.496-.565H7a.506.506 0 0 1-.461-.702l3.907-9.128a.5.5 0 0 1 .46-.313h4.518c.353 0 .594.36.465.694l-2.225 5.712a.506.506 0 0 0 .464.694H17c.412 0 .647.479.398.813l-7.47 10.046c-.062.083-.191.03-.18-.072z"/>',
    'controller': '<path d="M7.5 11.5v3M6 13h3m3-4.653c2.005 0 3.7-1.888 5.786-1.212 2.264.733 3.82 3.413 3.708 9.492-.022 1.224-.336 2.578-1.546 3.106-2.797 1.221-4.397-2.328-7-2.328h-1.897c-2.605 0-4.213 3.545-6.998 2.328-1.21-.528-1.525-1.882-1.547-3.107-.113-6.078 1.444-8.758 3.708-9.491C8.299 6.459 9.994 8.347 12 8.347m0-4.565v4.342M14.874 13h3"/>',
    'heart': '<path d="M7.75 3.5C5.127 3.5 3 5.76 3 8.547 3 14.125 12 20.5 12 20.5s9-6.375 9-11.953C21 5.094 18.873 3.5 16.25 3.5c-1.86 0-3.47 1.136-4.25 2.79-.78-1.654-2.39-2.79-4.25-2.79"/>',
    'arrow-left-right': '<path d="M6.5 7.5h11M14 4l3.5 3.5L14 11m3.5 5.5h-11M10 13l-3.5 3.5L10 20"/>',
    'package': '<path d="M16.5 5.204c-1.19-.617-2.834-1.467-3.77-1.951-.267-.138-.4-.208-.541-.235a1 1 0 0 0-.378 0c-.14.027-.274.097-.542.235L3 7.533v7.826c0 .357 0 .536.054.694q.072.212.226.373c.115.12.273.202.589.366L12 21l8.131-4.208c.316-.164.474-.245.589-.366a1 1 0 0 0 .226-.373c.054-.159.054-.336.054-.692V7.533zM12 21v-9.063m9-4.404-9 4.404M3 7.533l4.5 2.202m4.5 2.202L7.5 9.735m0 0 9-4.531m-9 4.531v2.202"/>',
    'briefcase': '<path d="m20.953 10.587-.692 7.2C20.067 19.551 19.403 21 15.692 21H8.308c-3.71 0-4.375-1.449-4.569-3.213l-.692-7.2m17.906 0c.25-2.196-.397-3.987-4.338-3.987h-9.23c-3.941 0-4.587 1.791-4.338 3.987m17.906 0C18.935 11.882 16.301 13.249 14 13.5M3.047 10.587C5.065 11.882 7.699 13.249 10 13.5m0 0a2 2 0 1 0 4 0m-4 0a2 2 0 1 1 4 0m1-6.9V4a1 1 0 0 0-1-1h-4a1 1 0 0 0-.708.293A1 1 0 0 0 9 4.001V6.6"/>',
    'gift': '<path d="M12 21v-9m0-5H7.95c-2.77 0-2.94-4 0-4C11.1 3 12 7 12 7m0 0h4.05c2.896 0 2.896-4 0-4C12.9 3 12 7 12 7"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7m17 0V9a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v3z"/>',
    'undo': '<path d="M5.636 18.364A9 9 0 1 0 12 3C7.942 3 5.482 5.705 3 8.5"/><path d="M3 4.5v4h4"/>',
    'tag': '<path d="M4.472 5.5H14.77a2 2 0 0 1 1.396.568l5.35 5.216a1 1 0 0 1 0 1.432l-5.35 5.216a2 2 0 0 1-1.396.568H4.472c-.95 0-2.222-.541-2.222-1.625v-9.75C2.25 6.041 3.523 5.5 4.472 5.5"/>',
    'trash': '<path d="m18 9-.84 8.398c-.127 1.273-.19 1.909-.48 2.39a2.5 2.5 0 0 1-1.075.973C15.098 21 14.46 21 13.18 21h-2.36c-1.279 0-1.918 0-2.425-.24a2.5 2.5 0 0 1-1.076-.973c-.288-.48-.352-1.116-.48-2.389L6 9m7.5 6.5v-5m-3 5v-5m-6-4h4.615m0 0 .386-2.672c.112-.486.516-.828.98-.828h3.038c.464 0 .867.342.98.828l.386 2.672m-5.77 0h5.77m0 0H19.5"/>',
    'chart-bar': '<path d="M21 21H9.4c-2.24 0-3.36 0-4.216-.436a4 4 0 0 1-1.748-1.748C3 17.96 3 16.84 3 14.6V3m4 13.5h5M7 12h10M7 7.5h7"/>',
    'check': '<path d="m6 13.626 1.606 1.722c.886.95 1.329 1.424 1.825 1.574.436.131.9.096 1.315-.1.473-.224.852-.761 1.612-1.836L18 7"/>',
}


# The phone page: Home, Calendar, Settings tabs and an Add sheet. __KEY__, __THEME__, __CURRENCIES__ and __ICONS__
# are filled in when it is served.
MONEY_APP = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Money"><meta name="theme-color" content="#191919">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest/__KEY__.json"><link rel="apple-touch-icon" href="/icon.png">
<title>Island Money</title><style>
@font-face{font-family:'Noto Sans';font-weight:400;src:url(/fonts/NotoSans-Regular.ttf)}
@font-face{font-family:'Noto Sans';font-weight:600 700;src:url(/fonts/NotoSans-Bold.ttf)}
@font-face{font-family:'Noto Sans Thai';font-weight:400;src:url(/fonts/NotoSansThai-Regular.ttf)}
@font-face{font-family:'Noto Sans Thai';font-weight:600 700;src:url(/fonts/NotoSansThai-Bold.ttf)}
@font-face{font-family:'Kiwi Maru';font-weight:400;src:url(/fonts/KiwiMaru-Regular.ttf);unicode-range:U+3000-30FF,U+4E00-9FFF,U+FF00-FFEF}
@font-face{font-family:'Kiwi Maru';font-weight:500 700;src:url(/fonts/KiwiMaru-Medium.ttf);unicode-range:U+3000-30FF,U+4E00-9FFF,U+FF00-FFEF}
@font-face{font-family:'Kiwi Round';font-weight:500;src:url(/fonts/KiwiMaru-Medium.ttf)}
:root{__THEME__;--round:'Kiwi Round','Noto Sans',sans-serif;--nav:calc(72px + env(safe-area-inset-bottom))}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--ground);color:var(--ink);font:16px/1.5 'Noto Sans','Kiwi Maru','Noto Sans Thai',-apple-system,sans-serif;
 padding:env(safe-area-inset-top) max(16px,env(safe-area-inset-right)) calc(var(--nav) + 24px) max(16px,env(safe-area-inset-left))}
svg.i{width:22px;height:22px;flex:none;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
header{display:flex;align-items:center;gap:10px;margin:18px 2px 14px}
header h1{font:500 26px var(--round);margin:0;flex:1}header small{color:var(--muted);font-size:13px}
.petal{width:24px;height:24px;flex:none}
h2{font-size:12px;color:var(--muted);font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin:24px 4px 8px}
.view{display:none}.view.on{display:block;animation:in .2s ease-out}
@keyframes in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.card{background:var(--surface);border-radius:20px;padding:16px}
.hero{background:linear-gradient(145deg,var(--sakura-tint),var(--surface) 70%);border:1px solid #ff9ec833}
.hero small,.card small{color:var(--muted);font-size:13px;display:block}
.money{font:500 clamp(28px,9vw,34px)/1.15 var(--round);overflow-wrap:anywhere;font-variant-numeric:tabular-nums;margin:2px 0 4px}
.mid{font:500 clamp(15px,4.6vw,19px)/1.35 var(--round);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.in{color:var(--mint)}.out{color:var(--coral)}
.bar{height:8px;border-radius:4px;background:var(--surface-2);overflow:hidden;margin:14px 0 6px}
.bar i{display:block;height:100%;border-radius:4px;background:var(--sakura);transition:width .4s}
.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:10px}
.list{background:var(--surface);border-radius:20px;overflow:hidden}
.tx{display:flex;align-items:center;gap:12px;padding:12px 14px;min-height:60px;border-top:1px solid var(--line);cursor:pointer;
 width:100%;text-align:left;background:none;border-radius:0}
.tx:first-child{border-top:0}.tx:active{background:var(--surface-2)}
.tile{width:40px;height:40px;border-radius:13px;display:grid;place-items:center;flex:none}
.tile.out{background:#ff7f9822}.tile.in{background:#86ebc022}
.tx .w{flex:1;min-width:0;display:grid}.tx .n{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
.tx .s{color:var(--muted);font-size:13px}.tx .a{font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.day{font-size:13px;color:var(--muted);font-weight:700;margin:18px 6px 6px;display:flex;justify-content:space-between}
.empty{color:var(--muted);padding:22px 14px;text-align:center}
button{font:inherit;border:0;border-radius:14px;min-height:44px;padding:10px 16px;background:var(--surface-2);color:var(--ink);cursor:pointer}
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:none;box-shadow:0 0 0 3px #ff9ec880}
.primary{background:var(--sakura);color:var(--on-sakura);font-weight:700}.primary:active{background:var(--sakura-deep)}
input,select,textarea{font:inherit;width:100%;min-height:48px;padding:12px 14px;border-radius:14px;border:0;background:var(--surface-2);
 color:var(--ink);-webkit-appearance:none;appearance:none}
textarea{min-height:96px;resize:vertical}input::placeholder,textarea::placeholder{color:var(--faint)}
label{display:grid;gap:6px;font-size:13px;color:var(--muted);font-weight:600}
.seg{display:grid;grid-template-columns:1fr 1fr;gap:4px;background:var(--ground);border-radius:14px;padding:4px}
.seg button{min-height:40px;border-radius:11px;background:none;color:var(--muted);font-weight:700}
.seg button.on{background:var(--surface-2);color:var(--ink)}.seg button.on.out{color:var(--coral)}.seg button.on.in{color:var(--mint)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{display:flex;align-items:center;gap:6px;min-height:40px;padding:8px 14px 8px 10px;border-radius:999px;font-size:14px}
.chip svg.i{width:18px;height:18px}.chip.on{background:var(--sakura);color:var(--on-sakura);font-weight:700}
nav{position:fixed;left:0;right:0;bottom:0;z-index:5;display:grid;grid-template-columns:repeat(5,1fr);align-items:center;
 padding:8px 12px calc(8px + env(safe-area-inset-bottom));background:#231f21cc;-webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px);
 border-top:1px solid #ffffff14}
nav button{background:none;display:grid;justify-items:center;gap:2px;font-size:11px;color:var(--faint);padding:6px 0;min-height:52px}
nav button.on{color:var(--sakura)}
nav .add{width:56px;height:56px;min-height:56px;border-radius:20px;margin:auto;background:var(--sakura);color:var(--on-sakura);
 box-shadow:0 6px 18px #f06aa655;place-items:center}nav .add svg.i{width:28px;height:28px;stroke-width:2}
.sheet{position:fixed;inset:0;background:#0009;display:none;align-items:flex-end;z-index:9}.sheet.on{display:flex}
.panel{background:var(--surface);width:100%;max-width:560px;margin:0 auto;border-radius:24px 24px 0 0;max-height:92vh;overflow:auto;
 padding:10px 16px calc(env(safe-area-inset-bottom) + 18px);display:grid;gap:14px;animation:up .25s cubic-bezier(.2,.9,.3,1)}
@keyframes up{from{transform:translateY(40px);opacity:.5}to{transform:none;opacity:1}}
.grab{width:40px;height:5px;border-radius:3px;background:var(--line);margin:4px auto 0}
.panel h3{margin:0;font:500 20px var(--round);text-align:center}
.big{text-align:center;font:500 34px var(--round);min-height:64px;background:var(--ground)}
.row2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.danger{color:var(--coral)}
.note{color:var(--muted);font-size:13px;text-align:center;margin:0}
#pick{display:flex;align-items:center;justify-content:center;gap:8px}
.calhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.calhead b{font:500 18px var(--round)}.calhead button{width:44px;padding:0;display:grid;place-items:center}
.grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px}
.wd{font-size:11px;color:var(--muted);text-align:center;padding-bottom:2px}
.d{border-radius:12px;min-height:56px;padding:4px 2px;display:flex;flex-direction:column;align-items:center;gap:1px;background:var(--surface-2);
 font-size:10px;line-height:1.2;font-variant-numeric:tabular-nums;overflow:hidden}
.d .n{font-size:12px;font-weight:700}.d .o{color:var(--coral)}.d .i{color:var(--mint)}
.d.today{box-shadow:inset 0 0 0 2px var(--sakura)}.d.sel{background:var(--sakura-tint)}
.d.blank{background:transparent;pointer-events:none}.d.over{background:#ff7f9826}
.set{display:grid;gap:12px}
.tri{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:14px}
.tri small{color:var(--muted);font-size:13px}
.months{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:3px;align-items:end}
.mo{display:grid;justify-items:center;gap:4px;background:none;padding:6px 0 2px;min-height:44px;border-radius:10px;font-size:10px;color:var(--muted)}
.mo .bars{display:flex;align-items:flex-end;gap:2px;height:84px}.mo .bars i{width:5px;border-radius:3px;min-height:2px}
.mo .bars i.in{background:var(--mint)}.mo .bars i.out{background:var(--coral)}.mo.sel{background:var(--sakura-tint);color:var(--ink)}
.steps{color:var(--muted);font-size:14px}.steps b{color:var(--ink)}.steps ol{padding-left:20px;margin:8px 0}.steps li{margin:6px 0}
code{background:var(--ground);color:var(--sakura);padding:2px 6px;border-radius:6px;white-space:nowrap;font-size:13px}
code.ep{white-space:normal;word-break:break-all}
.result{border-radius:14px;padding:12px 14px;background:var(--ground);font-size:14px}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body>
<header><svg class="petal" viewBox="-12 -12 24 24" aria-hidden="true"><g style="fill:var(--sakura)">
<ellipse rx="4" ry="7" cy="-5"/><ellipse rx="4" ry="7" cy="-5" transform="rotate(72)"/><ellipse rx="4" ry="7" cy="-5" transform="rotate(144)"/>
<ellipse rx="4" ry="7" cy="-5" transform="rotate(216)"/><ellipse rx="4" ry="7" cy="-5" transform="rotate(288)"/></g>
<circle r="2.5" style="fill:var(--butter)"/></svg><h1 id="title">Money</h1><small id="cyc"></small></header>

<main>
<section class="view on" id="v_home" aria-label="Home">
 <div class="card hero"><small>Can spend per day</small><div class="money in" id="can"></div>
  <small id="avg"></small><div class="bar" aria-hidden="true"><i id="prog"></i></div><small id="left"></small></div>
 <div class="pair"><div class="card"><small>Today</small><div class="mid in" id="ti"></div><div class="mid out" id="to"></div></div>
  <div class="card"><small>This pay cycle</small><div class="mid in" id="mi"></div><div class="mid out" id="mo"></div></div></div>
 <h2>History</h2><div id="list"></div>
</section>

<section class="view" id="v_cal" aria-label="Calendar">
 <div class="card"><div class="calhead"><button type="button" id="prev" aria-label="Previous month">__I_chevron-left__</button>
  <b id="mon"></b><button type="button" id="next" aria-label="Next month">__I_chevron-right__</button></div>
  <div class="grid" id="grid"></div></div>
 <h2 id="dayhead">Today</h2><div id="daylist"></div>
</section>

<section class="view" id="v_hist" aria-label="History">
 <div class="card"><div class="calhead"><button type="button" id="yprev" aria-label="Previous year">__I_chevron-left__</button>
  <b id="year"></b><button type="button" id="ynext" aria-label="Next year">__I_chevron-right__</button></div>
  <div class="tri"><div><small>Money in</small><div class="mid in" id="yin"></div></div>
   <div><small>Money out</small><div class="mid out" id="yout"></div></div>
   <div><small>Left</small><div class="mid" id="ynet"></div></div></div>
  <div class="months" id="months"></div></div>
 <h2 id="histhead"></h2><div id="histlist"></div>
</section>

<section class="view" id="v_set" aria-label="Settings">
 <div class="card set"><div class="row2"><label>Payday<select id="pd"></select></label><label>Currency<select id="cu"></select></label></div>
  <p class="note" style="text-align:left">Your pay cycle starts on payday. Mark your pay as Salary and it starts on the day it really comes.</p></div>
 <h2>Test a bank message</h2>
 <div class="card set"><label>Paste an SMS or notification from your bank<textarea id="tm" placeholder="e.g. Transfer 1,250.00 THB to ..."></textarea></label>
  <button type="button" id="tbtn">Read it</button><div class="result" id="tres" hidden></div></div>
 <h2>Send bank messages automatically</h2>
 <div class="card set"><div class="seg"><button type="button" id="os_ios" class="on">iPhone</button><button type="button" id="os_and">Android</button></div>
  <div class="steps" id="st_ios"><ol>
   <li>Shortcuts → <b>Automation</b> → <b>Message</b> → Sender contains your bank's name (e.g. <code>KBank</code>) → <b>Run Immediately</b>.</li>
   <li>Add <b>Get Contents of URL</b>: URL <code class="ep"></code>, Method <b>POST</b>, Request Body <b>JSON</b>:
    <code>key</code> = <code>__KEY__</code>, <code>source</code> = <code>sms</code>, <code>text</code> = Shortcut Input.</li>
   <li>Bank emails work the same with Automation → <b>Email</b> and <code>source</code> = <code>email</code>.</li></ol></div>
  <div class="steps" id="st_and" hidden><ol>
   <li>Install <b>MacroDroid</b> (free) from Google Play and allow it to read SMS.</li>
   <li>New macro → Trigger <b>SMS Received</b> → from your bank's number or name.</li>
   <li>Action <b>HTTP Request</b>: Method <b>POST</b>, URL <code class="ep"></code>, Content type <code>application/json</code>, Body
    <code>{"key":"__KEY__","source":"sms","text":"…"}</code> with the SMS message text in place of … (Magic Text → SMS message).</li>
   <li>Add this page to the home screen: Chrome menu → <b>Add to Home screen</b>.</li></ol></div>
  <p class="note" style="text-align:left">Away from home Wi-Fi? Install Tailscale on the PC and the phone, and use the island's Tailscale address.
  Not sure your bank works? Try one of its messages in <b>Test a bank message</b> above.</p></div>
</section>
</main>

<nav aria-label="Main"><button type="button" data-v="home" class="on">__I_home__Home</button>
<button type="button" data-v="cal">__I_calendar__Calendar</button>
<button type="button" class="add" id="addbtn" aria-label="Add money">__I_plus__</button>
<button type="button" data-v="hist">__I_chart-bar__History</button>
<button type="button" data-v="set">__I_cog__Settings</button></nav>

<div class="sheet" id="sheet" role="dialog" aria-modal="true" aria-labelledby="e_title"><div class="panel">
<div class="grab"></div><h3 id="e_title">Add</h3>
<div class="seg"><button type="button" id="e_out" class="out">Money out</button><button type="button" id="e_in" class="in">Money in</button></div>
<label>Amount<input class="big" id="e_amt" inputmode="decimal" placeholder="0.00"></label>
<div class="chips" id="e_cats" aria-label="Category"></div>
<label>Note<input id="e_note" placeholder="Optional"></label>
<label>When<input id="e_when" type="datetime-local"></label>
<p class="note" id="e_src"></p>
<div class="row2"><button type="button" id="e_close">Cancel</button><button type="button" id="e_save" class="primary">Save</button></div>
<button type="button" id="e_del" class="danger">Delete</button>
<button type="button" id="pick">__I_image__Import bank slips from Photos</button>
<input id="files" type="file" accept="image/*" multiple hidden></div></div>
<script>
const K='__KEY__',$=id=>document.getElementById(id),IC=__ICONS__;
const icon=n=>`<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${IC[n]||IC.package}</svg>`;
document.body.innerHTML=document.body.innerHTML.replace(/__I_([a-z-]+)__/g,(_,n)=>icon(n));
// categories are saved as "emoji label" (the island shows the emoji); the page shows an icon instead
const CATS={out:['🍜 Food','🚆 Transport','🛍️ Shopping','💡 Bills','🎮 Fun','💊 Health','↔️ Transfer','📦 Other'],
 in:['💼 Salary','🎁 Gift','↩️ Refund','↔️ Transfer','🏷️ Sale','📦 Other']};
const CAT_ICON={Food:'bowl',Transport:'train',Shopping:'shopping-bag',Bills:'lightning',Fun:'controller',Health:'heart',
 Transfer:'arrow-left-right',Other:'package',Salary:'briefcase',Gift:'gift',Refund:'undo',Sale:'tag'};
const label=c=>(c||'').split(' ').slice(1).join(' ');
let CUR='฿';
const fmt=n=>n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})+' '+CUR;
const fmt0=n=>Math.round(n).toLocaleString('en-US')+' '+CUR;  // year totals: no satang, so three fit a phone
const nth=d=>d+({1:'st',2:'nd',3:'rd'}[d%10*(Math.floor(d/10)!=1)]||'th');
for(let d=1;d<=31;d++)$('pd').add(new Option(nth(d)+' of the month',d));
for(const c of __CURRENCIES__)$('cu').add(new Option(c,c));
const post=(p,b)=>fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:K,...b})});
$('pd').onchange=async()=>{await post('/api/prefs',{payday:+$('pd').value});load()};
$('cu').onchange=async()=>{await post('/api/prefs',{currency:$('cu').value});load()};
for(const e of document.querySelectorAll('.ep'))e.textContent=location.origin+'/api/tx';
const hhmm=d=>d.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});
const local=ts=>{const d=new Date(ts*1000);return new Date(d-d.getTimezoneOffset()*6e4).toISOString().slice(0,16)};
const dm=ts=>new Date(ts*1000).toLocaleDateString('en-GB',{day:'numeric',month:'short'});
const dkey=d=>d.getFullYear()+'-'+(d.getMonth()+1)+'-'+d.getDate();
const short=n=>n>=1e4?Math.round(n/1e3)+'k':n>=1e3?(n/1e3).toFixed(1)+'k':String(Math.round(n));
function dayName(d){const t=new Date();t.setHours(0,0,0,0);const x=new Date(d);x.setHours(0,0,0,0);const n=(t-x)/864e5;
 return n==0?'Today':n==1?'Yesterday':d.toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short',year:'numeric'})}
function show(v){for(const s of document.querySelectorAll('.view'))s.classList.toggle('on',s.id=='v_'+v);
 for(const b of document.querySelectorAll('nav [data-v]'))b.classList.toggle('on',b.dataset.v==v);
 $('title').textContent={home:'Money',cal:'Calendar',hist:'History',set:'Settings'}[v];scrollTo(0,0);history.replaceState(null,'','#'+v)}
for(const b of document.querySelectorAll('nav [data-v]'))b.onclick=()=>show(b.dataset.v);
function os(ios){$('os_ios').classList.toggle('on',ios);$('os_and').classList.toggle('on',!ios);$('st_ios').hidden=!ios;$('st_and').hidden=ios}
$('os_ios').onclick=()=>os(true);$('os_and').onclick=()=>os(false);if(/Android/i.test(navigator.userAgent))os(false);
let txs=[],tot=null,calMonth=new Date(),selDay=dkey(new Date());calMonth.setDate(1);
async function load(){
 if($('sheet').classList.contains('on'))return;  // don't redraw under an open sheet
 const d=await (await fetch('/api/tx?k='+K)).json();txs=d.tx.sort((a,b)=>a.ts-b.ts);tot=d.totals;const t=tot;
 CUR=d.prefs.currency;$('pd').value=d.prefs.payday;$('cu').value=CUR;
 $('can').textContent=fmt(t.can_day);$('avg').textContent='You spend about '+fmt(t.avg_day)+' a day';
 const span=(t.payday-t.cycle_start)/86400;$('prog').style.width=Math.min(100,100*(span-t.days_left)/span)+'%';
 $('left').textContent=`Payday in ${t.days_left} day${t.days_left==1?'':'s'} · ${dm(t.payday)}`;
 $('cyc').textContent=dm(t.cycle_start)+' – '+dm(t.payday-86400);
 $('ti').textContent='+'+fmt(t.today_in);$('to').textContent='−'+fmt(t.today_out);
 $('mi').textContent='+'+fmt(t.cycle_in);$('mo').textContent='−'+fmt(t.cycle_out);
 drawList($('list'),txs.slice(-60),'Nothing yet. Tap + to add money.');drawCal();drawHist()}
function row(x){const when=new Date(x.ts*1000),r=document.createElement('button');r.type='button';r.className='tx';
 r.innerHTML=`<span class="tile ${x.kind}">${icon(CAT_ICON[label(x.cat)]||(x.kind=='in'?'arrow-down-circle':'arrow-up-circle'))}</span>`+
  '<span class="w"><span class="n"></span><span class="s"></span></span><span class="a '+x.kind+'"></span>';
 r.querySelector('.n').textContent=label(x.cat)||x.note||(x.kind=='in'?'Money in':'Money out');
 r.querySelector('.s').textContent=[x.cat&&x.note?x.note:'',hhmm(when),x.source].filter(Boolean).join(' · ');
 r.querySelector('.a').textContent=(x.kind=='in'?'+':'−')+fmt(x.amount);r.onclick=()=>edit(x);return r}
function drawList(L,items,empty){L.innerHTML='';
 if(!items.length){L.innerHTML='<div class="list"><div class="empty"></div></div>';L.querySelector('.empty').textContent=empty;return}
 let box=null,day='',sum=0,sumEl=null;
 for(const x of items.slice().reverse()){const name=dayName(new Date(x.ts*1000));
  if(name!=day){day=name;sum=0;const h=document.createElement('div');h.className='day';h.innerHTML='<span></span><span></span>';
   h.firstChild.textContent=name;sumEl=h.lastChild;L.appendChild(h);box=document.createElement('div');box.className='list';L.appendChild(box)}
  sum+=x.kind=='in'?x.amount:-x.amount;sumEl.textContent=(sum<0?'−':'+')+fmt(Math.abs(sum));box.appendChild(row(x))}}
function drawCal(){  // each day: money out (coral) and in (mint); tinted when above 1.5x the daily average
 const days={};for(const x of txs){const k=dkey(new Date(x.ts*1000));(days[k]=days[k]||{i:0,o:0})[x.kind=='in'?'i':'o']+=x.amount}
 $('mon').textContent=calMonth.toLocaleDateString('en-GB',{month:'long',year:'numeric'});
 const G=$('grid');G.innerHTML=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(w=>`<div class="wd">${w}</div>`).join('');
 const lead=(calMonth.getDay()+6)%7,n=new Date(calMonth.getFullYear(),calMonth.getMonth()+1,0).getDate(),today=dkey(new Date());
 for(let i=0;i<lead;i++)G.insertAdjacentHTML('beforeend','<div class="d blank"></div>');
 for(let day=1;day<=n;day++){const date=new Date(calMonth.getFullYear(),calMonth.getMonth(),day),k=dkey(date),v=days[k];
  const c=document.createElement('button');c.type='button';c.setAttribute('aria-label',date.toDateString());
  c.className='d'+(k==today?' today':'')+(k==selDay?' sel':'')+(v&&tot&&v.o>tot.avg_day*1.5?' over':'');
  c.innerHTML=`<span class="n">${day}</span>`+(v&&v.o?`<span class="o">−${short(v.o)}</span>`:'')+(v&&v.i?`<span class="i">+${short(v.i)}</span>`:'');
  c.onclick=()=>{selDay=k;drawCal()};G.appendChild(c)}
 const [y,m,dd]=selDay.split('-').map(Number);$('dayhead').textContent=dayName(new Date(y,m-1,dd));
 drawList($('daylist'),txs.filter(x=>dkey(new Date(x.ts*1000))==selDay),'Nothing on this day');
 const h=$('daylist').querySelector('.day');if(h)h.remove()}  // the heading above already names the day
let hYear=new Date().getFullYear(),hMonth=null;  // History: one year, or one month of it
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function drawHist(){
 const inYear=txs.filter(x=>new Date(x.ts*1000).getFullYear()==hYear),sum=[...Array(12)].map(()=>({i:0,o:0}));
 for(const x of inYear)sum[new Date(x.ts*1000).getMonth()][x.kind=='in'?'i':'o']+=x.amount;
 const yi=sum.reduce((a,m)=>a+m.i,0),yo=sum.reduce((a,m)=>a+m.o,0),top=Math.max(1,...sum.map(m=>Math.max(m.i,m.o)));
 $('year').textContent=hYear;$('yin').textContent='+'+fmt0(yi);$('yout').textContent='−'+fmt0(yo);
 $('ynet').textContent=(yi-yo<0?'−':'+')+fmt0(Math.abs(yi-yo));$('ynet').className='mid '+(yi-yo<0?'out':'in');
 const M=$('months');M.innerHTML='';
 sum.forEach((m,i)=>{const b=document.createElement('button');b.type='button';b.className='mo'+(i===hMonth?' sel':'');
  b.setAttribute('aria-label',`${MON[i]}: in ${fmt(m.i)}, out ${fmt(m.o)}`);
  b.innerHTML=`<span class="bars"><i class="in" style="height:${84*m.i/top}px"></i><i class="out" style="height:${84*m.o/top}px"></i></span>${MON[i][0]}`;
  b.onclick=()=>{hMonth=hMonth===i?null:i;drawHist()};M.appendChild(b)});
 const shown=hMonth===null?inYear:inYear.filter(x=>new Date(x.ts*1000).getMonth()==hMonth);
 $('histhead').textContent=(hMonth===null?'All of '+hYear:MON[hMonth]+' '+hYear)+` · ${shown.length} entr${shown.length==1?'y':'ies'}`;
 drawList($('histlist'),shown,'Nothing in this '+(hMonth===null?'year':'month'))}
$('yprev').onclick=()=>{hYear--;hMonth=null;drawHist()};$('ynext').onclick=()=>{hYear++;hMonth=null;drawHist()};
$('prev').onclick=()=>{calMonth.setMonth(calMonth.getMonth()-1);drawCal()};
$('next').onclick=()=>{calMonth.setMonth(calMonth.getMonth()+1);drawCal()};
// one sheet: add new money, edit an entry (cur.id), or check a slip before saving it (title set)
let cur=null,answer=null;
function chips(){const el=$('e_cats');el.innerHTML='';
 for(const c of CATS[cur.kind]){const b=document.createElement('button');b.type='button';b.className='chip'+(c==cur.cat?' on':'');
  b.innerHTML=icon(CAT_ICON[label(c)])+'<span></span>';b.lastChild.textContent=label(c);
  b.onclick=()=>{cur.cat=c==cur.cat?'':c;chips()};el.appendChild(b)}}
function drawKind(){$('e_amt').className='big '+cur.kind;$('e_out').classList.toggle('on',cur.kind=='out');$('e_in').classList.toggle('on',cur.kind=='in');chips()}
function edit(x,title){cur={...x};const adding=!x.id&&!title;
 $('e_amt').value=x.amount?x.amount.toFixed(2):'';$('e_note').value=x.note||'';$('e_when').value=local(x.ts);
 $('e_title').textContent=title||(x.id?'Edit':'Add money');
 $('e_src').textContent=title?'Check what the slip says, fix anything wrong, then Save':x.id?'From '+x.source:'';
 $('e_del').hidden=adding;$('e_del').textContent=x.id?'Delete':'Skip this slip';$('pick').hidden=!adding;
 drawKind();$('sheet').classList.add('on');if(adding)setTimeout(()=>$('e_amt').focus(),250);
 return new Promise(r=>answer=r)}
$('addbtn').onclick=()=>edit({kind:'out',cat:'',note:'',amount:0,ts:Date.now()/1000,source:'manual'});
$('e_out').onclick=()=>{if(cur.kind!='out'){cur.kind='out';cur.cat=''}drawKind()};
$('e_in').onclick=()=>{if(cur.kind!='in'){cur.kind='in';cur.cat=''}drawKind()};
const close=r=>{$('sheet').classList.remove('on');if(answer){answer(r);answer=null}load()};
$('e_close').onclick=()=>close('cancel');$('sheet').onclick=e=>{if(e.target.id=='sheet')close('cancel')};
document.addEventListener('keydown',e=>{if(e.key=='Escape'&&$('sheet').classList.contains('on'))close('cancel')});
$('e_save').onclick=async()=>{
 const ts=new Date($('e_when').value).getTime()/1000,amount=parseFloat($('e_amt').value.replace(/,/g,''));
 if(!(amount>0)){$('e_amt').focus();return}
 const f={kind:cur.kind,cat:cur.cat||'',note:$('e_note').value,amount,...(isNaN(ts)?{}:{ts})};
 if(cur.id)await post('/api/edit',{id:cur.id,...f});else await post('/api/tx',{...f,source:cur.source||'manual',text:cur.text||''});
 close('saved')};
$('e_del').onclick=async()=>{if(!cur.id)return close('skipped');
 if(confirm('Delete this entry?')){await post('/api/edit',{id:cur.id,delete:true});close('deleted')}};
async function shrink(f){  // phone photos are huge: send at most 2000 px, as JPEG (also turns HEIC into JPEG)
 const b=await createImageBitmap(f),s=Math.min(1,2000/Math.max(b.width,b.height)),c=document.createElement('canvas');
 c.width=Math.round(b.width*s);c.height=Math.round(b.height*s);c.getContext('2d').drawImage(b,0,0,c.width,c.height);
 return new Promise(r=>c.toBlob(r,'image/jpeg',0.9))}
$('pick').onclick=()=>$('files').click();
$('files').onchange=async()=>{  // read each slip, show it for checking, save only what you confirm
 const fs=[...$('files').files];let ok=0,skip=0,noThai=false;close('cancel');
 for(const [i,f] of fs.entries()){
  let d={amount:0,kind:'out',ts:f.lastModified/1000,note:'Slip',cat:'',source:'slip'},title=`Slip ${i+1} of ${fs.length}`;
  try{const r=await fetch(`/api/slip?k=${K}&preview=1&ts=${f.lastModified}`,{method:'POST',headers:{'Content-Type':'image/jpeg'},body:await shrink(f)});
   const res=await r.json();if(res.lang&&res.lang!='th')noThai=true;
   if(r.ok)d={...d,...res};else title+=' · could not read it, type it in'}catch(e){title+=' · could not read it, type it in'}
  const a=await edit(d,title);if(a=='saved')ok++;else skip++;if(a=='cancel')break}
 $('files').value='';
 alert(`${ok} slip${ok==1?'':'s'} saved`+(skip?`, ${skip} skipped`:'')+(noThai?'. Thai text reading is not installed on the PC, so dates come from the photo.':''))};
$('tbtn').onclick=async()=>{const R=$('tres');R.hidden=false;R.textContent='Reading…';
 const r=await post('/api/parse',{text:$('tm').value}),d=await r.json();
 R.textContent=r.ok?`${d.kind=='in'?'Money in':'Money out'}: ${fmt(d.amount)}`+
  (d.dated?` · ${new Date(d.ts*1000).toLocaleString('en-GB')}`:' · no date found, the time it arrives is used')+'. Nothing was saved.'
  :'No amount found in this message. Send a copy (hide the account number) so this bank can be added.'};
const start=location.hash.slice(1);show({cal:'cal',hist:'hist',set:'set'}[start]||'home');  // #cal, #hist, #set and #add open directly
load().then(()=>{if(start=='add')$('addbtn').click()});setInterval(load,30000);  // own changes reload at once; the poll only catches SMS/emails
</script></body></html>'''


async def read_slip(data):
    """Text of a slip image via Windows' built-in OCR -> (text, language used). Thai needs Windows' Thai OCR pack;
    without it English OCR still finds the amount, but not the Thai words or the month."""
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(data)
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)
    bitmap = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
    bitmap = SoftwareBitmap.convert(bitmap, BitmapPixelFormat.BGRA8)  # the OCR engine wants 8-bit BGRA or gray
    for tag in ('th', 'en-US'):
        engine = OcrEngine.try_create_from_language(Language(tag))
        if engine:
            result = await engine.recognize_async(bitmap)
            return '\n'.join(line.text for line in result.lines), tag
    raise RuntimeError('no OCR language installed')


def start_money_server(key, icon, on_prefs, on_sync):
    MoneyHandler.key, MoneyHandler.icon = key, icon
    MoneyHandler.on_prefs, MoneyHandler.on_sync = staticmethod(on_prefs), staticmethod(on_sync)
    server = http.server.ThreadingHTTPServer(('0.0.0.0', MONEY_PORT), MoneyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------- Claude Code usage, from its own logs on this PC ----------
CLAUDE_DIR = pathlib.Path.home() / '.claude' / 'projects'
# $ per million tokens: input, output, cache read. Cache writes cost 1.25x input (5 min) and 2x input (1 h);
# fast mode doubles everything. shortcut: a fixed table of Anthropic API prices (October 2026); a model it doesn't
# know still counts its tokens but adds no $. Add new models here.
CLAUDE_PRICES = {'claude-fable-5-1': (10, 50, 0.25), 'claude-mythos-5-1': (10, 50, 0.25), 'claude-fable-5': (10, 50, 1.0),
                 'claude-opus-5-5': (4, 20, 0.20), 'claude-opus-5': (5, 25, 0.50), 'claude-opus-4-8': (5, 25, 0.50),
                 'claude-opus-4-7': (5, 25, 0.50), 'claude-opus-4-6': (5, 25, 0.50), 'claude-sonnet-5-5': (2, 10, 0.20),
                 'claude-sonnet-5': (2, 10, 0.20), 'claude-sonnet-4-6': (3, 15, 0.30), 'claude-haiku-5-5': (0.1, 0.5, 0.01),
                 'claude-haiku-4-5': (1, 5, 0.10)}
WINDOW = 5 * 3600  # Claude plans count usage in 5-hour windows that start with the first message


def claude_price(model):
    key = max((k for k in CLAUDE_PRICES if model.startswith(k)), key=len, default=None)  # 'claude-opus-5' vs '-5-5'
    return CLAUDE_PRICES.get(key)


def message_cost(model, u, speed=None):
    """(tokens, $) of one reply's usage block."""
    c = u.get('cache_creation') or {}
    write_1h = c.get('ephemeral_1h_input_tokens', 0)
    write_5m = c.get('ephemeral_5m_input_tokens', u.get('cache_creation_input_tokens', 0) - write_1h)
    tokens = (u.get('input_tokens', 0) + u.get('output_tokens', 0) + u.get('cache_read_input_tokens', 0)
              + write_5m + write_1h)
    price = claude_price(model)
    if not price:
        return tokens, 0.0
    inp, out, read = price
    cost = (u.get('input_tokens', 0) * inp + write_5m * inp * 1.25 + write_1h * inp * 2
            + u.get('cache_read_input_tokens', 0) * read + u.get('output_tokens', 0) * out) / 1e6
    return tokens, cost * (2 if (speed or u.get('speed')) == 'fast' else 1)


class ClaudeUsage:
    """Reads Claude Code's session logs (~/.claude/projects/**/*.jsonl). After the first read, only the lines
    added since last time; one entry per reply (the logs repeat a reply as it streams)."""

    def __init__(self, folder=CLAUDE_DIR):
        self.folder, self.offsets, self.replies = folder, {}, {}  # reply id -> (time, tokens, $)

    def scan(self, pause=0.0):
        for f in self.folder.glob('**/*.jsonl'):
            try:
                size, start = f.stat().st_size, self.offsets.get(f, 0)
                if size < start:  # rewritten: read again
                    start = 0
                if size == start:
                    continue
                with open(f, 'rb') as fh:
                    fh.seek(start)
                    data = fh.read()
            except OSError:
                continue
            end = data.rfind(b'\n') + 1  # a half-written last line waits for next time
            self.offsets[f] = start + end
            for line in data[:end].splitlines():
                self.add(line)
            time.sleep(pause)  # let the island breathe during the first big read

    def add(self, line):
        try:  # one odd line must not stop the reader thread
            d = json.loads(line)
            m = d.get('message')
            if not isinstance(m, dict) or not m.get('usage') or not d.get('timestamp'):
                return
            ts = dt.datetime.fromisoformat(d['timestamp'].replace('Z', '+00:00')).timestamp()
            tokens, cost = message_cost(m.get('model') or '', m['usage'])
        except (ValueError, TypeError, AttributeError):
            return
        self.replies[m.get('id') or d.get('requestId') or d.get('uuid')] = (ts, tokens, cost)

    def summary(self, now=None):
        now = now or time.time()
        blocks = []  # [start, end, tokens, $] per 5-hour window
        for ts, tokens, cost in sorted(self.replies.values()):
            if not blocks or ts >= blocks[-1][1]:
                start = ts - ts % 3600  # windows start on the hour of their first message
                blocks.append([start, start + WINDOW, 0, 0.0])
            blocks[-1][2] += tokens
            blocks[-1][3] += cost
        cur = blocks[-1] if blocks and blocks[-1][1] > now else None
        past = blocks[:-1] if cur else blocks
        peak = max((b[3] for b in past), default=0.0)
        today = dt.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
        days = [0.0] * 7  # $ per day, oldest first, today last
        week = [0, 0.0]
        for ts, tokens, cost in self.replies.values():
            ago = (today - dt.datetime.fromtimestamp(ts).replace(hour=0, minute=0, second=0, microsecond=0)).days
            if 0 <= ago < 7:
                days[6 - ago] += cost
                week[0] += tokens
                week[1] += cost
        today_tokens = sum(t for ts, t, _ in self.replies.values() if ts >= today.timestamp())
        return {'window_end': cur[1] if cur else None, 'window_tokens': cur[2] if cur else 0,
                'window_cost': cur[3] if cur else 0.0, 'peak_cost': peak,
                'pct': (cur[3] / peak if cur and peak else None), 'today_cost': days[6], 'today_tokens': today_tokens,
                'week_cost': week[1], 'week_tokens': week[0], 'days': days}


claude_usage = {}  # latest summary, written by the usage thread


def watch_claude():
    usage, first = ClaudeUsage(), True
    while True:
        usage.scan(pause=0.05 if first else 0)
        first = False
        claude_usage.update(usage.summary())  # same keys every time: no clear(), so readers never see it empty
        time.sleep(60)


def icon_png(size=180):
    """The island icon as PNG bytes, for the iPhone Home Screen (iOS wants PNG). Needs the Qt GUI thread."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    s = size / 256
    p.setBrush(QColor(THEME['surface']))
    p.drawRoundedRect(QRectF(8 * s, 78 * s, 240 * s, 100 * s), 50 * s, 50 * s)
    p.setBrush(QColor(SAKURA))
    for x, h in ((150, 40), (170, 64), (190, 30), (210, 52)):
        p.drawRoundedRect(QRectF(x * s, (128 - h / 2) * s, 11 * s, h * s), 5.5 * s, 5.5 * s)
    p.setBrush(QColor(THEME['fox']))
    p.drawEllipse(QRectF(40 * s, 106 * s, 44 * s, 44 * s))
    p.end()
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    img.save(buf, 'PNG')
    return bytes(buf.data())


def load_fonts():
    """Register the bundled fonts. Noto Sans has no Japanese or Thai, so Qt takes each such character from the
    substitutes in order: Kiwi Maru for kana and kanji, Noto Sans Thai for Thai, then Segoe UI for the rest."""
    for f in sorted(FONT_DIR.glob('*.ttf')):
        QFontDatabase.addApplicationFont(str(f))
    QFont.insertSubstitutions(FONT, ['Kiwi Maru', 'Noto Sans Thai', 'Segoe UI'])


def single_instance():
    """False when another island already runs (autostart plus a manual start)."""
    ctypes.windll.kernel32.CreateMutexW(None, False, 'DynamicIslandSingleInstance')
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


class Rect(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', Rect), ('rcWork', Rect), ('dwFlags', ctypes.c_ulong)]


def keyboard_language():
    """(native language name, short label) of the keyboard in the foreground app, e.g. ('ไทย', 'TH'),
    ('日本語', 'あ') when the Japanese IME types kana, ('日本語', 'A') when it types letters."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetKeyboardLayout.restype = ctypes.c_void_p
    hwnd = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None)
    lcid = (user32.GetKeyboardLayout(tid) or 0) & 0xFFFF
    if lcid not in LANG_NAMES:  # look a language up once; this runs twice a second
        name = ctypes.create_unicode_buffer(85)
        if not kernel32.LCIDToLocaleName(lcid, name, 85, 0):
            return None
        native = ctypes.create_unicode_buffer(80)
        kernel32.GetLocaleInfoEx(name.value, 0x4, native, 80)  # LOCALE_SNATIVELANGUAGENAME
        LANG_NAMES[lcid] = (native.value or name.value, name.value.split('-')[0].upper())
    native, label = LANG_NAMES[lcid]
    if label == 'JA':  # the IME's open state is kana input, closed is plain letters
        ctypes.windll.imm32.ImmGetDefaultIMEWnd.restype = ctypes.c_void_p
        ime = ctypes.windll.imm32.ImmGetDefaultIMEWnd(ctypes.c_void_p(hwnd))
        res = ctypes.c_size_t()  # IMC_GETOPENSTATUS; SMTO_ABORTIFHUNG: a frozen app must not freeze the island
        sent = ime and user32.SendMessageTimeoutW(ctypes.c_void_p(ime), 0x283, 0x5, 0, 0x2, 100, ctypes.byref(res))
        label = 'あ' if sent and res.value else 'A'
    return native, label


LANG_NAMES = {}  # keyboard LCID -> (native name, label)


def idle_seconds():
    """Seconds since the last mouse or keyboard input anywhere (the fox sleeps after a while)."""
    class LastInput(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]
    info = LastInput(ctypes.sizeof(LastInput), 0)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


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
    for attempt in range(2):  # the free endpoint fails now and then
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)
        except Exception:
            if attempt:
                raise
            time.sleep(2)


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
    def __init__(self, size=10, color=FG, bold=False, align=Qt.AlignLeft, family=FONT):
        super().__init__()
        self.setFont(QFont(family, size, QFont.Bold if bold else QFont.Normal))
        self.color, self.align, self.text = QColor(color), align, ''
        self.backdrop = False  # paint the pill's black behind the text, so things can pass behind it
        self.setFixedHeight(self.fontMetrics().height() + 2)

    def set(self, text, color=None):
        if text != self.text or (color and QColor(color) != self.color):
            self.text, self.color = text, QColor(color or self.color)
            self.update()

    @functools.lru_cache(maxsize=64)
    def shown(self, text, width):  # the elided text; repaints (a fox running past) don't measure it again
        return self.fontMetrics().elidedText(text, Qt.ElideRight, width)

    def text_box(self):
        """Where the text (with its backdrop) sits, in the parent island's coordinates."""
        text = self.shown(self.text, self.width())
        tw = self.fontMetrics().horizontalAdvance(text) + 12
        left = self.mapTo(self.window(), QPoint(0, 0)).x() + (self.width() - tw) / 2
        return QRectF(left, 0, tw, self.height())

    def paintEvent(self, e):
        p = QPainter(self)
        text = self.shown(self.text, self.width())
        if self.backdrop and text:
            box = p.boundingRect(self.rect(), self.align | Qt.AlignVCenter, text).adjusted(-6, -4, 6, 4)
            veil = QColor(BG)
            veil.setAlphaF(0.55)  # half see-through: the fox and petals show faintly, the text stays readable
            p.fillRect(box, veil)
        p.setPen(self.color)
        p.drawText(self.rect(), self.align | Qt.AlignVCenter, text)


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
            p.fillPath(path, QColor(THEME['sakura-tint']))


class Wave(QWidget):  # decorative equalizer bars
    def __init__(self, w, h):
        super().__init__()
        self.setFixedSize(w, h)
        self.levels, self.on, self.color = [0.25] * 4, False, QColor(SAKURA)
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

    def fit(self, text, font, width, min_pt=8):
        """(font, text) that fits the width: shrink first so the whole line stays readable, cut only past min_pt."""
        key = (text, font, int(width))
        if key not in self.elided:
            f = QFont(self.fonts[font])
            fm = QFontMetricsF(f)
            while f.pointSize() > min_pt and fm.horizontalAdvance(text) > width:
                f.setPointSize(f.pointSize() - 1)
                fm = QFontMetricsF(f)
            self.elided[key] = (f, fm.elidedText(text, Qt.ElideRight, width))
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
            p.setFont(self.fonts['small'])
            p.drawText(self.rect(), Qt.AlignCenter, 'No synced lyrics for this song')
            return
        # split view: original on the left, romaji / translation on the right, same row per line
        split = w * 0.42 if self.rows else w  # romaji runs about twice as long as the Japanese it spells
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
                font, text = self.fit(line[1] or '♪', 'small', box.width())
                p.setFont(font)
                p.drawText(box, Qt.AlignCenter, text)
            # right column: romaji big and in the song's color (easiest to read along), translation small below
            texts = [(row, t) for row in self.rows if (t := self.extra(line, row))]
            heights = [22 if row == 'pron' else self.EXTRA_H for row, _ in texts]
            top = y - sum(heights) / 2
            for (row, text), h in zip(texts, heights):
                font = ('romaji_now' if current else 'romaji') if row == 'pron' else 'small' if current else 'tiny'
                c = QColor(self.color if row == 'pron' else FG if current else DIM)
                c.setAlphaF(1 if current else fade * (1.6 if row == 'pron' else 1))
                p.setPen(c)
                qfont, text = self.fit(text, font, right_w)
                p.setFont(qfont)
                p.drawText(QRectF(right_x, top, right_w, h), Qt.AlignLeft | Qt.AlignVCenter, text)
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


class ShelfList(QListWidget):
    """Files parked on the island: drag them back out to any app (they are copied, the shelf keeps them),
    double-click opens, Delete removes. Drops are handled by the island so the whole pill is a target."""

    def __init__(self, on_change):
        super().__init__(viewMode=QListView.IconMode, flow=QListView.LeftToRight, wrapping=True,
                         resizeMode=QListView.Adjust, movement=QListView.Static, iconSize=QSize(36, 36),
                         gridSize=QSize(88, 70), selectionMode=QListWidget.ExtendedSelection, dragEnabled=True,
                         wordWrap=False, textElideMode=Qt.ElideMiddle)
        self.on_change, self.icons, self.dragging = on_change, QFileIconProvider(), False
        self.itemDoubleClicked.connect(lambda item: os.startfile(item.data(Qt.UserRole)))

    def paths(self):
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())]

    def add(self, paths):
        have = set(self.paths())
        for path in paths:
            if path not in have and os.path.exists(path):
                item = QListWidgetItem(self.icons.icon(QFileInfo(path)), os.path.basename(path.rstrip('\\/')) or path)
                item.setData(Qt.UserRole, path)
                item.setToolTip(path)
                self.addItem(item)
                have.add(path)
        self.on_change()

    def remove_selected(self):
        for item in self.selectedItems():
            self.takeItem(self.row(item))
        self.on_change()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove_selected()
        else:
            super().keyPressEvent(e)

    def mimeData(self, items):
        m = QMimeData()
        m.setUrls([QUrl.fromLocalFile(i.data(Qt.UserRole)) for i in items])
        return m

    def supportedDropActions(self):  # what a drag out offers: copy, so Explorer can't move the file away
        return Qt.CopyAction

    def startDrag(self, actions):
        self.dragging = True  # keep the island open while the file is carried out
        try:
            super().startDrag(actions)
        finally:
            self.dragging = False


FOX_BODY = ['           o o  ',  # 16 x 8 pixel fox facing right: o orange, w white, d dark
            '          ooooo ',
            ' ww       oodooo',
            'wooo  oooooooowd',
            ' ooooooooooooww ',
            '   oooooooooo   ']
FOX_LEGS = (['    o o   o o   ', '    d d   d d   '],  # standing / stride 1
            ['   o   o o   o  ', '  d     d d   d '])  # stride 2
FOX_SIT = ['           o o  ',  # sitting, tail around the paws; the wag frame moves the tail tip
           '          ooooo ',
           '          oodooo',
           '         ooooowd',
           '        ooooooww',
           '  ww   ooooooo  ',
           ' wooooooooooo   ',
           '         d d    ']
FOX_WAG = FOX_SIT[:5] + ['   ww  ooooooo  ', '  woooooooooo   ', FOX_SIT[7]]
FOX_SLEEP = ['                ',  # curled up, eye closed, tail over the nose
             '                ',
             '      ooooo o o ',
             '    ooooooooooo ',
             '   ooooooooodoo ',
             '  wwoooooooooowd',
             '   wwwwooooooo  ',
             '                ']
FOX_COLORS = {'o': THEME['fox'], 'w': THEME['fox-cream'], 'd': THEME['fox-ink']}


class Fox(QWidget):
    """Idle buddy in the closed pill when nothing plays. Runs back and forth and, between runs, picks something
    at random: stands, sits and wags, or looks around. Curls up asleep after the PC sits untouched for the
    time set in Settings, with z's floating up, and wakes when you come back."""
    PX = 2  # screen pixels per fox pixel
    W, H, Z = 32, 16, 10  # fox size in px, plus room above it (inside the pill) for the z's

    def __init__(self, island):
        super().__init__(island)
        self.island = island
        self.setFixedSize(self.W + 12, self.H + self.Z)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # hovering the fox still opens the island
        self.x, self.dir, self.frame, self.state, self.until = 0.0, 1, 0, 'run', 0.0
        self.idle_at, self.asleep = 0.0, False
        self.timer = QTimer(self, timeout=self.step, interval=90)

    def set_active(self, on):
        if on and not self.timer.isActive():
            self.x, self.state = self.island.shape.left() + 12, 'run'
            self.timer.start(90)
            self.show()
            self.step()
            self.island.petals.burst()  # a little sakura drift as the fox comes out
        elif not on and self.timer.isActive():
            self.timer.stop()
            self.hide()

    def go(self, state, seconds=0.0):
        self.state, self.until = state, time.time() + seconds
        # frames per state: running needs them; a resting or sleeping fox barely moves, so save CPU
        self.timer.setInterval({'run': 90, 'sleep': 400}.get(state, 220))

    def pick(self):  # what to do after a run
        self.go(*random.choices([('stand', random.uniform(1, 3)), ('sit', random.uniform(3, 7)),
                                 ('look', random.uniform(2, 3.5))], weights=(35, 40, 25))[0])

    def clear_of_clock(self):  # resting behind the clock would hide the fox: it only stops beside it
        box = self.island.c_text.text_box()
        return self.x + self.W < box.left() or self.x > box.right()

    def step(self):
        r, now = self.island.shape, time.time()
        lo, hi = r.left() + 12, r.right() - 12 - self.W
        if now - self.idle_at >= 2:  # idle time changes slowly: check it every 2 s, not every frame
            self.idle_at, self.asleep = now, idle_seconds() >= self.island.settings['fox_sleep']
        asleep = self.asleep
        if asleep and self.state not in ('sleep', 'run'):
            self.go('run')  # first find a free spot beside the clock
        if asleep and self.state == 'run' and self.clear_of_clock():
            self.go('sleep')
        elif not asleep and self.state == 'sleep':
            self.go('wake', 1.6)  # sits up for a moment, then runs again
        if self.state == 'run':
            self.x += self.dir * 2.2
            self.frame ^= 1
            if not lo <= self.x <= hi:  # an end: turn around and take a break
                self.x = min(max(self.x, lo), hi)
                self.dir = 1 if self.x == lo else -1
                if not asleep:
                    self.pick()
            elif random.random() < 0.006 and self.clear_of_clock() and not asleep:  # now and then a break
                self.pick()
        elif self.state != 'sleep' and now >= self.until:
            self.go('run')
        elif self.state == 'look' and random.random() < 0.08:
            self.dir = -self.dir  # glances one way, then the other
        self.frame = self.frame ^ 1 if self.state in ('sit', 'sleep') and random.random() < 0.35 else self.frame
        self.x = min(max(self.x, lo), hi)  # the pill may have changed size
        self.move(int(self.x), int(r.center().y() - self.H / 2 + 2 - self.Z))
        self.update()

    def rows(self):
        if self.state == 'run':
            return FOX_BODY + FOX_LEGS[self.frame]
        if self.state in ('sit', 'wake'):
            return FOX_WAG if self.state == 'sit' and self.frame else FOX_SIT
        if self.state == 'sleep':
            return FOX_SLEEP
        return FOX_BODY + FOX_LEGS[0]  # stand, look

    @functools.lru_cache(maxsize=16)
    def sprite(self, rows, facing):
        """One pose drawn once into a pixmap; painting is then a single copy instead of ~60 little squares."""
        pix = QPixmap(self.W, self.H)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        if facing < 0:  # facing left: mirror
            p.translate(self.W, 0)
            p.scale(-1, 1)
        for y, line in enumerate(rows):
            for x, ch in enumerate(line):
                if ch != ' ':
                    p.fillRect(x * self.PX, y * self.PX, self.PX, self.PX, QColor(FOX_COLORS[ch]))
        p.end()
        return pix

    def paintEvent(self, e):
        p = QPainter(self)
        breath = 1 if self.state == 'sleep' and self.frame else 0  # a slow rise and fall while asleep
        p.drawPixmap(0, self.Z + breath, self.sprite(tuple(self.rows()), self.dir))
        if self.state == 'sleep':  # three z's drifting up from the head
            p.setFont(QFont(ROUND, 7, QFont.Bold))
            head = self.W - 8 if self.dir > 0 else 4
            for i in range(3):
                t = (time.time() / 2.4 + i / 3) % 1
                c = QColor(THEME['lavender'])
                c.setAlphaF(1 - t)
                p.setPen(c)
                p.drawText(QPointF(head + 4 * t + 2 * i, self.Z + 6 - t * (self.Z + 2)), 'z')


class Veil(QWidget):
    """The island's black over the open page, see-through by `alpha`: fades a new page in."""

    def __init__(self, island):
        super().__init__(island)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.shape, self.alpha = QRectF(), 0.0
        self.hide()

    def cover(self, shape, alpha):
        if (shape, alpha) != (self.shape, self.alpha):
            self.shape, self.alpha = QRectF(shape), alpha
            self.setGeometry(shape.toAlignedRect())
            self.setVisible(alpha > 0.001)
            self.raise_()
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        c = QColor(BG)
        c.setAlphaF(min(1.0, self.alpha))
        p.setBrush(c)
        r = min(self.shape.height() / 2, 34)
        p.drawRoundedRect(QRectF(self.rect()), r, r)


class Petals(QWidget):
    """Sakura drifts across the closed pill while the fox is out: a burst when it comes out, then a few at a time.
    50 ms frames, not 33: every frame repaints the see-through window."""

    def __init__(self, island):
        super().__init__(island)
        self.island, self.petals = island, []
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.timer = QTimer(self, timeout=self.step, interval=50)
        self.hide()

    def petal(self, spread=70):
        r = self.island.shape
        return {'x': r.width() + random.uniform(0, spread), 'y': random.uniform(4, r.height() - 6),
                'vx': -random.uniform(1.3, 2.7), 'phase': random.uniform(0, 6.3), 'rot': random.uniform(0, 360),
                'spin': random.uniform(-9, 9), 'size': random.uniform(4, 6.5),
                'color': random.choice((THEME['sakura'], THEME['lavender'], '#ffd1e6'))}

    def burst(self, n=7):
        self.setGeometry(self.island.shape.toAlignedRect())
        self.petals = [self.petal() for _ in range(n)]
        self.show()
        self.timer.start()

    def step(self):
        self.setGeometry(self.island.shape.toAlignedRect())
        for pt in self.petals:
            pt['x'] += pt['vx']
            pt['phase'] += 0.12
            pt['y'] += math.sin(pt['phase']) * 0.4
            pt['rot'] += pt['spin']
        self.petals = [pt for pt in self.petals if pt['x'] > -10]
        if self.island.fox.isVisible() and len(self.petals) < 3 and random.random() < 0.04:
            self.petals.append(self.petal(10))  # one more now and then, never more than 3 after the burst
        if not self.island.fox.isVisible():  # keeps running while the fox is out, even between petals
            self.timer.stop()
            self.hide()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self.height() / 2, self.height() / 2)
        p.setClipPath(path)  # petals stay inside the pill
        p.setPen(Qt.NoPen)
        for pt in self.petals:
            c = QColor(pt['color'])
            c.setAlphaF(0.85)
            p.setBrush(c)
            p.save()
            p.translate(pt['x'], pt['y'])
            p.rotate(pt['rot'])
            s = pt['size']
            p.drawEllipse(QRectF(-s / 2, -s / 3, s, s * 0.66))
            p.restore()


class UsageRing(QWidget):
    """The current 5-hour Claude window as a ring: how full it is next to your heaviest window so far."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(116, 124)
        self.pct = None

    def set(self, pct):
        if pct != self.pct:
            self.pct = pct
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(10, 6, 96, 96)
        p.setPen(QPen(QColor(LINE), 9, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(r)
        if self.pct:
            color = ORANGE if self.pct >= 0.9 else SAKURA  # nearly as heavy as your biggest window: fox orange
            p.setPen(QPen(QColor(color), 9, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(r, 90 * 16, -int(min(self.pct, 1) * 360 * 16))
        p.setPen(QColor(FG))
        p.setFont(QFont(ROUND, 17, QFont.Bold))
        p.drawText(r, Qt.AlignCenter, f'{self.pct:.0%}' if self.pct is not None else '–')
        p.setPen(QColor(DIM))
        p.setFont(QFont(FONT, 7))
        p.drawText(QRectF(0, 106, self.width(), 16), Qt.AlignCenter,
                   'of your biggest window' if self.pct is not None else 'no window running')


def short_money(n):  # same as the phone page: 950, 1.2k, 15k
    return f'{n / 1e3:.0f}k' if n >= 1e4 else f'{n / 1e3:.1f}k' if n >= 1e3 else f'{n:.0f}'


class MoneyCalendar(QWidget):
    """Month grid like the phone page: money out (coral) and in (mint) on each day; a day spent over 1.5x the
    cycle's daily average gets a sakura tint, today a sakura ring."""

    def __init__(self):
        super().__init__()
        self.month, self.days, self.avg = dt.date.today().replace(day=1), {}, 0.0

    def set(self, days, avg):
        if (days, avg) != (self.days, self.avg):
            self.days, self.avg = days, avg
            self.update()

    def shift(self, months):
        self.month = shift_month(self.month, months)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cw, top = self.width() / 7, 16
        rows = math.ceil((self.month.weekday() + calendar.monthrange(self.month.year, self.month.month)[1]) / 7)
        ch = (self.height() - top) / rows
        p.setFont(QFont(FONT, 7))
        p.setPen(QColor(DIM))
        for i, wd in enumerate(('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')):
            p.drawText(QRectF(i * cw, 0, cw, top), Qt.AlignCenter, wd)
        today = dt.date.today()
        for day in range(1, calendar.monthrange(self.month.year, self.month.month)[1] + 1):
            d = self.month.replace(day=day)
            k = self.month.weekday() + day - 1  # cell index, Monday first
            cell = QRectF(k % 7 * cw + 2, top + k // 7 * ch + 2, cw - 4, ch - 4)
            i, o = self.days.get(d, (0.0, 0.0))
            p.setPen(QPen(QColor(SAKURA), 1.5) if d == today else Qt.NoPen)
            p.setBrush(QColor(THEME['sakura-tint'] if o and self.avg and o > self.avg * 1.5 else THEME['surface']))
            p.drawRoundedRect(cell, 8, 8)
            p.setPen(QColor(FG if d == today else DIM))
            p.setFont(QFont(FONT, 7))
            p.drawText(cell.adjusted(5, 2, 0, 0), Qt.AlignLeft | Qt.AlignTop, str(day))
            p.setFont(QFont(FONT, 6, QFont.Bold))
            lines = [(f'−{short_money(o)}', RED)] * bool(o) + [(f'+{short_money(i)}', GREEN)] * bool(i)
            for n, (text, color) in enumerate(reversed(lines)):  # bottom up: in at the bottom, out above it
                p.setPen(QColor(color))
                p.drawText(cell.adjusted(0, 0, -4, -1 - n * 10), Qt.AlignRight | Qt.AlignBottom, text)


class DayBars(QWidget):
    """$ value per day for the last 7 days, today last and in sakura."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(110)
        self.values = [0.0] * 7

    def set(self, values):
        if values != self.values:
            self.values = list(values)
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        top = max(self.values) or 1
        w, h, base = self.width(), self.height(), self.height() - 16
        slot = w / 7
        today = dt.date.today()
        for i, v in enumerate(self.values):
            bh = max(3.0, (base - 14) * v / top)
            x = i * slot + slot * 0.22
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(SAKURA if i == 6 else PURPLE if v else LINE))
            p.drawRoundedRect(QRectF(x, base - bh, slot * 0.56, bh), 4, 4)
            day = today - dt.timedelta(days=6 - i)
            p.setPen(QColor(FG if i == 6 else DIM))
            p.setFont(QFont(FONT, 7))
            p.drawText(QRectF(i * slot, base + 2, slot, 14), Qt.AlignCenter, day.strftime('%a')[:2])
            if i == 6 and v:
                p.setPen(QColor(SAKURA))
                p.drawText(QRectF(i * slot - 10, base - bh - 14, slot + 20, 12), Qt.AlignCenter, f'${v:.0f}')


class QRCode(QWidget):
    """A link as a QR code for the iPhone camera: dark squares on cream, rounded like the island."""

    def __init__(self, size=120):
        super().__init__()
        self.setFixedSize(size, size)
        self.text, self.matrix = None, ()

    def set(self, text):
        if text != self.text:
            self.text = text
            self.matrix = segno.make(text, error='m').matrix if text else ()
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if not self.matrix:  # phone page off: an empty card where the code will be
            p.setBrush(QColor(PANEL))
            p.drawRoundedRect(self.rect(), 14, 14)
            p.setPen(QColor(FAINT))
            p.setFont(QFont(FONT, 9))
            p.drawText(self.rect(), Qt.AlignCenter, 'QR code')
            return
        p.setBrush(QColor(THEME['fox-cream']))
        p.drawRoundedRect(self.rect(), 14, 14)
        n = len(self.matrix) + 6  # 3 light modules round the code so the camera finds its edge
        cell = self.width() / n
        p.setBrush(QColor(BG))
        for y, line in enumerate(self.matrix):
            for x, dark in enumerate(line):
                if dark:
                    p.drawRect(QRectF((x + 3) * cell, (y + 3) * cell, cell + 0.4, cell + 0.4))  # +0.4: no seams


class TabBar(QWidget):
    """Tab icons centred at the top of the open island. The island is centred too, so the icons stay in the
    same screen spot when it resizes for another tab; a soft sakura pill slides to the active one."""

    def __init__(self, buttons):
        super().__init__()
        self.buttons, self.index, self.pos = buttons, -1, 0.0  # pos: the pill's place, in tabs (2.5 = between 2 and 3)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addStretch(1)
        for b in buttons:
            lay.addWidget(b)
        lay.addStretch(1)
        self.anim = QVariantAnimation(self, duration=260, easingCurve=QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(lambda v: (setattr(self, 'pos', v), self.update()))

    def select(self, i):
        if i == self.index:
            return
        if self.index < 0:
            self.pos = float(i)
        else:  # animate the tab position, not pixels: the bar resizes with the island while the pill slides
            self.anim.stop()
            self.anim.setStartValue(self.pos)
            self.anim.setEndValue(float(i))
            self.anim.start()
        self.index = i
        self.update()

    def paintEvent(self, e):
        if self.index < 0:
            return
        i, f = int(self.pos), self.pos - int(self.pos)
        a = QRectF(self.buttons[i].geometry())
        b = QRectF(self.buttons[min(i + 1, len(self.buttons) - 1)].geometry())
        r = QRectF(a.x() + (b.x() - a.x()) * f, a.y(), a.width(), a.height()).adjusted(2, 1, -2, -1)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(THEME['sakura-tint']))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)


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
        p.setBrush(QColor(SAKURA if self.isChecked() else LINE))
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
             'QPushButton:hover{{color:' + SAKURA + '}}'


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
        lay.addStretch(1) if it is None else lay.addSpacing(it) if isinstance(it, int) else lay.addWidget(it)
    return w


STYLE = f'''
QLineEdit, QTimeEdit {{ background:{PANEL}; color:{FG}; border:none; border-radius:10px; padding:4px 8px;
                       font:10pt "{FONT}"; selection-background-color:{SAKURA}; selection-color:{ON_SAKURA}; }}
QTimeEdit::up-button, QTimeEdit::down-button {{ width:0; }}
QPushButton#pill {{ background:{PANEL}; color:{FG}; border:none; border-radius:12px; padding:4px 12px; font:9pt "{FONT}"; }}
QPushButton#pill:hover {{ background:{LINE}; }}
QPushButton#pill:pressed {{ background:{SAKURA}; color:{ON_SAKURA}; }}
QMenu {{ background:{PANEL}; color:{FG}; border:none; }}
QMenu::item:selected {{ background:{SAKURA}; color:{ON_SAKURA}; }}
QListWidget {{ background:{THEME['surface']}; color:{FG}; border:none; border-radius:14px; padding:2px; font:9pt "{FONT}";
               outline:0; }}
QListWidget::item:selected {{ background:{THEME['sakura-tint']}; border-radius:8px; }}
QCalendarWidget QWidget {{ background:{BG}; color:{FG}; alternate-background-color:{BG}; }}
QCalendarWidget QAbstractItemView {{ selection-background-color:{SAKURA}; selection-color:{ON_SAKURA};
                                     font:9pt "{FONT}"; outline:0; }}
QCalendarWidget QAbstractItemView:disabled {{ color:{FAINT}; }}
QCalendarWidget QToolButton {{ color:{FG}; background:transparent; border:none; font:bold 10pt "{FONT}"; padding:2px 6px; }}
QCalendarWidget QToolButton:hover {{ background:{PANEL}; border-radius:10px; }}
QCalendarWidget QToolButton::menu-indicator {{ image:none; }}
QCalendarWidget QSpinBox {{ background:{PANEL}; color:{FG}; border:none; }}
QComboBox {{ background:{PANEL}; color:{FG}; border:none; border-radius:10px; padding:3px 10px; font:9pt "{FONT}"; }}
QComboBox::drop-down {{ border:none; width:0; }}
QComboBox QAbstractItemView {{ background:{PANEL}; color:{FG}; selection-background-color:{SAKURA};
                               selection-color:{ON_SAKURA}; border:none; }}
QToolTip {{ background:{PANEL}; color:{FG}; border:none; }}
QLabel#body {{ color:{DIM}; font:10pt "{FONT}"; }}
QPushButton#primary {{ background:{SAKURA}; color:{ON_SAKURA}; border:none; border-radius:12px; padding:4px 16px;
                       font:bold 9pt "{FONT}"; }}
QPushButton#primary:hover {{ background:{THEME['sakura-deep']}; }}
'''


# ---------- island ----------
class Island(QWidget):
    def __init__(self):
        load_fonts()  # before any widget picks its font
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        first_run = not SETTINGS_FILE.exists()
        self.settings, self.tiny, self.fs_at = load_settings(), False, 0
        self.updating, self.quit_now, self.update_msg = None, False, ''  # set by the update thread, acted on in tick
        self.drop_hover = False  # files dragged over the island
        if first_run and FROZEN:  # freshly installed: start with Windows by default (toggle in Settings)
            autostart(True)
            self.set_setting('auto_update', True)
        for key, options in (('translation', TRANSLATIONS), ('pronunciation', PRONUNCIATIONS)):
            if self.settings[key] not in [v for _, v in options]:  # e.g. Korean from an older version
                self.settings[key] = None
        prefs.update(lang=self.settings['translation'], romaji=bool(self.settings['pronunciation']))
        money_prefs.update(payday=self.settings['payday'], currency=self.settings['currency'])
        self.place()
        self.expanded, self.tab, self.alert, self.alert_at = False, None, None, 0
        self.timer_end, self.reminders, self.art_key, self.goal = None, load_reminders(), False, None
        self.shape = QRectF()
        self.anim = QVariantAnimation(self, duration=550)
        self.anim.valueChanged.connect(self.set_shape)
        self.anim.finished.connect(self.fit_window)
        load_money()
        self.money_server, self.money_at, self.flash = None, 0, None  # flash: (text, color, until, pill width)
        self.lang = None  # keyboard language last seen, (native name, label)
        self.claude_end = None  # end of the running 5-hour Claude window, to flash when it resets
        money_last['at'] = 0  # entries loaded from disk are not news
        if not self.settings['phone_key']:
            self.set_setting('phone_key', secrets.token_urlsafe(9))  # pairing key in every phone URL and Shortcut
        self.build()
        self.set_phone_sync(self.settings['phone_sync'])
        threading.Thread(target=self.sync_loop, daemon=True).start()
        self.shape = self.target()
        self.morph()
        if first_run:  # a new user: say hello once the island is on screen
            QTimer.singleShot(1200, self.open_guide)
        self.tick_timer = QTimer(self, timeout=self.tick, interval=100)
        self.tick_timer.start()
        QTimer(self, timeout=self.stats, interval=1000).start()
        QTimer(self, timeout=self.check_language, interval=500).start()
        self.stats()
        if FROZEN:  # source runs update with git; the installed .exe checks GitHub a minute after start, then every 6 h
            QTimer.singleShot(60_000, self.check_update)
            QTimer(self, timeout=self.check_update, interval=6 * 3600_000).start()

    def check_language(self):
        """Pop the keyboard language up on the pill for 1.5 s when it changes (Win+Space, Alt+Shift, IME あ/A)."""
        try:
            lang = keyboard_language()
        except OSError:
            return
        if lang and lang != self.lang:
            if self.lang:  # not on start-up
                self.flash = (f'{lang[0]}   {lang[1]}', SAKURA, time.time() + 1.5, 200)
                self.tick()
            self.lang = lang

    def check_update(self, now=False):
        """now: the Check now button, which also runs with auto update off and says what it found."""
        def run():
            try:
                found = newer_release()
                if found:
                    self.updating = found[0]
                    self.update_msg = f'updating to v{found[0]}…'
                    install_update(found[1])
                    self.quit_now = True  # the installer starts the new version
                else:
                    self.update_msg = 'up to date'
            except Exception:  # offline or GitHub down: try again next time
                self.updating, self.update_msg = None, "can't reach GitHub"
        if (now or self.settings['auto_update']) and not self.updating:
            self.update_msg = 'checking…' if now else ''
            threading.Thread(target=run, daemon=True).start()

    def build(self):
        # compact: art | text | waveform or countdown
        self.c_art = Art(24, 12)  # round, matching the pill's ends
        self.c_text = Text(10, bold=True, align=Qt.AlignCenter)
        self.c_wave = Wave(20, 14)
        self.c_timer = Text(10, ORANGE, bold=True, align=Qt.AlignRight)
        self.c_timer.setFixedWidth(46)
        self.compact = row(self.c_art, self.c_text, self.c_wave, self.c_timer, spacing=8)
        self.compact.setParent(self)
        self.compact.layout().setContentsMargins(8, 6, 10, 6)  # near-even sides keep the clock centered
        self.petals = Petals(self)
        self.petals.stackUnder(self.compact)
        self.fox = Fox(self)
        self.fox.stackUnder(self.petals)  # order, bottom to top: fox, petals, clock (stays readable)
        self.fox.hide()
        self.compact.layout().setStretch(1, 1)

        # expanded header: centred tab icons that never move (the date lives in the closed pill)
        self.tab_buttons = [icon_button(g, 10, lambda _=False, t=t: self.set_tab(t))
                            for g, t in zip((MUSIC, LYRICS, SHELF, MONEY, SYSTEM, CLAUDE, TIMER, CALENDAR, GEAR), TABS)]
        header = self.tab_bar = TabBar(self.tab_buttons)

        # media page
        self.f_art = Art(64, 14)
        self.f_title = Text(12, bold=True)
        self.f_artist = Text(10, DIM)
        self.f_lyric = Text(11, SAKURA)
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

        # shelf page: files dropped on the island
        self.shelf_hint = Text(8, DIM)
        self.shelf = ShelfList(self.save_shelf)
        try:
            self.shelf.add(json.loads(SHELF_FILE.read_text('utf-8')))  # files deleted since are skipped
        except (OSError, ValueError):
            pass
        shelf_bottom = row(self.shelf_hint, pill_button('Remove', self.shelf.remove_selected),
                           pill_button('Clear', lambda: (self.shelf.clear(), self.save_shelf())), spacing=6)
        shelf_bottom.layout().setStretch(0, 1)
        shelf_page = column(self.shelf, shelf_bottom, spacing=6)
        shelf_page.layout().setStretch(0, 1)
        self.setAcceptDrops(True)
        self.save_shelf()  # writes back without missing files, sets the hint

        # money page: today / month totals, latest entries, phone link
        self.m_totals = [Text(14, c, bold=True, family=ROUND) for c in (GREEN, RED, GREEN, RED)]
        heads = []
        for label in ('TODAY', 'PAY CYCLE'):
            heads.append(Text(8, DIM))
            heads[-1].set(label)
        for w in self.m_totals + heads:  # Text has no natural width; without this the list squeezes them to 0
            w.setFixedWidth(150)  # round Kiwi Maru digits run wide: "+25,500.00 ฿" must fit
        self.m_rows = [(Text(9, DIM), Text(9), Text(10, bold=True, align=Qt.AlignRight)) for _ in range(4)]
        for when, _, amount in self.m_rows:
            when.setFixedWidth(72)
            amount.setFixedWidth(96)
        self.m_link = Text(8, DIM)
        self.b_phone = pill_button('Connect iPhone', lambda: self.set_phone_sync(True))
        self.b_open = pill_button('Open', lambda: os.startfile(self.phone_url()) if self.phone_url() else None)
        self.b_qr = pill_button('QR code', lambda: self.open_guide(2))  # the guide's phone step
        # left: totals with the month calendar under them; right: latest entries with the budget under them
        self.m_cal, self.m_month = MoneyCalendar(), Text(9, FG, bold=True, align=Qt.AlignCenter)
        self.m_cal.setFixedSize(318, 232)  # room for the day number and two amounts
        self.m_month.setFixedWidth(120)
        self.m_budget = [Text(13, c, bold=True, family=ROUND) for c in (FG, GREEN, SAKURA)]  # avg, can spend, payday
        stats = []
        for label, value in zip(('AVG SPENT / DAY', 'CAN SPEND / DAY', 'PAYDAY IN'), self.m_budget):
            head = Text(8, DIM)
            head.set(label)
            head.setFixedWidth(112)
            value.setFixedWidth(112)
            stats.append(column(head, value, spacing=0))
        month_bar = row(None, pill_button('‹', lambda: (self.m_cal.shift(-1), self.refresh_money())), self.m_month,
                        pill_button('›', lambda: (self.m_cal.shift(1), self.refresh_money())), None, spacing=4)
        money_page = column(
            row(column(row(column(heads[0], self.m_totals[0], self.m_totals[1], spacing=0),
                           column(heads[1], self.m_totals[2], self.m_totals[3], spacing=0), spacing=18),
                       month_bar, self.m_cal, spacing=4),
                column(*(row(*r, spacing=6) for r in self.m_rows), None, row(*stats, None, spacing=8), spacing=2), spacing=18),
            None, row(self.m_link, self.b_phone, self.b_qr, self.b_open, spacing=6), spacing=6)
        money_page.layout().itemAt(0).widget().layout().setStretch(1, 1)
        money_page.layout().itemAt(2).widget().layout().setStretch(0, 1)

        # claude page: 5-hour window ring | reset, today, week | last 7 days
        self.u_ring, self.u_bars = UsageRing(), DayBars()
        self.u_reset, self.u_window = Text(13, FG, bold=True, family=ROUND), Text(9, DIM)
        self.u_today, self.u_week, self.u_note = Text(10), Text(10), Text(7, FAINT)
        self.u_note.set('Estimate from Claude Code logs, at API prices')
        self.u_bars.setFixedWidth(200)
        claude_page = row(self.u_ring, column(self.u_reset, self.u_window, None, self.u_today, self.u_week, self.u_note,
                                              spacing=3), self.u_bars, spacing=16)
        claude_page.layout().setStretch(1, 1)

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
        self.t_big = Text(30, ORANGE, bold=True, align=Qt.AlignCenter, family=ROUND)
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
        self.cal.setFixedSize(300, 214)  # English day names need the width: Sunday was cut off
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

        sleep_box = QComboBox()
        sleep_box.addItems([label for label, _ in FOX_SLEEP_AFTER])
        sleep_box.setCurrentIndex(next((i for i, (_, s) in enumerate(FOX_SLEEP_AFTER)
                                        if s == self.settings['fox_sleep']), 2))
        sleep_box.currentIndexChanged.connect(lambda i: self.set_setting('fox_sleep', FOX_SLEEP_AFTER[i][1]))
        self.phone_switch = Switch(self.settings['phone_sync'], self.set_phone_sync)
        # another PC's island: paste the main PC's phone link, both then share one money history
        self.sync_edit = QLineEdit(placeholderText="Main PC's phone link")
        if self.settings['sync_url']:
            self.sync_edit.setText(f'{self.settings["sync_url"]}/app/{self.settings["sync_key"]}')
        self.sync_edit.setFixedWidth(150)
        self.sync_edit.editingFinished.connect(self.set_sync_link)
        self.sync_status = Text(10)  # "Link to main PC · Synced 12:03", the row's label
        # the same two pickers sit in Settings and in the guide; sync_money_widgets keeps them equal
        self.payday_boxes, self.currency_boxes = [], []
        for _ in range(2):
            pay, cur = QComboBox(), QComboBox()
            pay.addItems([f'{d}{ {1: "st", 2: "nd", 3: "rd"}.get(d % 10 * (d // 10 != 1), "th")}' for d in range(1, 32)])
            pay.setMaxVisibleItems(12)
            cur.addItems(CURRENCIES)
            pay.currentIndexChanged.connect(lambda i: self.set_money_pref(payday=i + 1))
            cur.currentIndexChanged.connect(lambda i: self.set_money_pref(currency=CURRENCIES[i]))
            self.payday_boxes.append(pay)
            self.currency_boxes.append(cur)
        def group(title, *rows):
            t = Text(9, SAKURA, bold=True)
            t.set(title)
            return column(t, *rows, spacing=6)

        self.update_label = Text(10)
        self.update_label.set(f'Auto update  ·  v{VERSION}')
        self.update_label.setMinimumWidth(170)  # Text has no natural width: the stretch next to it would eat it
        settings_page = column(
            row(column(group('General', setting('Start with Windows', Switch(autostart(), autostart)),
                             setting('Screen', self.screen_box), setting('Alert sound', toggle('sound'))),
                       group('Island', setting('Tiny in fullscreen games', toggle('tiny_fullscreen')),
                             setting('Fox runs when idle', toggle('fox')), setting('Fox sleeps after idle', sleep_box)),
                       group('Update', row(self.update_label, None, pill_button('Check now', lambda: self.check_update(True)),
                                           toggle('auto_update'), spacing=8)),
                       None, spacing=12),
                column(group('Lyrics', setting('Lyrics in small island', toggle('compact_lyrics')),
                             setting('Translation', choice('translation', TRANSLATIONS)),
                             setting('Pronunciation', choice('pronunciation', PRONUNCIATIONS))),
                       group('Money', setting('iPhone sync', self.phone_switch),
                             setting('Payday and currency', row(self.payday_boxes[0], self.currency_boxes[0], spacing=6)),
                             row(self.sync_status, self.sync_edit, spacing=8)),
                       None, spacing=12),
                spacing=24),
            None,
            row(None, pill_button('Show guide', lambda: self.open_guide(0)), pill_button('Quit island', QApplication.quit)),
            spacing=6)
        cols = settings_page.layout().itemAt(0).widget().layout()
        cols.setStretch(0, 1)  # two equal columns; else the combo boxes on the right take the space
        cols.setStretch(1, 1)

        # guide: a few steps on first start (and from Settings); the phone step also opens from the Money tab
        def body(text):
            label = QLabel(text, objectName='body', wordWrap=True)
            label.setTextFormat(Qt.PlainText)
            return label

        def heading(text):
            t = Text(15, SAKURA, bold=True, family=ROUND)
            t.set(text)
            return t

        def labelled(text, widget):
            t = Text(10)
            t.set(text)
            t.setFixedWidth(80)
            return row(t, widget, None, spacing=8)

        self.g_qr, self.g_link = QRCode(124), Text(8, DIM)
        self.g_phone_on = pill_button('Turn on phone page', lambda: self.set_phone_sync(True))
        steps = [
            column(heading("Hi! I'm your Dynamic Island 🌸"), body(
                'I sit at the top of your screen. Hover me to open, move the mouse away to close, '
                'right-click me to quit.\n\nWhen nothing plays, a little fox runs around in me. In fullscreen games '
                'I shrink to a thin bar.'), None, spacing=10),
            column(heading("What's inside"), body(
                'Music and Lyrics: whatever plays, YouTube too, with synced lyrics, romaji and translation.\n'
                'Shelf: drop files on me, drag them out when you need them.\n'
                'Money · System · Claude · Timer · Calendar: one icon each at the top.\n'
                'Settings (the gear): everything else, and this guide again.'), None, spacing=10),
            row(column(heading('Money on your iPhone'), body(
                '1. Turn on the phone page. If Windows asks, click Allow.\n'
                '2. Your iPhone must be on the same Wi-Fi as this PC. Away from home? Install Tailscale on both.\n'
                '3. Scan the code with the iPhone camera and open it in Safari.\n'
                "4. Share → Add to Home Screen. Don't need Money? Just click Next."),
                row(self.g_phone_on, None), self.g_link, None, spacing=6),
                column(None, self.g_qr, None), spacing=16),
            column(heading('Payday'), body(
                'Your pay cycle starts on payday, so I can tell how much you can spend each day. Mark your pay as '
                '💼 Salary and the cycle starts on the day it really comes.\n'
                'Bank messages are read for KBank only. With another bank, add money by hand on the phone.'),
                labelled('Payday', self.payday_boxes[1]), labelled('Currency', self.currency_boxes[1]), None,
                spacing=8),
            column(heading('All set!'), body(
                'I start with Windows (you can turn it off in Settings) and update myself.\n\n'
                'Open this guide again any time: Settings → Show guide.'), None, spacing=10)]
        self.g_steps = QStackedWidget()
        for s in steps:
            self.g_steps.addWidget(s)
        self.g_dots = Text(9, DIM)
        self.g_skip = pill_button('Skip', self.close_guide)
        self.g_back = pill_button('Back', lambda: self.guide_step(-1))
        self.g_next = QPushButton('Next', objectName='primary')
        self.g_next.setCursor(Qt.PointingHandCursor)
        self.g_next.setFocusPolicy(Qt.NoFocus)
        self.g_next.clicked.connect(lambda: self.guide_step(1))
        guide_page = column(self.g_steps, row(self.g_dots, None, self.g_skip, self.g_back, self.g_next, spacing=6),
                            spacing=8)
        guide_page.layout().setStretch(0, 1)
        self.sync_money_widgets()

        # alert page
        self.a_text = Text(14, ORANGE, bold=True, align=Qt.AlignCenter)
        alert_page = column(None, self.a_text, row(None, pill_button('Dismiss', self.dismiss), None), None,
                            spacing=12)

        self.pages = QStackedWidget()
        for name, page in zip(TABS + ['alert', 'guide'], (media_page, lyrics_page, shelf_page, money_page, system_page,
                                                          claude_page, timer_page, calendar_page, settings_page,
                                                          alert_page, guide_page)):
            page.setObjectName(name)
            self.pages.addWidget(page)
        self.pages.setCurrentWidget(alert_page)  # first tick then switches page and colors the tabs
        self.full = column(header, self.pages, spacing=4)
        self.full.setParent(self)
        self.full.layout().setContentsMargins(22, 10, 22, 14)

        self.c_fx = QGraphicsOpacityEffect(self.compact)
        self.compact.setGraphicsEffect(self.c_fx)
        # pages fade in under a black veil, not an opacity effect: with the effect, the calendar came out
        # see-through on the layered window
        self.veil = Veil(self)

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

    # --- money / iPhone ---
    def set_phone_sync(self, on):
        self.set_setting('phone_sync', on)
        if on and not self.money_server:
            try:
                self.money_server = start_money_server(self.settings['phone_key'], icon_png(), self.set_money_pref,
                                                       self.handle_sync)
            except OSError as e:  # port taken by another program
                self.m_link.set(f'Phone sync failed: port {MONEY_PORT} busy ({e.strerror})', RED)
                return self.sync_money_widgets()
        elif not on and self.money_server:
            self.money_server.shutdown()
            self.money_server.server_close()
            self.money_server = None
        self.b_phone.setVisible(not self.money_server)
        self.b_open.setVisible(bool(self.money_server))
        self.m_link.set(f'iPhone: {self.phone_url()}  (Safari → Share → Add to Home Screen)' if self.money_server
                        else 'Connect your iPhone to add money from bank SMS (KBank), slips or by hand', DIM)
        self.sync_money_widgets()

    def sync_money_widgets(self):
        """Settings and guide pickers, phone switch and QR code follow the current values (they can change from
        the phone, or from the other copy of a picker)."""
        for box, i in ([(b, money_prefs['payday'] - 1) for b in self.payday_boxes] +
                       [(b, CURRENCIES.index(money_prefs['currency'])) for b in self.currency_boxes]):
            if box.currentIndex() != i:
                box.blockSignals(True)
                box.setCurrentIndex(i)
                box.blockSignals(False)
        on = bool(self.money_server)
        if self.phone_switch.isChecked() != on:
            self.phone_switch.blockSignals(True)
            self.phone_switch.setChecked(on)
            self.phone_switch.blockSignals(False)
        self.g_qr.set(self.phone_url())
        self.g_phone_on.setVisible(not on)
        self.update_label.set(f'v{VERSION}  ·  {self.update_msg}' if self.update_msg else f'Auto update  ·  v{VERSION}')
        s = sync_state
        if not self.settings['sync_url']:
            self.sync_status.set('Link to main PC', FG)
        else:
            self.sync_status.set('Main PC  ·  ' +(s['error'] or (time.strftime('synced %H:%M', time.localtime(s['at']))
                                 if s['at'] else 'linking…')), RED if s['error'] else FG)
        self.g_link.set(self.phone_url() or "The code shows here once the phone page is on.")

    # --- guide ---
    def open_guide(self, step=0):
        self.expanded, self.tab = True, 'guide'
        self.show_guide_step(step)
        self.sync_money_widgets()
        self.tick()

    def show_guide_step(self, i):
        n = self.g_steps.count()
        self.g_steps.setCurrentIndex(i)
        self.g_dots.set('  '.join('●' if k == i else '○' for k in range(n)))
        self.g_back.setVisible(i > 0)
        self.g_skip.setVisible(i < n - 1)
        self.g_next.setText('Done' if i == n - 1 else 'Next')

    def guide_step(self, delta):
        i = self.g_steps.currentIndex() + delta
        if i >= self.g_steps.count():
            return self.close_guide()
        self.show_guide_step(max(0, i))

    def close_guide(self):
        self.tab = None
        self.tick()
        self.maybe_collapse()

    def set_money_pref(self, payday=None, currency=None, mt=None):
        """From Settings, the guide, the phone or a sync (server thread): only plain values change here; the
        widgets catch up in tick. mt: when it was changed, the newer change wins in a sync."""
        if payday is not None:
            payday = int(payday)
            if not 1 <= payday <= 31:
                raise ValueError(payday)
            money_prefs['payday'] = payday
            self.set_setting('payday', payday)
        if currency is not None:
            if currency not in CURRENCIES:
                raise ValueError(currency)
            money_prefs['currency'] = currency
            self.set_setting('currency', currency)
        self.set_setting('prefs_mt', mt or time.time())
        self.money_at = 0  # Money tab redraws on the next tick

    # --- sync with the island on another PC (Settings → Link to main PC) ---
    def sync_payload(self):
        with money_lock:
            tx = [{k: v for k, v in t.items() if k != 'raw'} for t in money]
            deleted = dict(money_deleted)
        return {'tx': tx, 'deleted': deleted, 'prefs': {**money_prefs, 'mt': self.settings['prefs_mt']}}

    def take_sync(self, d):
        if merge_money(d['tx'], d['deleted']):
            self.money_at = 0
        p = d['prefs']
        if p['mt'] > self.settings['prefs_mt']:
            self.set_money_pref(p['payday'], p['currency'], p['mt'])

    def handle_sync(self, body):  # server thread
        self.take_sync(body)
        return self.sync_payload()

    def sync_loop(self):
        """Every minute: send everything here to the main PC, take back everything there.
        shortcut: sends the whole history each time (fine for a few thousand entries); send only changes if it grows."""
        while True:
            url, key = self.settings.get('sync_url'), self.settings.get('sync_key')
            if url and key:
                try:
                    req = urllib.request.Request(f'{url}/api/sync', json.dumps({**self.sync_payload(), 'key': key}).encode(),
                                                 {'Content-Type': 'application/json'})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        self.take_sync(json.loads(r.read()))
                    sync_state.update(at=time.time(), error=None)
                except urllib.error.HTTPError as e:
                    sync_state['error'] = 'wrong link (key)' if e.code == 403 else f'main PC said {e.code}'
                except (OSError, ValueError, KeyError, TypeError) as e:
                    sync_state['error'] = "can't reach main PC" if isinstance(e, OSError) else 'bad answer'
            sync_wake.wait(60)
            sync_wake.clear()

    def set_sync_link(self):
        text = self.sync_edit.text().strip()
        m = re.match(r'(https?://[^/\s]+)/app/([\w-]+)', text)
        if text and not m:
            sync_state['error'] = 'paste the whole phone link'
            return
        self.set_setting('sync_url', m[1] if m else None)
        self.set_setting('sync_key', m[2] if m else None)
        sync_state.update(at=0, error=None)
        sync_wake.set()  # sync now, not in a minute

    def phone_url(self):
        if not self.money_server:
            return ''
        lan, tail = phone_addresses()
        host = (tail or lan or ['127.0.0.1'])[0]  # Tailscale works at home and away
        return f'http://{host}:{MONEY_PORT}/app/{self.settings["phone_key"]}'

    def refresh_money(self):
        t, cur = money_totals(), money_prefs['currency']
        days = {}
        with money_lock:
            for tx in money:
                i, o = days.get(d := dt.date.fromtimestamp(tx['ts']), (0.0, 0.0))
                days[d] = (i + tx['amount'], o) if tx['kind'] == 'in' else (i, o + tx['amount'])
        self.m_cal.set(days, t['avg_day'])
        self.m_month.set(self.m_cal.month.strftime('%B %Y'))
        for w, text in zip(self.m_budget, (f'{t["avg_day"]:,.0f} {cur}', f'{t["can_day"]:,.0f} {cur}',
                                           f'{t["days_left"]} day{"s" * (t["days_left"] != 1)}')):
            w.set(text)
        for w, v, sign in zip(self.m_totals, (t['today_in'], t['today_out'], t['cycle_in'], t['cycle_out']),
                              '+−+−'):
            w.set(f'{sign}{v:,.2f} {cur}')
        with money_lock:
            latest = money[-4:][::-1]
        for (when, note, amount), tx in zip(self.m_rows, latest + [None] * 4):
            when.set(time.strftime('%d %b %H:%M', time.localtime(tx['ts'])) if tx else '')
            note.set(' · '.join(filter(None, (tx.get('cat'), tx['note']))) or tx['source'] if tx else '')
            amount.set(f'{"+" if tx["kind"] == "in" else "−"}{tx["amount"]:,.2f} {cur}' if tx else '',
                       (GREEN if tx['kind'] == 'in' else RED) if tx else None)

    def refresh_claude(self, now):
        u = claude_usage
        if not u:
            self.u_reset.set('Reading Claude Code logs…')
            return
        self.u_ring.set(u['pct'])
        if u['window_end']:
            left = max(0, int(u['window_end'] - now))
            self.u_reset.set(f'Resets in {left // 3600} h {left % 3600 // 60:02d} min')
            self.u_window.set(f'This window ≈ ${u["window_cost"]:,.2f} of ${u["peak_cost"]:,.0f} biggest')
        else:
            self.u_reset.set('No 5-hour window running')
            self.u_window.set('It starts with your next Claude Code message')
        self.u_today.set(f'Today ≈ ${u["today_cost"]:,.2f}   ·   {u["today_tokens"] / 1e6:,.1f}M tokens')
        self.u_week.set(f'Last 7 days ≈ ${u["week_cost"]:,.2f}   ·   {u["week_tokens"] / 1e6:,.1f}M tokens')
        self.u_bars.set(u['days'])

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
                w, h = 780, h + 14 * len(self.lyrics.rows)
        elif self.tiny and not self.alert:
            w, h = TINY
            return QRectF((self.width() - w) / 2, 2, w, h)
        else:
            busy = self.alert or media.get('playing') or self.timer_end
            w = self.flash[3] if self.flash else 320 if busy else 290 if self.settings['fox'] else 190  # idle: date + fox
            h = COMPACT_H
        return QRectF((self.width() - w) / 2, TOP, w, h)

    def morph(self):
        t = self.target()
        if t == self.goal:
            return
        self.goal = t
        if self.expanded:
            self.full.setGeometry(t.toRect())
            self.set_shape(self.shape)  # hide the new page before the first frame shows
        self.anim.stop()
        self.anim.setStartValue(self.shape)
        self.anim.setEndValue(t)
        grow = t.height() >= self.shape.height()
        self.anim.setEasingCurve(QEasingCurve.OutBack if grow else QEasingCurve.OutCubic)
        self.anim.setDuration(550 if grow else 380)
        if t.height() > COMPACT_H + 1:
            self.fit_window(tall=True)  # room to open before the spring starts
        self.anim.start()

    def fit_window(self, tall=False):
        """Closed island: the window is only as tall as the pill. Windows redraws the whole transparent window on
        every frame, so the fox and the sound bars cost a fraction of what an 820 x 430 window would.
        Only the height changes, so nothing moves sideways."""
        h = WIN[1] if tall or self.shape.height() > COMPACT_H + 1 else int(TOP + COMPACT_H + 8)
        if self.height() != h:
            self.resize(self.width(), h)

    def set_shape(self, r):
        self.shape = r
        k = max(0.0, min(1.0, (r.height() - COMPACT_H) / (160 - COMPACT_H)))  # 0 compact .. 1 expanded
        self.compact.setGeometry(r.toRect())
        self.c_fx.setOpacity(max(0.0, 1 - k * 4))
        # a new page is laid out at its final size: it fades in once the box has nearly grown, so no text shows
        # outside the box. No setMask on the page: it kept the calendar from painting on the layered window.
        run = self.anim.state() == QVariantAnimation.Running
        fade = max(0.0, min(1.0, (self.anim.currentTime() / self.anim.duration() - 0.4) / 0.4)) if run else 1.0
        shown = max(0.0, k * 2 - 1) * fade if self.expanded else 0
        self.compact.setVisible(k < 0.25 and r.height() > COMPACT_H - 8)  # hidden in the tiny pill
        self.full.setVisible(self.expanded and k > 0.5)
        self.veil.cover(r, 1 - shown if self.full.isVisible() else 0)
        # no setMask: the window is layered, so its transparent pixels already let clicks through, and changing
        # the window region left the old pixels behind as a square black box until the next full repaint
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(BG))
        r = min(self.shape.height() / 2, 34)
        p.drawRoundedRect(self.shape, r, r)
        if self.drop_hover:  # files are being dragged over: outline says "drop here"
            p.setPen(QPen(QColor(SAKURA), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(self.shape.adjusted(1, 1, -1, -1), r - 1, r - 1)

    # --- shelf: drop files anywhere on the island ---
    @staticmethod
    def local_files(e):
        return [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()] if e.mimeData().hasUrls() else []

    def dragEnterEvent(self, e):
        if self.local_files(e) and not self.shelf.dragging:  # not our own tiles being dragged out
            e.acceptProposedAction()
            self.drop_hover, self.expanded, self.tab = True, True, 'shelf'
            self.tick()

    def dragMoveEvent(self, e):
        if self.drop_hover:
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.drop_hover = False
        self.update()
        QTimer.singleShot(350, self.maybe_collapse)

    def dropEvent(self, e):
        self.drop_hover = False
        self.shelf.add(self.local_files(e))
        e.acceptProposedAction()
        self.update()

    def save_shelf(self):
        SHELF_FILE.write_text(json.dumps(self.shelf.paths()), 'utf-8')
        n = self.shelf.count()
        self.shelf_hint.set(f'{n} file{"s" * (n != 1)} · drag out to use, double-click to open' if n else
                            'Drop files on the island to keep them here')

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
        if (not inside and not typing and not self.alert and not self.shelf.dragging and not self.drop_hover
                and self.tab != 'guide'):  # the guide stays open until Skip or Done
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
        if money_last['at']:  # a new transaction from the iPhone, not shown yet
            tx = money_last['tx']
            self.flash = (f'{"+" if tx["kind"] == "in" else "−"}{tx["amount"]:,.2f} {money_prefs["currency"]}  ·  '
                          f'{tx["note"] or tx["source"]}', GREEN if tx['kind'] == 'in' else RED, money_last['at'] + 6, 320)
            money_last['at'] = 0
            self.refresh_money()
        end = claude_usage.get('window_end')
        if self.claude_end and now >= self.claude_end:  # the 5-hour Claude window just ran out: a fresh one is ready
            self.flash = ('Claude usage window reset', SAKURA, now + 4, 260)
            self.claude_end = None
        elif end and end > now:
            self.claude_end = end
        if self.flash and now > self.flash[2]:
            self.flash = None

        if media.get('art_key') != self.art_key:
            self.art_key = media.get('art_key')
            pix = QPixmap()
            if media.get('art'):
                pix.loadFromData(media['art'])
            pix = None if pix.isNull() else pix
            color = tint(pix) if pix else QColor(SAKURA)
            for w in (self.c_art, self.f_art):
                w.set(pix)
            self.c_wave.color = self.f_wave.color = self.lyrics.color = color
            self.f_lyric.set(self.f_lyric.text, color)

        title, playing, lyric = media.get('title', ''), bool(media.get('playing')), current_lyric()
        left = int(self.timer_end - now) if self.timer_end else None
        timer = mmss(left) if left is not None else ''

        # compact
        self.c_art.setVisible(playing and not self.alert and not self.flash)  # paused: clock alone, centered
        self.fox.set_active(self.settings['fox'] and not (self.expanded or self.tiny or self.alert or self.flash
                                                           or playing or timer or self.anim.state() == QVariantAnimation.Running))
        self.c_text.backdrop = self.fox.isVisible()  # the fox runs behind the clock, like through a tunnel
        self.c_wave.set_on(playing)
        self.f_wave.set_on(playing)
        self.c_wave.setVisible(playing and not timer)
        self.c_timer.setVisible(bool(timer))
        self.c_timer.set(timer)
        if self.alert:
            self.c_text.set(self.alert, ORANGE)
        elif self.flash:
            self.c_text.set(self.flash[0], self.flash[1])
        elif lyric and self.settings['compact_lyrics']:
            self.c_text.set(lyric, self.c_wave.color)
        else:
            self.c_text.set(title if playing else time.strftime('%a %d/%m   %H:%M'), FG)

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
        self.f_title.set(title or 'Nothing playing')
        self.f_artist.set(media.get('artist', ''))
        self.f_lyric.set(lyric)
        if page == 'claude':
            self.refresh_claude(now)
        if page in ('money', 'settings', 'guide') and now - self.money_at > 2:  # totals and pickers change slowly
            self.money_at = now
            self.refresh_money() if page == 'money' else self.sync_money_widgets()
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
                b.setStyleSheet(ICON_STYLE.format(color=FG if t == page else FAINT))
            if page in TABS:
                self.tab_bar.select(TABS.index(page))
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
    when = dt.datetime(2026, 10, 26, 14, 5).timestamp()
    sms_in = parse_tx('26/10/69 14:05 บชX1234X รับโอนจากX5678X 500.00บ คงเหลือ 12,345.67บ', 'sms')
    assert sms_in == {'ts': when, 'amount': 500.0, 'kind': 'in', 'dated': True}, sms_in
    sms_out = parse_tx('26/10/69 18:30 บชX1234X โอนเงิน 1,250.00บ คงเหลือ 11,095.67บ', 'sms')
    assert sms_out['kind'] == 'out' and sms_out['amount'] == 1250.0
    mail = parse_tx('Transaction Date: 26/10/2026 09:15 Transaction: Transfer to X5678 Amount (THB): 2,000.00 '
                    'Fee (THB): 0.00 Available Balance (THB): 9,095.67', 'email')
    assert mail['kind'] == 'out' and mail['amount'] == 2000.0, mail
    slip = parse_tx('โอนเงินสำเร็จ\n26 ต.ค. 69 14:05 น.\nนาย ก\nจำนวน:\n350.00 บาท\nค่าธรรมเนียม:\n0.00 บาท', 'slip')
    assert slip == {'ts': when, 'amount': 350.0, 'kind': 'out', 'dated': True}, slip
    assert parse_tx('สวัสดีครับ', 'sms') is None
    ocr_en = parse_tx('27 n.n. 69 09:41 u.\nunti\nxxx-x-xl 234-x\n<-nnu:\n1,280.50\n0.00', 'slip')  # no Thai OCR
    assert ocr_en['amount'] == 1280.5 and not ocr_en['dated'], ocr_en
    assert parse_tx('จ ำ น ว น : 99.00 บ า ท', 'slip')['amount'] == 99.0  # Thai OCR's spaced-out letters
    global MONEY_FILE, DELETED_FILE
    saved_file, MONEY_FILE = MONEY_FILE, pathlib.Path(os.environ.get('TEMP', '.')) / 'island_money_test.json'
    saved_deleted, DELETED_FILE = DELETED_FILE, MONEY_FILE.with_name('island_deleted_test.json')
    money.clear()
    a = add_tx(sms_in, 'sms')
    b = add_tx({**sms_in, 'ts': when + 60}, 'slip')  # same transfer reported twice: merged
    assert a is b and a['source'] == 'sms+slip' and len(money) == 1
    money.clear()
    d = dt.datetime
    assert pay_cycle(d(2026, 10, 20, 12)) == (d(2026, 9, 26), d(2026, 10, 26))  # no salary yet: the 26th
    assert pay_cycle(d(2026, 10, 27, 9)) == (d(2026, 10, 26), d(2026, 11, 26))
    money.append({'id': 's', 'ts': d(2026, 10, 28, 10).timestamp(), 'amount': 30000.0, 'kind': 'in',
                  'cat': '💼 Salary', 'note': '', 'source': 'manual'})
    assert pay_cycle(d(2026, 11, 2)) == (d(2026, 10, 28), d(2026, 11, 26))  # paid on the 28th: cycle from then
    assert pay_cycle(d(2026, 11, 27, 9)) == (d(2026, 10, 28), d(2026, 11, 28))  # pay late: payday tomorrow, not past
    saved_money, money[:] = money[:], []
    money_prefs['payday'] = 31  # set to the 31st: short months use their last day
    assert pay_cycle(d(2027, 2, 10)) == (d(2027, 1, 31), d(2027, 2, 28))
    assert pay_cycle(d(2026, 12, 1)) == (d(2026, 11, 30), d(2026, 12, 31))
    money_prefs['payday'], money[:] = 26, saved_money
    usage = ClaudeUsage()
    for bad in (b'"text"', b'[1]', b'{"message": {"usage": {"output_tokens": 5}}, "timestamp": "not a time"}'):
        usage.add(bad)  # ignored, no error
    assert not usage.replies
    money.append({'id': 'f', 'ts': d(2026, 11, 2, 12).timestamp(), 'amount': 400.0, 'kind': 'out', 'cat': '',
                  'note': '', 'source': 'manual'})
    t = money_totals(d(2026, 11, 2, 18).timestamp())
    assert t['cycle_out'] == 400 and t['days_left'] == 24 and round(t['avg_day']) == 67  # 400 over 6 days
    assert round(t['can_day'], 2) == round(29600 / 24, 2)
    money.clear()
    # sync with another PC: newer edit wins, older loses, a delete always wins and stays deleted
    money.extend([{'id': 'x', 'ts': 1.0, 'amount': 5.0, 'kind': 'out', 'mt': 10},
                  {'id': 'y', 'ts': 2.0, 'amount': 7.0, 'kind': 'out', 'mt': 10, 'raw': 'sms text'}])
    assert merge_money([{'id': 'x', 'ts': 1.0, 'amount': 9.0, 'kind': 'out', 'mt': 5},  # older: ignored
                        {'id': 'y', 'ts': 2.0, 'amount': 8.0, 'kind': 'out', 'mt': 20},  # newer: taken, keeps raw
                        {'id': 'z', 'ts': 3.0, 'amount': 1.0, 'kind': 'in', 'mt': 1}], {'x': 30})
    assert [(t['id'], t['amount']) for t in money] == [('y', 8.0), ('z', 1.0)] and money[0]['raw'] == 'sms text'
    assert not merge_money([{'id': 'x', 'ts': 1.0, 'amount': 5.0, 'kind': 'out', 'mt': 99}], {})  # deleted stays
    assert json.loads(DELETED_FILE.read_text('utf-8')) == {'x': 30}
    money.clear()
    money_deleted.clear()
    MONEY_FILE.unlink(missing_ok=True)
    DELETED_FILE.unlink(missing_ok=True)
    MONEY_FILE, DELETED_FILE = saved_file, saved_deleted
    u = {'input_tokens': 1_000_000, 'output_tokens': 1_000_000, 'cache_read_input_tokens': 1_000_000,
         'cache_creation_input_tokens': 2_000_000, 'cache_creation': {'ephemeral_1h_input_tokens': 1_000_000,
                                                                      'ephemeral_5m_input_tokens': 1_000_000}}
    assert message_cost('claude-opus-5-5', u) == (5_000_000, 4 + 20 + 0.20 + 4 * 1.25 + 4 * 2)  # 37.2 $
    assert claude_price('claude-opus-5') == (5, 25, 0.50) and claude_price('claude-opus-5-5[1m]') == (4, 20, 0.20)
    assert message_cost('<synthetic>', u)[1] == 0
    usage = ClaudeUsage(pathlib.Path(os.environ.get('TEMP', '.')) / 'no-such-claude-dir')
    base = dt.datetime(2026, 10, 27, 9, 20).timestamp()
    usage.replies = {'a': (base - 6 * 3600, 10, 4.0), 'b': (base - 5.5 * 3600, 10, 6.0),  # an earlier window: $10
                     'c': (base, 10, 2.0), 'd': (base + 600, 10, 3.0)}  # the current one: started 09:00, $5
    s = usage.summary(base + 900)
    assert s['window_end'] == dt.datetime(2026, 10, 27, 14, 0).timestamp() and s['pct'] == 0.5, s
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['test']:
        selftest()
    elif single_instance():
        _s = load_settings()  # before the media thread loads the first song's lyrics
        prefs.update(lang=_s['translation'], romaji=bool(_s['pronunciation']))
        threading.Thread(target=lambda: asyncio.run(media_loop()), daemon=True).start()
        threading.Thread(target=scan_processes, daemon=True).start()
        threading.Thread(target=watch_claude, daemon=True).start()
        QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))  # calendar and dates in English on any Windows
        app = QApplication(sys.argv)
        app.setStyleSheet(STYLE)
        island = Island()
        island.show()
        app.exec()

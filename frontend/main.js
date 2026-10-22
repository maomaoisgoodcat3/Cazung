// Cazung desktop shell: transparent always-on-top window that lives on the taskbar.
const { app, BrowserWindow, ipcMain, screen, Menu, Tray, nativeImage, shell, globalShortcut } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const { spawn } = require('child_process');
const Sprite = require('./renderer/sprite.js');

const ROOT = path.resolve(__dirname, '..');
const DATA = process.env.CAZUNG_DATA ? path.resolve(ROOT, 'backend', process.env.CAZUNG_DATA) : path.join(ROOT, 'data');
const CONFIG = process.env.CAZUNG_CONFIG ? path.resolve(ROOT, 'backend', process.env.CAZUNG_CONFIG) : path.join(ROOT, 'config.toml');
const WIN_W = 400, WIN_H = 640;

function readPort() {
  try {
    const t = fs.readFileSync(CONFIG, 'utf8');
    const m = t.match(/\[server\][^[]*?\bport\s*=\s*(\d+)/);
    if (m) return Number(m[1]);
  } catch (e) { /* default */ }
  return 8765;
}
const BASE = `http://127.0.0.1:${readPort()}`;

let win = null, tray = null, backendProc = null;
let petRect = { left: WIN_W - 184, right: WIN_W - 24 };
let dragTimer = null, fallTimer = null, dragOffset = null;

if (!app.requestSingleInstanceLock()) { app.quit(); }

// ------------------------------------------------------------- backend
const sleep = ms => new Promise(r => setTimeout(r, ms));

function ping() {
  return new Promise(resolve => {
    const req = http.get(BASE + '/api/health', res => { res.resume(); resolve(res.statusCode === 200); });
    req.on('error', () => resolve(false));
    req.setTimeout(1500, () => { req.destroy(); resolve(false); });
  });
}

function startBackend() {
  const venvPy = process.platform === 'win32'
    ? path.join(ROOT, 'backend', '.venv', 'Scripts', 'python.exe')
    : path.join(ROOT, 'backend', '.venv', 'bin', 'python');
  const exe = fs.existsSync(venvPy) ? venvPy : (process.platform === 'win32' ? 'python' : 'python3');
  fs.mkdirSync(DATA, { recursive: true });
  const log = fs.openSync(path.join(DATA, 'backend-stdout.log'), 'a');
  backendProc = spawn(exe, ['-m', 'cazung'], {
    cwd: path.join(ROOT, 'backend'), windowsHide: true, stdio: ['ignore', log, log],
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
  });
  backendProc.on('exit', () => { backendProc = null; });
}

let ensuring = null;
async function ensureBackend() {
  if (await ping()) return true;
  if (!ensuring) {
    ensuring = (async () => {
      if (!backendProc) startBackend();
      for (let i = 0; i < 80; i++) { await sleep(500); if (await ping()) return true; }
      return false;
    })().finally(() => { ensuring = null; });
  }
  return ensuring;
}

ipcMain.handle('get-backend', async () => {
  const ok = await ensureBackend();
  let token = '';
  try { token = fs.readFileSync(path.join(DATA, '.token'), 'utf8').trim(); } catch (e) { /* not yet */ }
  return { ok, url: BASE, token };
});

// -------------------------------------------------------------- window
const workArea = () => screen.getDisplayMatching(win.getBounds()).workArea;
const groundY = wa => wa.y + wa.height - WIN_H;

function clampX(x, wa) {
  const minX = wa.x - petRect.left;
  const maxX = wa.x + wa.width - petRect.right;
  return Math.max(minX, Math.min(maxX, x));
}

function place(x, y) {
  win.setBounds({ x: Math.round(x), y: Math.round(y), width: WIN_W, height: WIN_H });
}

function createWindow() {
  const wa = screen.getPrimaryDisplay().workArea;
  win = new BrowserWindow({
    width: WIN_W, height: WIN_H,
    x: wa.x + wa.width - WIN_W - 30, y: groundY(wa),
    transparent: true, frame: false, resizable: false, movable: true,
    alwaysOnTop: true, skipTaskbar: true, hasShadow: false,
    maximizable: false, minimizable: false, fullscreenable: false,
    backgroundColor: '#00000000', title: 'Cazung', icon: trayIcon(64),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false,
    },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setIgnoreMouseEvents(true, { forward: true });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e, url) => {
    e.preventDefault();
    if (/^https?:/.test(url)) shell.openExternal(url);
  });
}

ipcMain.on('set-ignore-mouse', (_e, ignore) => {
  if (win && !dragTimer) win.setIgnoreMouseEvents(ignore, { forward: true });
});

ipcMain.on('pet-rect', (_e, r) => { if (r && Number.isFinite(r.left) && Number.isFinite(r.right)) petRect = r; });
ipcMain.on('focus', () => { if (win) win.focus(); });

ipcMain.handle('move-by', (_e, dx) => {
  if (!win || dragTimer || fallTimer) return null;
  const wa = workArea();
  const [x] = win.getPosition();
  const nx = clampX(x + dx, wa);
  place(nx, groundY(wa));
  return { atLeft: nx <= wa.x - petRect.left, atRight: nx >= wa.x + wa.width - petRect.right };
});

ipcMain.handle('ensure-visible', async () => {
  if (!win) return;
  const wa = workArea();
  const [x, y] = win.getPosition();
  const tx = Math.max(wa.x, Math.min(wa.x + wa.width - WIN_W, x));
  for (let i = 1; i <= 8; i++) { place(x + (tx - x) * i / 8, y); await sleep(16); }
});

ipcMain.on('drag-start', () => {
  if (!win) return;
  clearTimeout(fallTimer); fallTimer = null;
  win.setIgnoreMouseEvents(false);
  const c = screen.getCursorScreenPoint();
  const [x, y] = win.getPosition();
  dragOffset = { x: c.x - x, y: c.y - y };
  clearInterval(dragTimer);
  dragTimer = setInterval(() => {
    const p = screen.getCursorScreenPoint();
    place(p.x - dragOffset.x, p.y - dragOffset.y);
  }, 16);
});

ipcMain.on('drag-end', () => {
  clearInterval(dragTimer); dragTimer = null;
  fall();
});

function fall() {
  let v = 0;
  win.webContents.send('falling');
  const tick = () => {
    const wa = workArea();
    const [x, y0] = win.getPosition();
    v += 2.2;
    const ground = groundY(wa);
    const y = y0 < ground ? Math.min(ground, y0 + v) : ground;
    place(clampX(x, wa), y);
    if (y >= ground) { fallTimer = null; win.webContents.send('landed'); return; }
    fallTimer = setTimeout(tick, 16);
  };
  tick();
}

ipcMain.on('context-menu', (_e, state = {}) => {
  const send = (ch, ...a) => win && win.webContents.send(ch, ...a);
  Menu.buildFromTemplate([
    { label: 'Mở / đóng chat   (Ctrl+Alt+C)', click: () => send('toggle-chat') },
    { label: 'Cuộc trò chuyện mới', click: () => send('menu', 'new') },
    { label: 'Xem bộ nhớ của Cazung', click: () => send('menu', 'memory') },
    state.sleeping ? { label: 'Đánh thức', click: () => send('menu', 'wake') } : { label: 'Đi ngủ đi', click: () => send('menu', 'sleep') },
    { label: 'Tự đi dạo', type: 'checkbox', checked: !!state.wander, click: i => send('menu', i.checked ? 'wander-on' : 'wander-off') },
    { type: 'separator' },
    { label: 'Mở thư mục làm việc (workspace)', click: () => { const p = path.join(DATA, 'workspace'); fs.mkdirSync(p, { recursive: true }); shell.openPath(p); } },
    { label: 'Sửa cấu hình (config.toml)', click: () => shell.openPath(CONFIG) },
    { label: 'Mở trên trình duyệt', click: () => shell.openExternal(BASE) },
    { label: 'Khởi động lại Cazung', click: () => { restartBackend(); app.relaunch(); app.quit(); } },
    { type: 'separator' },
    { label: 'Tạm biệt Cazung (thoát)', click: () => app.quit() },
  ]).popup({ window: win });
});

function restartBackend() {
  if (backendProc) backendProc.kill();
}

// --------------------------------------------------------------- tray
function trayIcon(size) {
  const g = Sprite.compose({ eyes: 'happy', mouth: 'grin', blush: true });
  const head = g.slice(0, 24).map(r => r.slice(4, 28)); // crop the head (24x24)
  const scale = Math.max(1, Math.floor(size / 24));
  const { width, height, data } = Sprite.toRGBA(head, scale);
  const bgra = Buffer.alloc(data.length);
  for (let i = 0; i < data.length; i += 4) {
    bgra[i] = data[i + 2]; bgra[i + 1] = data[i + 1]; bgra[i + 2] = data[i]; bgra[i + 3] = data[i + 3];
  }
  return nativeImage.createFromBitmap(bgra, { width, height });
}

function createTray() {
  tray = new Tray(trayIcon(32).resize({ width: 16, height: 16 }));
  tray.setToolTip('Cazung');
  tray.on('click', () => win && win.webContents.send('toggle-chat'));
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Mở / đóng chat', click: () => win.webContents.send('toggle-chat') },
    { label: 'Gọi Cazung về góc phải', click: () => { const wa = screen.getPrimaryDisplay().workArea; place(wa.x + wa.width - WIN_W - 30, groundY(wa)); } },
    { label: 'Mở trên trình duyệt', click: () => shell.openExternal(BASE) },
    { type: 'separator' },
    { label: 'Thoát', click: () => app.quit() },
  ]));
}

// ----------------------------------------------------------- lifecycle
app.whenReady().then(() => {
  if (process.platform === 'win32') app.setAppUserModelId('Cazung');
  createWindow();
  createTray();
  ensureBackend();
  globalShortcut.register('CommandOrControl+Alt+C', () => {
    if (!win) return;
    win.webContents.send('toggle-chat');
    win.focus();
  });
  screen.on('display-metrics-changed', () => { if (win && !dragTimer) fall(); });
  if (process.env.CAZUNG_SNAPSHOT) devSnapshot(process.env.CAZUNG_SNAPSHOT);
});

// dev helper: CAZUNG_SNAPSHOT=out.png -> open the chat, capture the window, quit
async function devSnapshot(out) {
  await new Promise(r => win.webContents.once('did-finish-load', r));
  await sleep(6000);
  await win.webContents.executeJavaScript(`(() => { const c = document.getElementById('pet');
    for (const t of ['pointerdown', 'pointerup']) c.dispatchEvent(new PointerEvent(t, { button: 0, pointerId: 1 })); })()`);
  await sleep(2500);
  if (process.env.CAZUNG_DEMO) { // replay a fake streamed turn through the real chat UI
    await win.webContents.executeJavaScript(`(() => {
      const c = window.__chat; c.list.innerHTML = '';
      c._append('user', 'Viết giúp mình hàm đảo chuỗi bằng Python');
      c._handle({type: 'start'});
      const think = 'Chủ nhân muốn một hàm đảo chuỗi. Dùng slicing s[::-1] là gọn nhất...';
      [...think].forEach((ch, i) => setTimeout(() => c._handle({type: 'thinking', text: ch}), 200 + i * 15));
      setTimeout(() => c._handle({type: 'tool_call', id: 't1', name: 'write_file', args: {path: 'reverse.py'}}), 1500);
      setTimeout(() => c._handle({type: 'tool_result', id: 't1', name: 'write_file', ok: true, preview: 'Đã ghi 42 ký tự'}), 1800);
      const ans = 'Xong rồi nè~ Hàm của chủ nhân đây:\\n\\n\`\`\`python\\ndef reverse(s):\\n    return s[::-1]\\n\`\`\`\\n\\nEm đã lưu vào **reverse.py** luôn!';
      ans.match(/.{1,3}/gs).forEach((p, i) => setTimeout(() => c._handle({type: 'token', text: p}), 2000 + i * 25));
      setTimeout(() => c._handle({type: 'done', message_id: 1, text: ans, emotion: 'happy'}), 2000 + ans.length * 9);
    })()`);
    await sleep(1200);
    fs.writeFileSync(out.replace(/\.png$/, '-thinking.png'), (await win.webContents.capturePage()).toPNG());
    await sleep(1800);
    fs.writeFileSync(out.replace(/\.png$/, '-typing.png'), (await win.webContents.capturePage()).toPNG());
    await sleep(4000);
  }
  fs.writeFileSync(out, (await win.webContents.capturePage()).toPNG());
  await win.webContents.executeJavaScript(`document.getElementById('btn-memory').click();
    document.querySelector('[data-tab=stats]').click();`);
  await sleep(1500);
  fs.writeFileSync(out.replace(/\.png$/, '-memory.png'), (await win.webContents.capturePage()).toPNG());
  app.quit();
}

app.on('second-instance', () => { if (win) win.webContents.send('toggle-chat'); });
app.on('window-all-closed', () => app.quit());
app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (backendProc) backendProc.kill();
});

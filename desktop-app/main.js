// DJ Scratch Desktop v1.0.0 — clean Electron shell (ground-up rebuild).
// Responsibilities: window, updater, Discord RPC (now-playing aware), auth deep-link.
const { app, BrowserWindow, shell, dialog } = require('electron');
const path = require('path');
const http = require('http');
const url = require('url');

const serve = require('electron-serve');
const electronServe = serve.default || serve;
const loadURL = electronServe({ directory: 'build' });

const AUTH_PORT = 43210;
const DISCORD_CLIENT_ID = '1521582398188290049';

let mainWindow = null;
let rpc = null;
let rpcTrackLabel = '';

function createWindow() {
  // Linux has no titleBarOverlay: use a normal frame so window controls exist.
  const isLinux = process.platform === 'linux';
  mainWindow = new BrowserWindow({
    width: 1240,
    height: 840,
    minWidth: 980,
    minHeight: 640,
    ...(isLinux
      ? { frame: true }
      : {
          titleBarStyle: 'hidden',
          titleBarOverlay: { color: '#09090b', symbolColor: '#ffffff', height: 40 },
        }),
    backgroundColor: '#09090b',
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url: target }) => {
    shell.openExternal(target);
    return { action: 'deny' };
  });

  loadURL(mainWindow);

  mainWindow.webContents.on('did-finish-load', () => {
    mainWindow.webContents.insertCSS(
      '.electron-drag{position:fixed;top:0;left:0;right:140px;height:40px;-webkit-app-region:drag;z-index:99999;pointer-events:none}'
    );
    mainWindow.webContents
      .executeJavaScript(
        "if(!document.getElementById('electron-drag')){const d=document.createElement('div');d.id='electron-drag';d.className='electron-drag';document.body.appendChild(d);}"
      )
      .catch(() => {});
  });
}

function setupUpdater() {
  try {
    const { autoUpdater } = require('electron-updater');
    autoUpdater.logger = console;
    autoUpdater.autoDownload = true;

    autoUpdater.on('update-available', (info) => {
      mainWindow?.webContents.send('djscratch:update-available', info || {});
    });
    autoUpdater.on('download-progress', (p) => {
      mainWindow?.webContents.send('djscratch:update-progress', Math.floor(p.percent || 0));
      try {
        mainWindow?.setProgressBar((p.percent || 0) / 100);
      } catch {}
    });
    autoUpdater.on('update-downloaded', (info) => {
      mainWindow?.webContents.send('djscratch:update-downloaded', info || {});
      try {
        mainWindow?.setProgressBar(-1);
      } catch {}
      dialog
        .showMessageBox({
          type: 'info',
          title: 'Update ready',
          message: 'A new version of DJ Scratch downloaded. Restart now to install?',
          buttons: ['Restart now', 'Later'],
        })
        .then((r) => {
          if (r.response === 0) autoUpdater.quitAndInstall();
        });
    });
    autoUpdater.on('update-not-available', (info) => {
      mainWindow?.webContents.send('djscratch:update-not-available', info || {});
    });
    autoUpdater.on('error', (err) => {
      mainWindow?.webContents.send('djscratch:update-error', String((err && err.message) || err));
    });

    // Renderer-triggered manual check (Settings → Check for updates).
    try {
      const { ipcMain } = require('electron');
      ipcMain.on('djscratch:check-updates', () => {
        autoUpdater.checkForUpdatesAndNotify();
      });
    } catch {}

    autoUpdater.checkForUpdatesAndNotify();
    setInterval(() => autoUpdater.checkForUpdatesAndNotify(), 6 * 60 * 60 * 1000);
  } catch (err) {
    console.error('updater unavailable', err);
  }
}

function deliverTokenToRenderer(token) {
  if (!mainWindow || !token) return false;
  const safe = String(token).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  mainWindow.webContents
    .executeJavaScript(`localStorage.setItem('discord_jwt','${safe}');location.reload();`)
    .catch(() => {});
  try {
    mainWindow.show();
    mainWindow.focus();
  } catch {}
  return true;
}

function setupAuthServer() {
  const server = http.createServer((req, res) => {
    const parsed = url.parse(req.url || '', true);
    if (parsed.pathname === '/auth') {
      const token = parsed.query.token;
      if (typeof token === 'string' && token.length > 10) {
        deliverTokenToRenderer(token);
        res.writeHead(200, { 'Content-Type': 'text/html' });
        res.end(successHtml());
      } else {
        res.writeHead(400);
        res.end('Missing token');
      }
      return;
    }
    res.writeHead(404);
    res.end();
  });
  server.listen(AUTH_PORT, '127.0.0.1', () => console.log(`auth server on ${AUTH_PORT}`));
}

function successHtml() {
  return `<!doctype html><html><head><meta charset="utf-8"><title>Logged in</title><style>body{background:#09090b;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}.card{background:#18181b;border:1px solid rgba(255,255,255,.1);border-radius:20px;padding:36px 44px;text-align:center;max-width:440px}h1{margin:0 0 10px}p{color:#a1a1aa}.x{color:#818cf8}</style></head><body><div class="card"><h1>Logged <span class="x">in!</span></h1><p>Connected to DJ Scratch desktop. You can close this tab.</p><script>setTimeout(function(){window.close()},1500)</script></div></body></html>`;
}

function setupRpc() {
  try {
    const DiscordRPC = require('discord-rpc');
    DiscordRPC.register(DISCORD_CLIENT_ID);
    rpc = new DiscordRPC.Client({ transport: 'ipc' });
    const started = new Date();
    const push = () => {
      if (!rpc) return;
      try {
        rpc.setActivity({
          details: rpcTrackLabel || 'Browsing stats',
          state: 'On DJ Scratch',
          startTimestamp: started,
          largeImageKey: 'logo',
          largeImageText: 'DJ Scratch',
          instance: false,
        });
      } catch {}
    };
    rpc.on('ready', () => {
      push();
      setInterval(push, 15000);
    });
    rpc.login({ clientId: DISCORD_CLIENT_ID }).catch((e) => console.error('rpc login', e));

    const { ipcMain } = require('electron');
    ipcMain.on('djscratch:now-playing', (_evt, label) => {
      rpcTrackLabel = typeof label === 'string' ? label.slice(0, 120) : '';
      push();
    });
  } catch (err) {
    console.error('rpc unavailable', err);
  }
}

// Custom protocol djscratch://auth?token=... (fallback to localhost server above)
function setupProtocol() {
  const PROTOCOL = 'djscratch';
  try {
    if (process.defaultApp) {
      if (process.argv.length >= 2) app.setAsDefaultProtocolClient(PROTOCOL, process.execPath, [path.resolve(process.argv[1])]);
    } else {
      app.setAsDefaultProtocolClient(PROTOCOL);
    }
  } catch {}
  const handleArgv = (argv) => {
    const hit = (argv || []).find((a) => String(a).startsWith(`${PROTOCOL}://`));
    if (!hit) return;
    try {
      const u = new URL(hit);
      const token = u.searchParams.get('token');
      if (token) deliverTokenToRenderer(token);
    } catch {}
  };
  app.on('second-instance', (_e, argv) => handleArgv(argv));
  app.on('open-url', (e, u) => {
    e.preventDefault();
    try {
      const parsed = new URL(u);
      const token = parsed.searchParams.get('token');
      if (token) deliverTokenToRenderer(token);
    } catch {}
  });
  handleArgv(process.argv);
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  setupProtocol();
  app.whenReady().then(() => {
    createWindow();
    setupUpdater();
    setupAuthServer();
    setupRpc();
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });
  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
}

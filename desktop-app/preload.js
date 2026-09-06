const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('djscratch', {
  onUpdateAvailable: (cb) => ipcRenderer.on('djscratch:update-available', (_e, v) => cb(v)),
  onUpdateProgress: (cb) => ipcRenderer.on('djscratch:update-progress', (_e, v) => cb(v)),
  onUpdateDownloaded: (cb) => ipcRenderer.on('djscratch:update-downloaded', (_e, v) => cb(v)),
  onUpdateError: (cb) => ipcRenderer.on('djscratch:update-error', (_e, v) => cb(v)),
  onUpdateNotAvailable: (cb) => ipcRenderer.on('djscratch:update-not-available', (_e, v) => cb(v)),
  checkForUpdates: () => ipcRenderer.send('djscratch:check-updates'),
  reportNowPlaying: (label) => ipcRenderer.send('djscratch:now-playing', label || ''),
});

// Back-compat for older renderer bundles calling window.electronAPI
contextBridge.exposeInMainWorld('electronAPI', {
  onUpdateAvailable: (cb) => ipcRenderer.on('djscratch:update-available', cb),
  onUpdateProgress: (cb) => ipcRenderer.on('djscratch:update-progress', (_e, v) => cb(_e, v)),
  onUpdateDownloaded: (cb) => ipcRenderer.on('djscratch:update-downloaded', cb),
  onUpdateError: (cb) => ipcRenderer.on('djscratch:update-error', (_e, v) => cb(_e, v)),
});

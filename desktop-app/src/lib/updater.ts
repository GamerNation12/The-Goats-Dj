// Typed access to the Electron auto-updater bridge (window.djscratch).
// In a plain browser (vite dev) the bridge is absent — every call no-ops.

interface UpdateBridge {
  onUpdateAvailable: (cb: (info: unknown) => void) => void;
  onUpdateProgress: (cb: (pct: number) => void) => void;
  onUpdateDownloaded: (cb: (info: unknown) => void) => void;
  onUpdateError: (cb: (msg: string) => void) => void;
  onUpdateNotAvailable: (cb: (info: unknown) => void) => void;
  checkForUpdates: () => void;
}

export function getBridge(): UpdateBridge | null {
  try {
    const w = window as unknown as { djscratch?: UpdateBridge };
    return w.djscratch || null;
  } catch {
    return null;
  }
}

type Handlers = {
  onAvailable?: () => void;
  onUpToDate?: () => void;
  onError?: (msg: string) => void;
};

let wired = false;
const current: Handlers = {};

function ensureWired() {
  const b = getBridge();
  if (!b || wired) return;
  wired = true;
  b.onUpdateAvailable(() => current.onAvailable?.());
  b.onUpdateNotAvailable(() => current.onUpToDate?.());
  b.onUpdateError((m) => current.onError?.(String(m)));
}

/** Trigger a manual update check; results arrive via the given callbacks. */
export function manualCheck(handlers: Handlers): void {
  const b = getBridge();
  if (!b) {
    handlers.onError?.('Updater is only available in the installed app.');
    return;
  }
  ensureWired();
  current.onAvailable = handlers.onAvailable;
  current.onUpToDate = handlers.onUpToDate;
  current.onError = handlers.onError;
  b.checkForUpdates();
}

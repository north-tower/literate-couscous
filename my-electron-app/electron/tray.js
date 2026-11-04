import { nativeImage, Tray, Menu, app } from 'electron';

/** 16×16 PNG (blue / green / red) as data URLs for tray states. */
const ICONS = {
  idle: nativeImage.createFromDataURL(
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAHklEQVQ4T2NkYGD4z0ABYBw1gGE0DBhGQ4NhNAwYBkYGBgAAGQABf5J0nQAAAABJRU5ErkJggg==',
  ),
  running: nativeImage.createFromDataURL(
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAIElEQVQ4T2NkYGD4z0ABYBw1gGE0DBhGQ4NhNAwYBkYGBgAAGgABf5J0nQAAAABJRU5ErkJggg==',
  ),
  error: nativeImage.createFromDataURL(
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAJElEQVQ4T2NkYGD4z0ABYBw1gGE0DBhGQ4NhNAwYBkYGBgAAHQABf5J0nQAAAABJRU5ErkJggg==',
  ),
};

/**
 * @param {() => import('electron').BrowserWindow | null} getMainWindow
 * @param {{ getStatus: () => object; onStatus: (fn: (s: object) => void) => () => void; isJobRunning: () => boolean }} agentRunner
 * @param {() => void} openSettings
 * @param {ReturnType<import('./config.js').getDesktopConfig>} config
 */
export function createSendlineTray(getMainWindow, agentRunner, openSettings, config) {
  /** @type {Tray | null} */
  let tray = null;

  function trayIconForStatus(status) {
    if (status.token_invalid || status.last_error || status.phase === 'error') return ICONS.error;
    if (status.phase === 'running' || status.phase === 'stopping') return ICONS.running;
    return ICONS.idle;
  }

  function buildMenu(status) {
    const jobLabel =
      status.job_id != null
        ? `Running job #${status.job_id}`
        : status.token_invalid
          ? 'Agent token rejected — link again'
          : status.connected
            ? 'Connected — idle'
            : 'No agent token';
    const running = agentRunner.isJobRunning();

    return Menu.buildFromTemplate([
      { label: jobLabel, enabled: false },
      { type: 'separator' },
      {
        label: 'Open Sendline',
        click: () => {
          const win = getMainWindow();
          if (win) {
            if (win.isMinimized()) win.restore();
            win.focus();
          }
        },
      },
      {
        label: 'Settings…',
        click: () => openSettings(),
      },
      { type: 'separator' },
      {
        label: running ? 'Quit (job running)' : 'Quit',
        enabled: !running,
        click: () => {
          if (running) return;
          app.quit();
        },
      },
    ]);
  }

  function refresh(status) {
    if (!tray) return;
    tray.setImage(trayIconForStatus(status));
    tray.setToolTip(
      status.job_id != null
        ? `Sendline — job #${status.job_id}`
        : status.token_invalid
          ? 'Sendline — agent token rejected'
          : status.connected
            ? 'Sendline — connected'
            : 'Sendline — configure agent token',
    );
    tray.setContextMenu(buildMenu(status));
  }

  tray = new Tray(ICONS.idle);
  tray.setToolTip('Sendline Desktop');
  tray.on('double-click', () => {
    const win = getMainWindow();
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  agentRunner.onStatus((status) => refresh(status));
  refresh(agentRunner.getStatus());

  return tray;
}

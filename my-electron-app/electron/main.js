import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';
import { app, BrowserWindow, ipcMain, Menu, shell } from 'electron';
import { mainWindowChromeOptions } from './window-chrome.js';
import { getDesktopConfig, readStoredApiUrl } from './config.js';
import { mainWindowWebPreferences } from './browser-prefs.js';
import { createAgentRunner } from './agent-runner.js';
import { createSendlineTray } from './tray.js';
import { openSettingsWindow, registerSettingsIpc } from './settings.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

dotenv.config({ path: path.join(app.getAppPath(), '.env') });

if (!app.isPackaged) {
  app.commandLine.appendSwitch('disable-http-cache');
}

/** @type {ReturnType<typeof getDesktopConfig> | null} */
let desktopConfig = null;

/** @type {BrowserWindow | null} */
let mainWindow = null;

/** @type {ReturnType<typeof createAgentRunner> | null} */
let agentRunner = null;

export function getConfig() {
  if (!desktopConfig) {
    desktopConfig = getDesktopConfig(app);
    const storedUrl = readStoredApiUrl(desktopConfig.paths);
    if (storedUrl) {
      desktopConfig = { ...desktopConfig, sendlineApiUrl: storedUrl };
    }
  }
  return desktopConfig;
}

export { mainWindowWebPreferences };

const MAIN_WINDOW_MIN_WIDTH = 800;
const MAIN_WINDOW_MIN_HEIGHT = 600;

/**
 * @param {string} targetUrl
 * @param {string} apiOrigin
 * @returns {boolean} true if opened externally (navigation should be blocked)
 */
function openExternalIfNotSendlineOrigin(targetUrl, apiOrigin) {
  let parsed;
  try {
    parsed = new URL(targetUrl);
  } catch {
    return false;
  }

  const externalSchemes = new Set(['http:', 'https:', 'mailto:', 'tel:']);
  if (!externalSchemes.has(parsed.protocol)) {
    return false;
  }

  if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
    if (parsed.origin === apiOrigin) {
      return false;
    }
  }

  shell.openExternal(targetUrl).catch((err) => {
    console.error('[sendline-desktop] openExternal failed:', err);
  });
  return true;
}

/**
 * @param {import('electron').WebContents} webContents
 * @param {string} apiOrigin
 */
function attachExternalLinkHandlers(webContents, apiOrigin) {
  webContents.setWindowOpenHandler(({ url }) => {
    if (openExternalIfNotSendlineOrigin(url, apiOrigin)) {
      return { action: 'deny' };
    }
    return { action: 'allow' };
  });

  webContents.on('will-navigate', (event, url) => {
    if (openExternalIfNotSendlineOrigin(url, apiOrigin)) {
      event.preventDefault();
    }
  });
}

/** Campaign UI + onboarding live on Flask; start at /login (redirects to / when session exists). */
function getMainWindowStartUrl(apiBase) {
  return `${apiBase.replace(/\/+$/, '')}/login`;
}

function createMainWindow() {
  const config = getConfig();
  const startUrl = getMainWindowStartUrl(config.sendlineApiUrl);
  const apiOrigin = new URL(config.sendlineApiUrl).origin;

  mainWindow = new BrowserWindow({
    ...mainWindowChromeOptions(app),
    minWidth: MAIN_WINDOW_MIN_WIDTH,
    minHeight: MAIN_WINDOW_MIN_HEIGHT,
    webPreferences: mainWindowWebPreferences,
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });

  attachExternalLinkHandlers(mainWindow.webContents, apiOrigin);

  if (!app.isPackaged) {
    console.log('[sendline-desktop] loading UI:', startUrl);
  }

  mainWindow.webContents.on('did-finish-load', () => {
    if (!app.isPackaged) {
      console.log('[sendline-desktop] loaded:', mainWindow?.webContents.getURL());
    }
  });

  mainWindow.loadURL(startUrl).catch((err) => {
    console.error('[sendline-desktop] loadURL failed:', err);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      mainWindow.focus();
    } else {
      createMainWindow();
    }
  });

  app.whenReady().then(() => {
    if (process.platform !== 'darwin') {
      Menu.setApplicationMenu(null);
    }

    const config = getConfig();
    if (process.argv.includes('--enable-logging') || !app.isPackaged) {
      console.log('[sendline-desktop] API URL:', config.sendlineApiUrl);
      console.log('[sendline-desktop] data root:', config.paths.root);
    }

    ipcMain.handle('sendline:getAgentStatus', () => agentRunner?.getStatus() ?? null);

    function afterAgentSettingsSaved() {
      desktopConfig = null;
      const next = getConfig();
      const win = mainWindow;
      if (win && !win.isDestroyed()) {
        try {
          const nextOrigin = new URL(next.sendlineApiUrl).origin;
          const current = win.webContents.getURL();
          const currentOrigin = current ? new URL(current).origin : null;
          if (currentOrigin !== nextOrigin) {
            const apiOrigin = nextOrigin;
            attachExternalLinkHandlers(win.webContents, apiOrigin);
            win.loadURL(next.sendlineApiUrl).catch((err) => {
              console.error('[sendline-desktop] reload after settings failed:', err);
            });
          }
        } catch (err) {
          console.error('[sendline-desktop] settings reload check failed:', err);
        }
      }
      agentRunner?.restart();
    }

    registerSettingsIpc(afterAgentSettingsSaved);

    agentRunner = createAgentRunner(app, config);
    agentRunner.onStatus((status) => {
      const win = mainWindow;
      if (win && !win.isDestroyed()) {
        win.webContents.send('sendline:agentStatus', status);
      }
    });
    agentRunner.start().catch((err) => {
      console.error('[sendline-desktop] agent loop exited:', err);
    });

    createMainWindow();

    createSendlineTray(
      () => mainWindow,
      agentRunner,
      () => {
        openSettingsWindow(afterAgentSettingsSaved);
      },
      config,
    );
  });
}

app.on('window-all-closed', () => {
  agentRunner?.stop();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createMainWindow();
  }
});

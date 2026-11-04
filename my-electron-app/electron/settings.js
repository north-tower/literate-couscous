import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BrowserWindow, ipcMain } from 'electron';
import { mainWindowWebPreferences } from './browser-prefs.js';
import { SENDLINE_CHROME_BG } from './window-chrome.js';
import { readAgentToken, writeAgentToken, writeAgentApiUrl, readStoredApiUrl } from './config.js';
import { getConfig } from './main.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {BrowserWindow | null} */
let settingsWindow = null;

/** @type {(() => void) | null} */
let onSettingsSaved = null;

/** @type {(() => void) | null} */
let defaultOnSettingsSaved = null;

/**
 * @param {unknown} payload
 * @returns {{ ok: true; apiUrlChanged: boolean }}
 */
async function persistSettingsPayload(payload) {
  const config = getConfig();
  const apiUrl = String(payload?.apiUrl || '').trim();
  const token = String(payload?.token || '').trim();
  let apiUrlChanged = false;
  if (apiUrl) {
    const normalized = apiUrl.replace(/\/+$/, '');
    const before = readStoredApiUrl(config.paths) || config.sendlineApiUrl;
    writeAgentApiUrl(config.paths, apiUrl);
    apiUrlChanged = normalized !== before.replace(/\/+$/, '');
  }
  if (token) await writeAgentToken(config.paths, token);
  return { ok: true, apiUrlChanged };
}

export function registerSettingsIpc(onSaved) {
  defaultOnSettingsSaved = onSaved ?? null;
  ipcMain.handle('settings:get', () => {
    const config = getConfig();
    return {
      apiUrl: readStoredApiUrl(config.paths) || config.sendlineApiUrl,
      hasToken: Boolean(readAgentToken(config.paths)),
    };
  });
  ipcMain.handle('settings:save', async (_event, payload) => {
    const result = await persistSettingsPayload(payload);
    (onSettingsSaved ?? defaultOnSettingsSaved)?.();
    settingsWindow?.close();
    return result;
  });
}

/**
 * @param {() => void} onSaved
 */
export function openSettingsWindow(onSaved) {
  onSettingsSaved = onSaved;
  if (settingsWindow) {
    settingsWindow.focus();
    return;
  }

  const config = getConfig();
  settingsWindow = new BrowserWindow({
    title: 'Sendline Desktop — Settings',
    width: 480,
    height: 360,
    resizable: false,
    minimizable: false,
    maximizable: false,
    autoHideMenuBar: true,
    backgroundColor: SENDLINE_CHROME_BG,
    webPreferences: {
      ...mainWindowWebPreferences,
      preload: path.join(__dirname, 'settings-preload.cjs'),
    },
  });

  settingsWindow.loadFile(path.join(__dirname, 'settings.html')).catch((err) => {
    console.error('[settings] load failed:', err);
  });

  settingsWindow.webContents.on('did-finish-load', () => {
    settingsWindow?.webContents.send('settings:init', {
      apiUrl: readStoredApiUrl(config.paths) || config.sendlineApiUrl,
      hasToken: Boolean(readAgentToken(config.paths)),
      registerHint: `${config.sendlineApiUrl}/`,
    });
  });

  settingsWindow.on('closed', () => {
    settingsWindow = null;
    onSettingsSaved = null;
  });
}

export function shouldPromptForToken() {
  const config = getConfig();
  return !readAgentToken(config.paths);
}

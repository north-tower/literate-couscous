import fs from 'node:fs';
import path from 'node:path';

/** Matches automate/static/styles.css --paper */
export const SENDLINE_CHROME_BG = '#f0fdfa';

/** Merged with app header row (same fill as `.app-header` on desktop) */
export const SENDLINE_HEADER_BG = '#ffffff';

/** Title bar control + overlay tint */
export const SENDLINE_TITLEBAR_OVERLAY = {
  color: SENDLINE_HEADER_BG,
  symbolColor: '#134e4a',
  height: 44,
};

/**
 * @param {import('electron').App} electronApp
 * @returns {string | undefined}
 */
export function resolveAppIconPath(electronApp) {
  const candidate = path.join(electronApp.getAppPath(), 'build', 'icon.png');
  return fs.existsSync(candidate) ? candidate : undefined;
}

/**
 * BrowserWindow options shared by the main Sendline UI window.
 * @param {import('electron').App} electronApp
 * @returns {import('electron').BrowserWindowConstructorOptions}
 */
export function mainWindowChromeOptions(electronApp) {
  const icon = resolveAppIconPath(electronApp);
  /** @type {import('electron').BrowserWindowConstructorOptions} */
  const options = {
    title: 'Sendline',
    width: 1200,
    height: 800,
    backgroundColor: SENDLINE_CHROME_BG,
    autoHideMenuBar: true,
    show: false,
    ...(icon ? { icon } : {}),
  };

  if (process.platform === 'win32') {
    options.titleBarStyle = 'hidden';
    options.titleBarOverlay = SENDLINE_TITLEBAR_OVERLAY;
  } else if (process.platform === 'darwin') {
    options.titleBarStyle = 'hiddenInset';
  }

  return options;
}

import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export function getPreloadPath() {
  return path.join(__dirname, 'preload.cjs');
}

/** @type {import('electron').WebPreferences} */
export const mainWindowWebPreferences = {
  preload: getPreloadPath(),
  contextIsolation: true,
  nodeIntegration: false,
};

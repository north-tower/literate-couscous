import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { safeStorage } from 'electron';

const require = createRequire(import.meta.url);
const pkg = require('../package.json');

/** Default hosted API origin; override with SENDLINE_API_URL or Settings. Local dev: http://127.0.0.1:5055 */
export const DEFAULT_SENDLINE_API_URL = 'https://chelping.com';

/**
 * Normalize base URL: trim, no trailing slash.
 * @param {string | undefined} raw
 * @returns {string}
 */
export function normalizeApiUrl(raw) {
  const value = (raw || DEFAULT_SENDLINE_API_URL).trim();
  return value.replace(/\/+$/, '');
}

/**
 * Resolve Sendline API origin from environment (after dotenv in main).
 * @returns {string}
 */
export function getSendlineApiUrl() {
  return normalizeApiUrl(process.env.SENDLINE_API_URL);
}

/**
 * Local agent/worker paths. Uses Electron userData unless SENDLINE_AGENT_DATA is set
 * (same override as scripts/agent_dev.py).
 * @param {import('electron').App} electronApp
 */
export function getSendlinePaths(electronApp) {
  const root = process.env.SENDLINE_AGENT_DATA?.trim()
    ? path.resolve(process.env.SENDLINE_AGENT_DATA.trim())
    : electronApp.getPath('userData');

  return {
    root,
    jobsDir: path.join(root, 'jobs'),
    chromeDir: path.join(root, 'chrome'),
    attachmentsDir: path.join(root, 'attachments'),
    ledgerPath: path.join(root, 'send_ledger.json'),
    agentConfigPath: path.join(root, 'agent-config.json'),
  };
}

/**
 * @param {import('electron').App} electronApp
 */
export function getDesktopConfig(electronApp) {
  return {
    sendlineApiUrl: getSendlineApiUrl(),
    paths: getSendlinePaths(electronApp),
  };
}

export function readDesktopMeta() {
  return {
    agentVersion: `sendline-desktop/${pkg.version}`,
  };
}

function readAgentConfigFile(paths) {
  try {
    const raw = fs.readFileSync(paths.agentConfigPath, 'utf8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function writeAgentConfigFile(paths, data) {
  const dir = path.dirname(paths.agentConfigPath);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(paths.agentConfigPath, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
}

function decryptStoredToken(parsed) {
  const enc = typeof parsed.token_enc === 'string' ? parsed.token_enc.trim() : '';
  if (enc && safeStorage.isEncryptionAvailable()) {
    try {
      return safeStorage.decryptString(Buffer.from(enc, 'base64')).trim() || null;
    } catch {
      return null;
    }
  }
  const legacy = typeof parsed.token === 'string' ? parsed.token.trim() : '';
  return legacy || null;
}

async function encryptTokenForStorage(token) {
  const trimmed = token.trim();
  if (safeStorage.isEncryptionAvailable()) {
    const encrypted = await safeStorage.encryptStringAsync(trimmed);
    return { token_enc: encrypted.toString('base64') };
  }
  console.warn('[sendline-desktop] OS encryption unavailable; storing agent token in plaintext.');
  return { token: trimmed };
}

async function migratePlainTokenToEncrypted(paths, parsed, token) {
  const next = { ...parsed };
  delete next.token;
  Object.assign(next, await encryptTokenForStorage(token));
  writeAgentConfigFile(paths, next);
}

/**
 * Agent Bearer token: env SENDLINE_AGENT_TOKEN, then agent-config.json.
 * @param {ReturnType<typeof getSendlinePaths>} paths
 * @returns {string | null}
 */
export function readAgentToken(paths) {
  const fromEnv = process.env.SENDLINE_AGENT_TOKEN?.trim();
  if (fromEnv) return fromEnv;
  const parsed = readAgentConfigFile(paths);
  if (!parsed) return null;
  const token = decryptStoredToken(parsed);
  if (token && typeof parsed.token === 'string' && !parsed.token_enc && safeStorage.isEncryptionAvailable()) {
    void migratePlainTokenToEncrypted(paths, parsed, token);
  }
  return token;
}

/**
 * @param {ReturnType<typeof getSendlinePaths>} paths
 * @param {string} token
 */
export async function writeAgentToken(paths, token) {
  const existing = readAgentConfigFile(paths) || {};
  const next = { ...existing };
  delete next.token;
  delete next.token_enc;
  Object.assign(next, await encryptTokenForStorage(token));
  writeAgentConfigFile(paths, next);
}

/**
 * @param {ReturnType<typeof getSendlinePaths>} paths
 * @param {string} apiUrl
 */
export function writeAgentApiUrl(paths, apiUrl) {
  const existing = readAgentConfigFile(paths) || {};
  writeAgentConfigFile(paths, { ...existing, api_url: normalizeApiUrl(apiUrl) });
}

/**
 * Optional api_url stored in agent-config.json (overrides .env when set).
 * @param {ReturnType<typeof getSendlinePaths>} paths
 */
export function readStoredApiUrl(paths) {
  try {
    const parsed = JSON.parse(fs.readFileSync(paths.agentConfigPath, 'utf8'));
    if (typeof parsed.api_url === 'string' && parsed.api_url.trim()) {
      return normalizeApiUrl(parsed.api_url);
    }
  } catch {
    /* ignore */
  }
  return null;
}

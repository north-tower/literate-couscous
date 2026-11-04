#!/usr/bin/env node
/**
 * Copy PyInstaller output into Electron resources (Windows + macOS).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const electronRoot = path.resolve(__dirname, '..');

/** Repo root: monorepo (my-electron-app inside automate) or sibling ../automate layout. */
function resolveAutomateRoot() {
  const fromEnv = process.env.SENDLINE_AUTOMATE_ROOT?.trim();
  if (fromEnv) {
    return path.resolve(fromEnv);
  }
  const monorepoRoot = path.resolve(electronRoot, '..');
  if (fs.existsSync(path.join(monorepoRoot, 'worker.py'))) {
    return monorepoRoot;
  }
  const siblingAutomate = path.resolve(electronRoot, '..', 'automate');
  if (fs.existsSync(path.join(siblingAutomate, 'worker.py'))) {
    return siblingAutomate;
  }
  return siblingAutomate;
}

const automateRoot = resolveAutomateRoot();

const workerName = process.platform === 'win32' ? 'sendline-worker.exe' : 'sendline-worker';
const srcDir = path.join(automateRoot, 'dist', 'sendline-worker');
const srcBin = path.join(srcDir, workerName);
const destDir = path.join(electronRoot, 'resources', 'sendline-worker');

if (!fs.existsSync(srcBin)) {
  const buildHint =
    process.platform === 'win32'
      ? 'From repo root: .\\scripts\\build-worker.ps1'
      : 'From repo root: ./scripts/build-worker.sh';
  console.error(`Worker not built (${srcBin}). Run: ${buildHint}`);
  process.exit(1);
}

fs.rmSync(destDir, { recursive: true, force: true });
fs.mkdirSync(destDir, { recursive: true });

for (const entry of fs.readdirSync(srcDir, { withFileTypes: true })) {
  const from = path.join(srcDir, entry.name);
  const to = path.join(destDir, entry.name);
  if (entry.isDirectory()) {
    fs.cpSync(from, to, { recursive: true });
  } else {
    fs.copyFileSync(from, to);
  }
}

console.log(`Copied worker to ${destDir}`);

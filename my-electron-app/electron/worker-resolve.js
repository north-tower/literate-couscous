import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function defaultWorkerPyPath() {
  const fromEnv = process.env.SENDLINE_WORKER_PY?.trim();
  if (fromEnv) return path.resolve(fromEnv);
  const electronRoot = path.resolve(__dirname, '..');
  const monorepoWorker = path.resolve(electronRoot, '..', 'worker.py');
  if (fs.existsSync(monorepoWorker)) {
    return monorepoWorker;
  }
  return path.resolve(electronRoot, '..', 'automate', 'worker.py');
}

/** @returns {string | null} */
export function readBundledWorkerVersion(app) {
  if (!app.isPackaged) {
    return process.env.WORKER_VERSION?.trim() || 'dev';
  }
  const versionPath = path.join(process.resourcesPath, 'sendline-worker', 'VERSION');
  try {
    return fs.readFileSync(versionPath, 'utf8').trim() || 'unknown';
  } catch {
    return 'unknown';
  }
}

/**
 * Resolve how to spawn the LinkedIn worker (dev Python vs bundled exe).
 * @param {import('electron').App} app
 */
export function resolveWorkerLaunch(app) {
  const debugPort = Number(process.env.SENDLINE_DEBUG_PORT || 9222);

  if (!app.isPackaged) {
    const repoWorker = defaultWorkerPyPath();
    const python =
      process.env.SENDLINE_PYTHON?.trim() ||
      (process.platform === 'win32' ? 'py' : 'python3');
    const workerCwd = process.env.SENDLINE_WORKER_CWD?.trim() || path.dirname(repoWorker);
    return {
      mode: 'python',
      command: python,
      scriptArgs: ['-u', repoWorker],
      cwd: workerCwd,
      debugPort,
    };
  }

  const exeDir = path.join(process.resourcesPath, 'sendline-worker');
  const workerName = process.platform === 'win32' ? 'sendline-worker.exe' : 'sendline-worker';
  const exe = path.join(exeDir, workerName);
  if (!fs.existsSync(exe)) {
    throw new Error(`Bundled worker not found: ${exe}`);
  }
  return {
    mode: 'exe',
    command: exe,
    scriptArgs: [],
    cwd: exeDir,
    debugPort,
  };
}

/**
 * @param {ReturnType<typeof resolveWorkerLaunch>} launch
 * @param {string} jobDir
 * @param {string} chromeDir
 */
export function workerSpawnArgs(launch, jobDir, chromeDir) {
  return [
    ...launch.scriptArgs,
    '--job-dir',
    jobDir,
    '--user-data-dir',
    chromeDir,
    '--debug-port',
    String(launch.debugPort),
  ];
}

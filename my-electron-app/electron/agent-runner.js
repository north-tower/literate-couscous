import fs from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { createAgentApi, AgentHttpError } from './agent-api.js';
import { readAgentToken, readDesktopMeta } from './config.js';
import { workerSpawnArgs, resolveWorkerLaunch, readBundledWorkerVersion } from './worker-resolve.js';

export const AGENT_VERSION = readDesktopMeta().agentVersion;

const POLL_IDLE_MS = 2500;
const HEARTBEAT_MS = 30_000;
const LOG_BATCH_SIZE = 40;
const FORCE_STOP_MS = 15_000;

/** @typedef {'idle' | 'running' | 'stopping' | 'error'} AgentPhase */

/**
 * @typedef {object} AgentPublicStatus
 * @property {boolean} connected
 * @property {boolean} token_invalid
 * @property {AgentPhase} phase
 * @property {number | null} job_id
 * @property {string | null} last_error
 */

/**
 * @param {import('electron').App} app
 * @param {ReturnType<import('./config.js').getDesktopConfig>} config
 */
export function createAgentRunner(app, config) {
  /** @type {AgentPhase} */
  let phase = 'idle';
  /** @type {number | null} */
  let activeJobId = null;
  /** @type {string | null} */
  let lastError = null;
  let tokenInvalid = false;
  let loopAbort = false;
  /** @type {Promise<void> | null} */
  let loopPromise = null;
  /** @type {Set<(status: AgentPublicStatus) => void>} */
  const listeners = new Set();

  const workerLaunch = resolveWorkerLaunch(app);
  const workerVersion = readBundledWorkerVersion(app);

  function publicStatus() {
    const hasToken = Boolean(readAgentToken(config.paths));
    return /** @type {AgentPublicStatus & { running: boolean }} */ ({
      connected: hasToken && !tokenInvalid,
      token_invalid: tokenInvalid,
      running: phase === 'running' || phase === 'stopping',
      phase,
      job_id: activeJobId,
      last_error: lastError,
    });
  }

  /**
   * @param {unknown} err
   */
  function isUnauthorizedError(err) {
    if (err instanceof AgentHttpError && err.status === 401) return true;
    const msg = String(/** @type {Error} */ (err)?.message || err);
    return msg.includes('(401)') || msg.includes('Invalid or revoked agent token');
  }

  /**
   * @param {unknown} err
   * @returns {boolean} true if handled as auth failure
   */
  function handleAgentError(err) {
    if (!isUnauthorizedError(err)) return false;
    const firstRejection = !tokenInvalid;
    tokenInvalid = true;
    lastError = 'Agent token rejected by server. Link this PC again.';
    setPhase('error');
    if (firstRejection) {
      console.warn(
        '[agent-runner] agent token rejected (401) — polling paused until you link this PC again',
      );
    }
    return true;
  }

  function clearTokenInvalidIfNeeded() {
    if (!tokenInvalid) return;
    tokenInvalid = false;
    lastError = null;
    if (phase === 'error') setPhase('idle');
    else emitStatus();
  }

  function emitStatus() {
    const snap = publicStatus();
    for (const fn of listeners) {
      try {
        fn(snap);
      } catch (err) {
        console.error('[agent-runner] status listener error:', err);
      }
    }
  }

  function setPhase(next) {
    phase = next;
    emitStatus();
  }

  /**
   * @param {Record<string, unknown>} work
   */
  function isFullWorkBundle(work) {
    return typeof work.secrets === 'object' && work.secrets !== null && typeof work.run === 'object' && work.run !== null;
  }

  /**
   * @param {ReturnType<typeof createAgentApi>} api
   * @param {Record<string, unknown>} work
   */
  async function prepareJobDir(api, work) {
    const jobId = Number(work.job_id);
    const folder = path.join(config.paths.jobsDir, String(jobId));
    await fs.mkdir(folder, { recursive: true });
    await fs.mkdir(config.paths.chromeDir, { recursive: true });
    await fs.mkdir(path.dirname(config.paths.ledgerPath), { recursive: true });

    const run = { ...(/** @type {Record<string, unknown>} */ (work.run)) };
    const attachmentMeta = work.attachment;
    if (attachmentMeta && typeof attachmentMeta === 'object') {
      const localFile = await api.downloadAttachment(
        jobId,
        /** @type {Record<string, unknown>} */ (attachmentMeta),
        config.paths.attachmentsDir,
      );
      run.attachment_path = localFile;
    } else {
      run.attachment_path = null;
    }
    run.ledger_path = config.paths.ledgerPath;

    const secrets = work.secrets;
    const runPath = path.join(folder, 'run.json');
    const secretsPath = path.join(folder, 'secrets.json');
    await fs.writeFile(runPath, `${JSON.stringify(run, null, 2)}\n`, 'utf8');
    await fs.writeFile(secretsPath, `${JSON.stringify(secrets, null, 2)}\n`, 'utf8');
    try {
      await fs.chmod(secretsPath, 0o600);
    } catch {
      /* Windows */
    }
    const stopFlag = path.join(folder, 'stop');
    try {
      await fs.unlink(stopFlag);
    } catch {
      /* ok */
    }
    return folder;
  }

  async function writeStopFlag(jobId) {
    const flag = path.join(config.paths.jobsDir, String(jobId), 'stop');
    await fs.mkdir(path.dirname(flag), { recursive: true });
    await fs.writeFile(flag, 'stop\n', 'utf8');
    return flag;
  }

  /**
   * @param {import('node:child_process').ChildProcess} proc
   * @param {number} jobId
   */
  function scheduleForceKillWorker(proc, jobId) {
    setTimeout(() => {
      if (proc.exitCode !== null) return;
      console.warn(`[agent-runner] job ${jobId} stop timed out — killing worker`);
      try {
        proc.kill();
      } catch (err) {
        console.error('[agent-runner] worker kill failed:', err);
      }
    }, FORCE_STOP_MS);
  }

  /**
   * When the server says ``stopping`` but this agent is not running a worker (orphan / crashed worker),
   * write the local stop file and report completion so the campaign is not stuck forever.
   *
   * @param {ReturnType<typeof createAgentApi>} api
   * @param {number} jobId
   */
  async function reconcileOrphanStop(api, jobId) {
    try {
      const flag = await writeStopFlag(jobId);
      console.log(`[agent-runner] orphan stop → ${flag}`);
      await api.postComplete(jobId, 130);
      console.log(`[agent-runner] orphan job ${jobId} marked cancelled on server`);
    } catch (err) {
      if (!handleAgentError(err)) {
        console.error('[agent-runner] orphan stop reconcile failed:', err);
      }
    }
  }

  /**
   * @param {ReturnType<typeof createAgentApi>} api
   * @param {Record<string, unknown>} work
   */
  async function runWorkerJob(api, work) {
    const jobId = Number(work.job_id);
    activeJobId = jobId;
    setPhase('running');
    lastError = null;

    const folder = await prepareJobDir(api, work);
    console.log(`[agent-runner] job ${jobId} → ${folder}`);

    const args = workerSpawnArgs(workerLaunch, folder, config.paths.chromeDir);
    const proc = spawn(workerLaunch.command, args, {
      cwd: workerLaunch.cwd,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: false,
    });

    /** @type {string[]} */
    let pending = [];
    let stopPollDone = false;
    let heartbeatTimer = null;

    const flushLogs = async () => {
      if (!pending.length) return;
      const batch = pending.splice(0, LOG_BATCH_SIZE);
      await api.postLogs(jobId, batch);
    };

    const heartbeatTick = async () => {
      try {
        await api.postHeartbeat({
          agent_version: AGENT_VERSION,
          worker_version: workerVersion,
        });
      } catch (err) {
        console.error('[agent-runner] heartbeat error:', err);
      }
    };

    heartbeatTimer = setInterval(heartbeatTick, HEARTBEAT_MS);
    heartbeatTick().catch(() => {});

    const stopPoll = async () => {
      while (!stopPollDone && proc.exitCode === null) {
        await sleep(POLL_IDLE_MS);
        if (proc.exitCode !== null) return;
        try {
          const poll = await api.getWork();
          if (poll.stop_requested) {
            const flag = await writeStopFlag(jobId);
            console.log(`[agent-runner] stop requested → ${flag}`);
            stopPollDone = true;
            setPhase('stopping');
            scheduleForceKillWorker(proc, jobId);
            return;
          }
        } catch (err) {
          console.error('[agent-runner] stop poll error:', err);
        }
      }
    };
    const stopPollPromise = stopPoll();

    const onLine = async (line) => {
      console.log(line);
      pending.push(line);
      if (pending.length >= LOG_BATCH_SIZE) {
        try {
          await flushLogs();
        } catch (err) {
          console.error('[agent-runner] log flush error:', err);
        }
      }
    };

    proc.stdout?.setEncoding('utf8');
    proc.stderr?.setEncoding('utf8');
    proc.stdout?.on('data', (chunk) => {
      for (const line of chunk.split(/\r?\n/)) {
        if (line) onLine(line).catch(() => {});
      }
    });
    proc.stderr?.on('data', (chunk) => {
      for (const line of chunk.split(/\r?\n/)) {
        if (line) onLine(`[stderr] ${line}`).catch(() => {});
      }
    });

    let exitCode = 1;
    try {
      exitCode = await new Promise((resolve) => {
        proc.on('error', (err) => {
          console.error('[agent-runner] worker spawn error:', err);
          lastError = String(err.message || err);
          resolve(1);
        });
        proc.on('close', (code) => resolve(code ?? 1));
      });
    } finally {
      stopPollDone = true;
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      await stopPollPromise.catch(() => {});
      try {
        await flushLogs();
      } catch (err) {
        console.error('[agent-runner] final log flush failed:', err);
      }
      try {
        await api.postComplete(jobId, exitCode);
      } catch (err) {
        console.error('[agent-runner] complete failed:', err);
        if (!handleAgentError(err)) {
          lastError = String(err.message || err);
          setPhase('error');
        }
        throw err;
      }
      console.log(`[agent-runner] job ${jobId} finished exit ${exitCode}`);
      activeJobId = null;
      setPhase('idle');
    }
  }

  async function idleLoop(api) {
    while (!loopAbort) {
      if (tokenInvalid) {
        await sleep(POLL_IDLE_MS);
        continue;
      }
      try {
        const poll = await api.getWork();
        clearTokenInvalidIfNeeded();
        const work = poll.work;
        if (!work || typeof work !== 'object') {
          activeJobId = null;
          if (phase !== 'error') setPhase('idle');
          await sleep(POLL_IDLE_MS);
          continue;
        }

        const jobId = Number(work.job_id);
        if (isFullWorkBundle(/** @type {Record<string, unknown>} */ (work))) {
          await runWorkerJob(api, /** @type {Record<string, unknown>} */ (work));
          continue;
        }

        if (poll.stop_requested && activeJobId === null) {
          await reconcileOrphanStop(api, jobId);
          await sleep(POLL_IDLE_MS);
          continue;
        }

        if (activeJobId === null && (work.status === 'running' || work.status === 'stopping')) {
          console.warn(
            `[agent-runner] job ${jobId} in progress on server but not started here (status=${work.status})`,
          );
        }
        await sleep(POLL_IDLE_MS);
      } catch (err) {
        if (!handleAgentError(err)) {
          console.error('[agent-runner] work poll error:', err);
          lastError = String(err.message || err);
          setPhase('error');
        }
        await sleep(POLL_IDLE_MS);
      }
    }
  }

  return {
    getStatus: publicStatus,
    /** @param {(status: AgentPublicStatus) => void} fn */
    onStatus(fn) {
      listeners.add(fn);
      fn(publicStatus());
      return () => listeners.delete(fn);
    },
    start() {
      if (loopPromise) return loopPromise;
      const token = readAgentToken(config.paths);
      if (!token) {
        console.warn('[agent-runner] no agent token — polling disabled until configured');
        emitStatus();
        return Promise.resolve();
      }
      const api = createAgentApi(config.sendlineApiUrl, token);
      loopAbort = false;
      lastError = null;
      tokenInvalid = false;
      setPhase('idle');
      api.postHeartbeat({ agent_version: AGENT_VERSION, worker_version: workerVersion }).catch((err) => {
        if (!handleAgentError(err)) {
          console.error('[agent-runner] initial heartbeat failed:', err);
          lastError = String(err.message || err);
          setPhase('error');
        }
      });
      loopPromise = idleLoop(api).finally(() => {
        loopPromise = null;
      });
      return loopPromise;
    },
    stop() {
      loopAbort = true;
    },
    /** Restart loop after token change */
    restart() {
      this.stop();
      loopPromise = null;
      loopAbort = false;
      return this.start();
    },
    isJobRunning() {
      return phase === 'running' || phase === 'stopping';
    },
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

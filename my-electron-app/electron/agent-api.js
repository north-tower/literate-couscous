/**
 * Sendline agent HTTP client (Bearer auth). Main process only.
 */

export class AgentHttpError extends Error {
  /**
   * @param {string} message
   * @param {number} status
   */
  constructor(message, status) {
    super(message);
    this.name = 'AgentHttpError';
    this.status = status;
  }
}

/**
 * @param {string} baseUrl
 * @param {string} bearer
 */
export function createAgentApi(baseUrl, bearer) {
  const apiRoot = baseUrl.replace(/\/+$/, '');

  /**
   * @param {string} method
   * @param {string} path
   * @param {object} [options]
   * @param {Record<string, string>} [options.headers]
   * @param {unknown} [options.body]
   * @param {ArrayBuffer} [options.rawResponse]
   */
  async function request(method, path, options = {}) {
    const url = `${apiRoot}${path}`;
    /** @type {RequestInit} */
    const init = {
      method,
      headers: {
        Authorization: `Bearer ${bearer}`,
        ...(options.headers || {}),
      },
    };
    if (options.body !== undefined) {
      init.headers = { ...init.headers, 'Content-Type': 'application/json' };
      init.body = JSON.stringify(options.body);
    }

    const resp = await fetch(url, init);
    const contentType = resp.headers.get('content-type') || '';
    if (options.rawResponse) {
      const buf = await resp.arrayBuffer();
      return { status: resp.status, data: buf };
    }
    let data = null;
    if (contentType.includes('application/json')) {
      data = await resp.json();
    } else {
      const text = await resp.text();
      data = text || null;
    }
    return { status: resp.status, data };
  }

  return {
    async getWork() {
      const { status, data } = await request('GET', '/api/agent/work');
      if (status !== 200 || !data || typeof data !== 'object') {
        throw new AgentHttpError(
          `GET /api/agent/work failed (${status}): ${JSON.stringify(data)}`,
          status,
        );
      }
      return /** @type {Record<string, unknown>} */ (data);
    },

    /**
     * @param {number} jobId
     * @param {string[]} lines
     */
    async postLogs(jobId, lines) {
      const { status, data } = await request('POST', '/api/agent/logs', {
        body: { job_id: jobId, lines },
      });
      if (status !== 200) {
        throw new AgentHttpError(
          `POST /api/agent/logs failed (${status}): ${JSON.stringify(data)}`,
          status,
        );
      }
    },

    /**
     * @param {{ agent_version?: string; worker_version?: string }} body
     */
    async postHeartbeat(body) {
      const { status, data } = await request('POST', '/api/agent/heartbeat', { body });
      if (status !== 200) {
        throw new AgentHttpError(
          `POST /api/agent/heartbeat failed (${status}): ${JSON.stringify(data)}`,
          status,
        );
      }
    },

    /**
     * @param {number} jobId
     * @param {number} exitCode
     */
    async postComplete(jobId, exitCode) {
      const { status, data } = await request('POST', '/api/agent/complete', {
        body: { job_id: jobId, exit_code: exitCode },
      });
      if (status !== 200) {
        throw new AgentHttpError(
          `POST /api/agent/complete failed (${status}): ${JSON.stringify(data)}`,
          status,
        );
      }
    },

    /**
     * @param {number} jobId
     * @param {Record<string, unknown>} attachment
     * @param {string} destPath
     */
    async downloadAttachment(jobId, attachment, destPath) {
      const fs = await import('node:fs/promises');
      const path = await import('node:path');
      const name = path.basename(String(attachment.name || 'attachment'));
      const rel = attachment.path || `/api/agent/jobs/${jobId}/attachment`;
      const downloadUrl =
        typeof attachment.download_url === 'string'
          ? attachment.download_url
          : `${apiRoot}${rel}`;
      const resp = await fetch(downloadUrl, {
        headers: { Authorization: `Bearer ${bearer}` },
      });
      if (!resp.ok) {
        throw new Error(`Attachment download failed (${resp.status})`);
      }
      const buf = Buffer.from(await resp.arrayBuffer());
      await fs.mkdir(path.dirname(destPath), { recursive: true });
      const finalPath = destPath.endsWith(name) ? destPath : path.join(path.dirname(destPath), name);
      await fs.writeFile(finalPath, buf);
      return finalPath;
    },
  };
}

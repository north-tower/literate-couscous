const { contextBridge, ipcRenderer } = require('electron');

function markSendlineDesktopShell() {
  const root = document.documentElement;
  root.classList.add('is-sendline-desktop');
  if (document.body) {
    document.body.classList.add('is-sendline-desktop');
  }
}

if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', markSendlineDesktopShell, { once: true });
} else {
  markSendlineDesktopShell();
}

/**
 * Renderer-safe API for hosted Sendline UI.
 */
contextBridge.exposeInMainWorld('sendlineDesktop', {
  version: '0.2.0-agent',
  platform: process.platform,
  async getAgentConfig() {
    return ipcRenderer.invoke('settings:get');
  },
  async saveAgentConfig(payload) {
    return ipcRenderer.invoke('settings:save', payload);
  },
  async getAgentStatus() {
    return ipcRenderer.invoke('sendline:getAgentStatus');
  },
  onAgentStatus(callback) {
    const handler = (_event, status) => callback(status);
    ipcRenderer.on('sendline:agentStatus', handler);
    ipcRenderer.invoke('sendline:getAgentStatus').then((status) => {
      if (status) callback(status);
    });
    return () => ipcRenderer.removeListener('sendline:agentStatus', handler);
  },
});

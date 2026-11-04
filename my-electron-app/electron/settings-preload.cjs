const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('sendlineSettings', {
  onInit(callback) {
    ipcRenderer.on('settings:init', (_event, data) => callback(data));
  },
  save(payload) {
    return ipcRenderer.invoke('settings:save', payload);
  },
});

const { contextBridge, ipcRenderer } = require('electron');

const CHANNELS = new Set(['landed', 'falling', 'toggle-chat', 'menu']);

contextBridge.exposeInMainWorld('cazungDesktop', {
  getBackend: () => ipcRenderer.invoke('get-backend'),
  setIgnoreMouse: ignore => ipcRenderer.send('set-ignore-mouse', !!ignore),
  moveBy: dx => ipcRenderer.invoke('move-by', Number(dx) || 0),
  dragStart: () => ipcRenderer.send('drag-start'),
  dragEnd: () => ipcRenderer.send('drag-end'),
  showMenu: state => ipcRenderer.send('context-menu', state || {}),
  ensureVisible: () => ipcRenderer.invoke('ensure-visible'),
  setPetRect: rect => ipcRenderer.send('pet-rect', rect),
  focus: () => ipcRenderer.send('focus'),
  on: (channel, fn) => {
    if (CHANNELS.has(channel)) ipcRenderer.on(channel, (_e, ...args) => fn(...args));
  },
});

import { contextBridge, ipcRenderer } from "electron";

// Minimal, safe bridge: the renderer reads the backend base URL the main
// process resolved (so it can point at localhost in dev and the deployed
// origin in prod) without exposing Node internals.
const api = {
  backendOrigin: ipcRenderer.sendSync("overlay:backend-origin") as string,
  platform: process.platform,
};

contextBridge.exposeInMainWorld("overlay", api);

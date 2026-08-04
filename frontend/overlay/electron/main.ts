import { app, BrowserWindow, ipcMain, shell } from "electron";
import { join } from "node:path";

// The backend origin the overlay connects to. Override with OVERLAY_BACKEND_URL
// (e.g. http://192.168.1.10:8000) when the backend runs on another machine.
const BACKEND_ORIGIN = process.env.OVERLAY_BACKEND_URL ?? "http://localhost:8000";

ipcMain.on("overlay:backend-origin", (event) => {
  event.returnValue = BACKEND_ORIGIN;
});

// Transparent, frameless, always-on-top overlay. Mouse events pass through the
// window to the game; only interactive widgets re-enable clicking locally.
const isDev = !app.isPackaged;

function createOverlay(): BrowserWindow {
  const win = new BrowserWindow({
    width: 420,
    height: 900,
    frame: false,
    transparent: true,
    resizable: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    hasShadow: false,
    // Keep the overlay above full-screen games where possible.
    type: "toolbar",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: join(__dirname, "preload.js"),
    },
  });

  win.setAlwaysOnTop(true, "screen-saver", 1);
  // Default: clicks fall through to the game. Widgets opt back in via CSS
  // `pointer-events: auto` on their own root element.
  win.setIgnoreMouseEvents(true, { forward: true });

  const url = isDev
    ? "http://localhost:5174"
    : `file://${join(__dirname, "..", "dist", "index.html")}`;
  void win.loadURL(url);

  // Open external links in the default browser, not the overlay.
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });

  return win;
}

void app.whenReady().then(() => {
  createOverlay();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createOverlay();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

"use strict";

const { spawn } = require("child_process");
const path = require("path");

const root = path.join(__dirname, "..");
const env = {
  ...process.env,
  CONXA_RENDERER_URL:
    process.env.CONXA_RENDERER_URL || `http://localhost:${process.env.CONXA_RENDERER_PORT || 5174}`,
};

delete env.ELECTRON_RUN_AS_NODE;

// A separate user-data dir gives a second Studio its own single-instance lock.
const extraArgs = process.env.CONXA_USER_DATA_DIR ? [`--user-data-dir=${process.env.CONXA_USER_DATA_DIR}`] : [];
const child = spawn(process.execPath, [require.resolve("electron/cli"), ".", ...extraArgs], {
  cwd: root,
  env,
  stdio: "inherit",
  windowsHide: false,
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    child.kill(signal);
  });
}

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});

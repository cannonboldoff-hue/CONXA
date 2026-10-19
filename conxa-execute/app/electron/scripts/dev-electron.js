"use strict";

const { spawn } = require("child_process");
const http = require("http");
const path = require("path");

// package.json lives at conxa-execute/app/, not electron/ (Studio's script is one level shallower).
const root = path.join(__dirname, "..", "..");
const rendererUrl = process.env.CONXA_RENDERER_URL || "http://localhost:5175";

function waitForRenderer(url, timeoutMs) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      const req = http.get(url, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => {
        if (Date.now() - started > timeoutMs) {
          reject(new Error("Renderer did not start at " + url));
          return;
        }
        setTimeout(tryOnce, 250);
      });
      req.setTimeout(800, () => {
        req.destroy();
      });
    };
    tryOnce();
  });
}

(async () => {
  await waitForRenderer(rendererUrl, 60_000);

  const env = {
    ...process.env,
    CONXA_RENDERER_URL: rendererUrl,
    // Dev lane skips Clerk login by default (same as scripts/conxa.ps1); set to 0 to test real login.
    CONXA_DEV_SKIP_AUTH: process.env.CONXA_DEV_SKIP_AUTH || "1",
  };
  // The dev bypass token is not a Clerk JWT, so only a local backend (SKILL_AUTH_REQUIRED=false) accepts it.
  if (["1", "true", "yes"].includes(env.CONXA_DEV_SKIP_AUTH) && !env.CONXA_EXECUTE_API_BASE_URL) {
    env.CONXA_EXECUTE_API_BASE_URL = "http://127.0.0.1:8000";
  }
  delete env.ELECTRON_RUN_AS_NODE;

  const child = spawn(process.execPath, [require.resolve("electron/cli"), "."], {
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
})().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});

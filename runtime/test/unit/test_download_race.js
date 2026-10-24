"use strict";

// Regression: server.js's page.on("download", ...) listener only pushes an entry onto
// ctx.downloadQueue once Playwright's download event actually fires — which can trail the
// triggering click by real wall-clock time (server round-trip, header negotiation). The
// download_observed handler used to check the queue exactly once and bail silently if it was
// still empty, so a download that simply hadn't started yet looked identical to "no download
// happened at all": downloaded_file/downloaded_file_dir never got bound, and a later upload step
// bound to {{downloaded_file_dir}} failed with a "no file path" error that had nothing to do with
// a missing input. The handler must wait for an entry to arrive, not just glance at the queue.
process.env.CONXA_GATE = "0";
process.env.CONXA_DOWNLOAD_WAIT_MS = "500";

const test = require("node:test");
const assert = require("node:assert");

const { executeStep } = require("../../app/run");

test("download_observed waits for a download that hasn't been queued yet", async () => {
  const inputs = {};
  const queue = [];
  // Simulates the real race: the queue is empty when the step starts, and the download event
  // fires a little later — well inside the wait budget, comfortably past one poll interval.
  setTimeout(() => {
    queue.push(Promise.resolve({ filename: "archive.zip", path: "/runs/r1/archive.zip", extractedDir: "/runs/r1/archive" }));
  }, 50);

  await executeStep(null, { type: "download_observed" }, inputs, { downloadQueue: queue });

  assert.strictEqual(inputs.downloaded_file, "/runs/r1/archive.zip");
  assert.strictEqual(inputs.downloaded_file_dir, "/runs/r1/archive");
});

// Regression: this step used to silently `return` on every no-download path instead of throwing
// — a for_each loop over download_observed could complete every iteration "successfully" while
// downloading some, one, or zero of the files a customer asked for, and nothing downstream ever
// checked. It must now fail loudly and immediately, the same way any other step with a bad
// outcome does, so a for_each's on_row_error: "stop" (the default this shape uses) halts the run
// right there instead of shipping a silently incomplete result.
test("download_observed throws if nothing is ever queued", async () => {
  const inputs = {};
  const queue = [];
  await assert.rejects(
    () => executeStep(null, { type: "download_observed" }, inputs, { downloadQueue: queue }),
    /Expected a file download/,
  );
  assert.strictEqual(inputs.downloaded_file, undefined);
});

test("download_observed throws when the download entry never resolves in time", async () => {
  const inputs = {};
  // A promise that never settles — simulates server.js's save still being in flight when the
  // race's own timeout (the same DOWNLOAD_WAIT_TIMEOUT_MS budget) elapses.
  const queue = [new Promise(() => {})];
  await assert.rejects(
    () => executeStep(null, { type: "download_observed" }, inputs, { downloadQueue: queue }),
    /Expected a file download/,
  );
  assert.strictEqual(inputs.downloaded_file, undefined);
});

test("download_observed throws when the save itself failed (entry resolves null)", async () => {
  const inputs = {};
  // server.js's download listener resolves null (never rejects) when saveAs() itself throws —
  // this is the shape that reaches the handler.
  const queue = [Promise.resolve(null)];
  await assert.rejects(
    () => executeStep(null, { type: "download_observed" }, inputs, { downloadQueue: queue }),
    /Expected a file download/,
  );
  assert.strictEqual(inputs.downloaded_file, undefined);
});

test("download_observed throws when downloadQueue itself is missing", async () => {
  const inputs = {};
  await assert.rejects(
    () => executeStep(null, { type: "download_observed" }, inputs, {}),
    /Expected a file download/,
  );
});

// A download that failed must say why, not just "did not finish saving".
for (const [error, message] of [
  ["cancelled: download of u did not complete", /was cancelled/],
  ["interrupted: download of u did not complete", /was interrupted/],
  ["empty: the downloaded file is 0 bytes", /empty or incomplete/],
]) {
  test(`download_observed reports a failed download: ${error.split(":")[0]}`, async () => {
    const inputs = {};
    await assert.rejects(
      () => executeStep(null, { type: "download_observed" }, inputs, { downloadQueue: [Promise.resolve({ error })] }),
      message,
    );
    assert.strictEqual(inputs.downloaded_file, undefined);
  });
}

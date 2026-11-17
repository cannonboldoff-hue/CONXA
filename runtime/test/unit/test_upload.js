"use strict";

// Unit tests for the upload action. Uploads are always parameterised — the compiler emits
// {"type": "upload", "value": "{{file_path}}"} and the real path arrives as a runtime input
// (see skill_package_builder_saved_skill.py::_saved_step_to_execution_step). These tests pin the
// behaviour that a path which never resolves must FAIL LOUDLY rather than skip: silently not
// uploading a document while reporting success is this action's worst failure mode.
// Disable GATE so no stability checks are simulated — mirrors test_branch.js's setup.
process.env.CONXA_GATE = "0";

const test = require("node:test");
const assert = require("node:assert");

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { executeStep, interpolate, resolveUploadPaths } = require("../../app/run");

// The handler throws before touching the page whenever the path is empty, so these cases need
// no browser and no locator mock at all. A page that throws on any access proves it.
const unusedPage = new Proxy({}, {
  get() { throw new Error("page must not be touched when the upload path is missing"); },
});

test("upload throws when the step has no value at all", async () => {
  await assert.rejects(
    () => executeStep(unusedPage, { type: "upload" }, {}),
    /no file path/,
  );
});

test("upload throws when the value is an empty string", async () => {
  await assert.rejects(
    () => executeStep(unusedPage, { type: "upload", value: "" }, {}),
    /no file path/,
  );
});

// The regression this file exists for: before the fix an unresolved {{file_path}} interpolated
// to "" and the handler returned silently, so the skill reported success having uploaded
// nothing. server.js's required-input gate should reject this first, but the handler must not
// depend on that.
test("upload throws when {{file_path}} is declared but not supplied", async () => {
  await assert.rejects(
    () => executeStep(unusedPage, { type: "upload", value: "{{file_path}}" }, {}),
    /no file path/,
  );
});

test("upload throws when file_path is supplied as whitespace-free empty value", async () => {
  await assert.rejects(
    () => executeStep(unusedPage, { type: "upload", value: "{{file_path}}" }, { file_path: "" }),
    /no file path/,
  );
});

// Regression: a step bound to {{downloaded_file_dir}} (or any of the other runtime-only
// download placeholders) never declares that name as an input — filter_runtime_only_inputs
// (compiler) deliberately excludes it — so the generic "supply the skill's file_path input"
// message pointed the user at an input that was never going to exist. When this specific
// placeholder family resolves empty, the error must name the real cause instead.
test("upload names the real cause when a downloaded_file placeholder never resolved", async () => {
  await assert.rejects(
    () => executeStep(unusedPage, { type: "upload", value: "{{downloaded_file_dir}}" }, {}),
    /recorded download.*didn't produce a file/,
  );
});

test("upload still gives the generic message for an unrelated unresolved placeholder", async () => {
  await assert.rejects(
    () => executeStep(unusedPage, { type: "upload", value: "{{some_other_input}}" }, {}),
    /no file path/,
  );
});

// The value plumbing itself — that a supplied input reaches the step's path — is interpolate's
// job. Asserting it here keeps the contract pinned without mocking the whole resolver stack
// (already covered by test_resolver.js / test_resolve_adapter.js).
test("file_path input interpolates into the upload path", () => {
  assert.strictEqual(
    interpolate("{{file_path}}", { file_path: "C:/docs/kyc_document.pdf" }),
    "C:/docs/kyc_document.pdf",
  );
});

test("a hand-authored literal path is passed through unchanged", () => {
  assert.strictEqual(interpolate("C:/fixed/form.pdf", {}), "C:/fixed/form.pdf");
});

// Regression: Windows Explorer's "Copy as path" wraps any path containing spaces in double
// quotes. Before the fix, that quoted string was passed straight to setInputFiles; Node only
// recognizes a bare drive letter as absolute, so the quoted path was treated as relative and
// silently joined onto the runtime's own working directory, failing with a garbled ENOENT deep
// inside the runtime's own install path instead of a clear error about the file itself.
// `multiple` mirrors the live element's attribute, which is what the handler probes via
// locator.evaluate. undefined simulates a probe that could not run at all.
function mockUploadPage(sel, multiple = true) {
  const calls = [];
  return {
    page: {
      locator: (s) => {
        assert.strictEqual(s, sel);
        return {
          count: async () => 1,
          first: () => ({
            setInputFiles: async (path, opts) => { calls.push(path); },
            evaluate: async (fn) => {
              if (multiple === undefined) throw new Error("evaluate blocked");
              return fn({ multiple });
            },
          }),
        };
      },
    },
    calls,
  };
}

test("a quoted path from Windows 'Copy as path' is unwrapped before use", async () => {
  const { page, calls } = mockUploadPage("#file-upload");
  const step = {
    type: "upload",
    value: "{{file_path}}",
    _explicit_selector: "#file-upload",
  };
  const raw = 'C:\\Users\\Lenovo\\Downloads\\WhatsApp Unknown 2026-08-06\\photo.jpeg';
  await executeStep(page, step, { file_path: `"${raw}"` });
  assert.deepStrictEqual(calls, [[raw]]);
});

test("a path with incidental leading/trailing whitespace is trimmed", async () => {
  const { page, calls } = mockUploadPage("#file-upload");
  const step = { type: "upload", value: "{{file_path}}", _explicit_selector: "#file-upload" };
  await executeStep(page, step, { file_path: "  C:/docs/kyc.pdf  " });
  assert.deepStrictEqual(calls, [["C:/docs/kyc.pdf"]]);
});

test("an unmatched leading quote (no trailing quote) is left alone rather than mangled", async () => {
  const { page, calls } = mockUploadPage("#file-upload");
  const step = { type: "upload", value: "{{file_path}}", _explicit_selector: "#file-upload" };
  await executeStep(page, step, { file_path: '"C:/docs/kyc.pdf' });
  assert.deepStrictEqual(calls, [['"C:/docs/kyc.pdf']]);
});

// EXEC-15: a batch upload is expressed as one folder path, not N file paths — that is what
// lets an agent move 20 or 200 documents without enumerating every one of them in the
// conversation. Before this, only the first file of a recorded multi-select ever uploaded.
function makeTempDir(names) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-upload-test-"));
  for (const name of names) fs.writeFileSync(path.join(dir, name), "x");
  return dir;
}

test("a folder path expands to every file inside it", async () => {
  const dir = makeTempDir(["b.pdf", "a.pdf", "c.pdf"]);
  try {
    const { page, calls } = mockUploadPage("#file-upload");
    const step = { type: "upload", value: "{{file_path}}", _explicit_selector: "#file-upload" };
    await executeStep(page, step, { file_path: dir });
    assert.deepStrictEqual(calls, [[
      path.join(dir, "a.pdf"),
      path.join(dir, "b.pdf"),
      path.join(dir, "c.pdf"),
    ]]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// Order is part of the outcome for a batch: invoice-2 must not land after invoice-10 just
// because "1" sorts before "2" by codepoint.
test("folder expansion sorts numerically, not by raw codepoint", () => {
  const dir = makeTempDir(["invoice-10.pdf", "invoice-2.pdf", "invoice-1.pdf"]);
  try {
    assert.deepStrictEqual(
      resolveUploadPaths(dir).map(p => path.basename(p)),
      ["invoice-1.pdf", "invoice-2.pdf", "invoice-10.pdf"],
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("subdirectories inside an upload folder are not passed as files", () => {
  const dir = makeTempDir(["real.pdf"]);
  try {
    fs.mkdirSync(path.join(dir, "nested"));
    fs.writeFileSync(path.join(dir, "nested", "deep.pdf"), "x");
    assert.deepStrictEqual(resolveUploadPaths(dir), [path.join(dir, "real.pdf")]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// Same reasoning as the empty-path throws above: uploading nothing while reporting success is
// this action's worst failure mode, and an empty folder is the batch-shaped way to hit it.
test("an empty folder throws rather than uploading nothing", () => {
  const dir = makeTempDir([]);
  try {
    assert.throws(() => resolveUploadPaths(dir), /no files in it/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("a single existing file still resolves to exactly that one file", () => {
  const dir = makeTempDir(["only.pdf"]);
  try {
    const file = path.join(dir, "only.pdf");
    assert.deepStrictEqual(resolveUploadPaths(file), [file]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// Whether a control takes one file or many belongs to the live page, not to the recording —
// so the handler asks the element. A folder aimed at a single-file input must be refused with
// a message that says what to do instead, not with Playwright's internal wording, and must not
// reach the recovery cascade (nothing about re-finding the element fixes wrong input).
test("a folder given to a single-file upload control is refused by name", async () => {
  const dir = makeTempDir(["a.pdf", "b.pdf"]);
  try {
    const { page, calls } = mockUploadPage("#file-upload", false);
    const step = { type: "upload", value: "{{file_path}}", _explicit_selector: "#file-upload" };
    await assert.rejects(
      () => executeStep(page, step, { file_path: dir }),
      (err) => {
        assert.match(err.message, /accepts only one file, but 2 files were given/);
        assert.strictEqual(err.badInput, true, "must be flagged so runPlan skips recovery");
        return true;
      },
    );
    assert.deepStrictEqual(calls, [], "nothing may be uploaded when the input is refused");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("a single file is never probed for multiple — one file always fits", async () => {
  const dir = makeTempDir(["only.pdf"]);
  try {
    const file = path.join(dir, "only.pdf");
    const { page, calls } = mockUploadPage("#file-upload", false);
    const step = { type: "upload", value: "{{file_path}}", _explicit_selector: "#file-upload" };
    await executeStep(page, step, { file_path: file });
    assert.deepStrictEqual(calls, [[file]]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// A probe that cannot run (detached node, cross-origin, evaluate blocked) must not block an
// upload that would otherwise have worked — setInputFiles gets to have its own say.
test("an unreadable multiple probe stays permissive rather than refusing", async () => {
  const dir = makeTempDir(["a.pdf", "b.pdf"]);
  try {
    const { page, calls } = mockUploadPage("#file-upload", undefined);
    const step = { type: "upload", value: "{{file_path}}", _explicit_selector: "#file-upload" };
    await executeStep(page, step, { file_path: dir });
    assert.deepStrictEqual(calls, [[path.join(dir, "a.pdf"), path.join(dir, "b.pdf")]]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// conxa_compile/compiler/upload_binding.py compiles a matched bulk upload's value to the literal
// string "{{downloaded_files_dir}}"; server.js sets inputs.downloaded_files_dir to this run's
// own isolated download folder before runPlan starts. A rename on either side of that contract
// breaks the handoff silently (falls through to a plain-file-not-found error deep in
// Playwright) — pin the placeholder name and the full interpolate -> resolveUploadPaths ->
// upload pipeline together so a mismatch fails loudly and close to the cause.
test("the compiled downloaded_files_dir placeholder resolves to every file this run downloaded", async () => {
  const dir = makeTempDir(["a.pdf", "b.pdf", "c.pdf"]);
  try {
    const { page, calls } = mockUploadPage("#file-upload");
    const step = { type: "upload", value: "{{downloaded_files_dir}}", _explicit_selector: "#file-upload" };
    await executeStep(page, step, { downloaded_files_dir: dir });
    assert.deepStrictEqual(calls, [[
      path.join(dir, "a.pdf"),
      path.join(dir, "b.pdf"),
      path.join(dir, "c.pdf"),
    ]]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// BUILD-31: unlike downloaded_file/downloaded_file_N (bound only once a real file exists, and
// throwing badInput otherwise), server.js sets inputs.downloaded_files_dir unconditionally at
// run start — it is never the empty string, so the "no file path" guard above never fires for
// it. A directory that was never created (no download landed in this run) used to fall straight
// through to setInputFiles via resolveUploadPaths's own ENOENT-swallowing fallback.
test("downloaded_files_dir pointing at a directory that was never created throws, badInput", async () => {
  const neverCreated = path.join(os.tmpdir(), "conxa-upload-test-does-not-exist-" + Date.now());
  await assert.rejects(
    () => executeStep(unusedPage, { type: "upload", value: "{{downloaded_files_dir}}" }, { downloaded_files_dir: neverCreated }),
    (err) => {
      assert.match(err.message, /recorded download.*didn't produce a file/);
      assert.strictEqual(err.badInput, true, "must be flagged so runPlan skips recovery");
      return true;
    },
  );
});

test("downloaded_files_dir pointing at an existing but empty directory throws, badInput", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-upload-test-empty-"));
  try {
    await assert.rejects(
      () => executeStep(unusedPage, { type: "upload", value: "{{downloaded_files_dir}}" }, { downloaded_files_dir: dir }),
      (err) => {
        assert.match(err.message, /recorded download.*didn't produce a file/);
        assert.strictEqual(err.badInput, true, "must be flagged so runPlan skips recovery");
        return true;
      },
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// EXEC-19: upload_binding.py's compile-time FIFO consumption already guarantees a downloaded
// file is bound to at most one upload step, but a shared run-wide download folder means that
// guarantee only holds on disk if the runtime deletes each file once it's actually uploaded.
// Without that, a later bulk upload chained in the same run (A downloads -> B bulk-uploads, then
// C downloads more -> D bulk-uploads) can still find A's stale files sitting in the folder.
test("a bulk upload deletes its consumed files so a later chained upload never sees them again", async () => {
  const dir = makeTempDir(["a.pdf", "b.pdf"]);
  try {
    const first = mockUploadPage("#file-upload");
    const step = { type: "upload", value: "{{downloaded_files_dir}}", _explicit_selector: "#file-upload" };
    await executeStep(first.page, step, { downloaded_files_dir: dir });
    assert.deepStrictEqual(first.calls, [[path.join(dir, "a.pdf"), path.join(dir, "b.pdf")]]);
    assert.deepStrictEqual(fs.readdirSync(dir), [], "a.pdf and b.pdf must be gone after upload");

    // Simulate C downloading a new file into the same run's shared folder.
    fs.writeFileSync(path.join(dir, "c.pdf"), "c");
    const second = mockUploadPage("#file-upload");
    await executeStep(second.page, step, { downloaded_files_dir: dir });
    assert.deepStrictEqual(second.calls, [[path.join(dir, "c.pdf")]], "only c.pdf, never a stale a.pdf/b.pdf");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// The cleanup above must never fire for a directory that didn't come from the compiler-controlled
// downloaded_files_dir placeholder — a hand-authored {{file_path}} pointed at a real folder is the
// user's own data and must survive the upload untouched.
test("an upload via a plain file_path directory never deletes its files", async () => {
  const dir = makeTempDir(["a.pdf", "b.pdf"]);
  try {
    const { page, calls } = mockUploadPage("#file-upload");
    const step = { type: "upload", value: "{{file_path}}", _explicit_selector: "#file-upload" };
    await executeStep(page, step, { file_path: dir });
    assert.deepStrictEqual(calls, [[path.join(dir, "a.pdf"), path.join(dir, "b.pdf")]]);
    assert.deepStrictEqual(fs.readdirSync(dir).sort(), ["a.pdf", "b.pdf"], "files must survive the upload");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// BUILD-103 (3): "the files were chosen" is read back from the file box, never assumed because the step ran.
function boxPage(keepsFiles) {
  const el = { multiple: true, files: [] };
  return {
    locator: () => ({
      count: async () => 1,
      first: () => ({
        setInputFiles: async (paths) => { el.files = keepsFiles ? paths.map((p) => ({ name: path.basename(p) })) : []; },
        evaluate: async (fn) => fn(el),
      }),
    }),
  };
}
const pick = (kind) => ({ type: "upload", value: "{{file_path}}", _explicit_selector: "#f", validation: { effect: { kind } } });

test("a file pick the page keeps is proven by the box holding exactly the chosen files", async () => {
  const dir = makeTempDir(["a.pdf", "b.pdf"]);
  try {
    const io = {};
    await executeStep(boxPage(true), pick("choose_files"), { file_path: dir }, io);
    assert.deepStrictEqual(io.filesChosen, { answer: "yes" });
    await assert.rejects(() => executeStep(boxPage(false), pick("choose_files"), { file_path: dir }, {}),
      /file box did not take the chosen files/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("a page that sends the files on pick and empties its box is not failed, only 'can't tell'", async () => {
  const dir = makeTempDir(["a.pdf"]);
  try {
    const io = {};
    await executeStep(boxPage(false), pick("upload"), { file_path: dir }, io);
    assert.deepStrictEqual(io.filesChosen, { answer: "cant_tell" });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("runPlan: a file pick the page keeps is proven by its box and needs no 'it finished' signal", async () => {
  const { runPlan } = require("../../app/run");
  const dir = makeTempDir(["a.pdf", "b.pdf"]);
  try {
    const pageOf = (keeps) => Object.assign(boxPage(keeps), {
      url: () => "https://app.test/upload", title: async () => "Upload", mainFrame: () => "main",
      context: () => ({ on: () => {} }), on: () => {}, waitForLoadState: async () => {}, waitForTimeout: async () => {},
      evaluate: async () => null, screenshot: async () => Buffer.from(""),
    });
    const stepOf = () => {
      const step = { ...pick("choose_files"), intent: "Choose the files" };
      step.validation.effect = { kind: "choose_files", required_rung: "accepted", target_rung: "accepted",
        quorum: { witnesses: 1 }, checks: [{ id: "c1", rung: "accepted", witness: "target", role: "support", probe: { type: "input_has_files" } }] };
      return step;
    };
    const events = [];
    const result = await runPlan(pageOf(true), [stepOf()], { file_path: dir }, 0, "slug-choose", { tracker: { emit: (c, d) => events.push([c, d]) } });
    const s = result.proof.steps[0];
    assert.deepStrictEqual([s.verdict, s.required], ["proven", "accepted"]);
    assert.deepStrictEqual(result.proof.notes, []);
    assert.ok(!events.some(([c]) => c === "completion"), "a pick starts no work: no upload wait");
    // A box that did not take the files is no proof, however the step ran.
    await assert.rejects(runPlan(pageOf(false), [stepOf()], { file_path: dir }, 0, "slug-choose-empty", { tracker: { emit: () => {} } }));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

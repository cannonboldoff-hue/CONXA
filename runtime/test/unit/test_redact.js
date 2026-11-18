"use strict";
// WF-8 secrets check: a form sent by GET put the password and token in the next page's address, and runtime.log wrote
// that address down. Every log line is scrubbed now.
const test = require("node:test");
const assert = require("node:assert");
const { scrubLine, scrubUrl, noteSecrets, isSecretInput } = require("../../app/redact");

test("secret-looking address values are replaced; everything else is kept byte for byte", () => {
  const url = "http://localhost:8128/secrets-done.html?dn=Ada&pw=ZzDummyPw%218812&tok=tok_DUMMY_Q7r2x9&country_code=US&code=x1#access_token=a&s=1";
  assert.strictEqual(scrubUrl(url),
    "http://localhost:8128/secrets-done.html?dn=Ada&pw=REDACTED&tok=REDACTED&country_code=US&code=REDACTED#access_token=REDACTED&s=1");
  assert.strictEqual(scrubUrl("https://shop.test/p/12?q=shoes+red"), "https://shop.test/p/12?q=shoes+red");
});

test("a secret run input's value is gone from every line, typed or inside an address", () => {
  noteSecrets({ user_password: "Hunter2Secret!", display_name: "Ada" });
  const line = JSON.stringify({ msg: "x", url: "https://a.test/?q=Hunter2Secret%21", note: "typed Hunter2Secret! for Ada" });
  const out = scrubLine(line);
  assert.ok(!out.includes("Hunter2Secret"), out);
  assert.ok(out.includes("Ada"));
  assert.ok(isSecretInput("api_token") && !isSecretInput("zip_code") && !isSecretInput("display_name"));
});

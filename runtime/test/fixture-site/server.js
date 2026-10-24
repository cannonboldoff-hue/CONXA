"use strict";
// A small hermetic web app for end-to-end tests: everything the conversational Studio loop has to
// survive in one place, with no network. Served over http (not file://) so cookies, downloads,
// iframes and popups behave as on a real site.
//
//   /signin                 form that sets a session cookie
//   /repo/:name             list page with a cookie banner and a link to the file
//   /file/:name             a file page whose download button lives inside an iframe
//   /frame/:name            the iframe document: a real <a download> to /raw/:name
//   /raw/:name              the file itself (attachment)
//   /submit                 an irreversible "Send" that renders a confirmation (#done)
//   /drive                  a Drive-shaped upload page: <input type=file>, #status lists what arrived
//   /decision               a "file already exists" dialog: Replace / Keep both
//
// start() resolves to { url, close }.

const http = require("http");

const page = (title, body) => `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title></head><body>${body}</body></html>`;

function route(req, res) {
  const url = new URL(req.url, "http://x");
  const parts = url.pathname.split("/").filter(Boolean);
  const send = (code, type, body, headers = {}) => { res.writeHead(code, { "content-type": type, ...headers }); res.end(body); };
  const html = (body, title = "Fixture") => send(200, "text/html; charset=utf-8", page(title, body));
  const name = decodeURIComponent(parts[1] || "");

  if (parts[0] === "signin") {
    if (req.method === "POST") return send(302, "text/plain", "", { location: "/repo/default", "set-cookie": "session=ok; Path=/" });
    return html(`<form method="post"><input name="user" aria-label="User"><button type="submit">Sign in</button></form>`, "Sign in");
  }
  if (parts[0] === "repo") {
    return html(`
      <div id="cookie-banner" role="dialog">We use cookies <button id="accept-cookies">Accept</button></div>
      <h1>Repository ${name}</h1>
      <ul><li data-row="file"><a data-testid="open-file" href="/file/${encodeURIComponent(name)}">${name}.gitignore</a></li></ul>
      <script>document.getElementById('accept-cookies').onclick = () => document.getElementById('cookie-banner').remove();</script>`, `Repo ${name}`);
  }
  if (parts[0] === "file") {
    return html(`<h1>${name}.gitignore</h1><iframe data-testid="raw-frame" src="/frame/${encodeURIComponent(name)}" width="400" height="120"></iframe>`, `File ${name}`);
  }
  if (parts[0] === "frame") {
    return html(`<a data-testid="download-btn" href="/raw/${encodeURIComponent(name)}" download="${name}.gitignore">Download raw file</a>`, "Frame");
  }
  if (parts[0] === "raw") {
    return send(200, "application/octet-stream", `# ${name}\n*.log\n`, { "content-disposition": `attachment; filename="${name}.gitignore"` });
  }
  if (parts[0] === "submit") {
    return html(`<button data-testid="send" id="send">Send</button><div id="done" hidden>Sent</div>
      <script>document.getElementById('send').onclick = () => { document.getElementById('done').hidden = false; };</script>`, "Submit");
  }
  if (parts[0] === "drive") {
    return html(`<input type="file" multiple data-testid="drive-upload"><div id="status">idle</div>
      <script>document.querySelector('[data-testid=drive-upload]').addEventListener('change', function () {
        document.getElementById('status').textContent = 'Uploaded: ' + Array.from(this.files).map((f) => f.name).sort().join(',');
      });</script>`, "Drive");
  }
  if (parts[0] === "decision") {
    return html(`<div role="alertdialog" id="exists"><p>A file with this name already exists.</p><button id="replace">Replace</button><button id="keep">Keep both</button></div>`, "Decision");
  }
  return send(404, "text/plain", "not found");
}

function start(port = 0) {
  return new Promise((resolve, reject) => {
    const server = http.createServer(route);
    server.on("error", reject);
    server.listen(port, "127.0.0.1", () => {
      resolve({ url: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((r) => server.close(r)) });
    });
  });
}

module.exports = { start };

if (require.main === module) {
  start(Number(process.env.PORT) || 0).then((s) => console.log(s.url));
}

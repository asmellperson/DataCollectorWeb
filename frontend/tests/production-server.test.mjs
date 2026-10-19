import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer, request } from "node:http";
import { gunzipSync } from "node:zlib";
import { createFrontendServer } from "../server.mjs";

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  return server.address().port;
}
async function close(server) {
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
function get(port, path, options = {}) {
  return new Promise((resolve, reject) => {
    const req = request(
      { host: "127.0.0.1", port, path, ...options },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () =>
          resolve({
            status: response.statusCode,
            headers: response.headers,
            body: Buffer.concat(chunks),
          }),
        );
      },
    );
    req.on("error", reject);
    req.end(options.body);
  });
}

test("production serving compresses assets, caches by hash, and streams API traffic", async () => {
  const root = await mkdtemp(join(tmpdir(), "dcw-static-"));
  await mkdir(join(root, "assets"));
  const script = "const production = true;\n".repeat(500);
  await writeFile(join(root, "index.html"), "<html>production</html>");
  await writeFile(join(root, "assets/app-hash.js"), script);
  const backend = createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      res.writeHead(201, {
        "Content-Type": "application/json",
        "X-Upstream": "yes",
      });
      res.end(
        JSON.stringify({
          method: req.method,
          path: req.url,
          body: Buffer.concat(chunks).toString(),
        }),
      );
    });
  });
  let server;
  try {
    const backendPort = await listen(backend);
    server = createFrontendServer({
      root,
      backend: `http://127.0.0.1:${backendPort}`,
    });
    const port = await listen(server);
    const asset = await get(port, "/assets/app-hash.js", {
      headers: { "Accept-Encoding": "gzip" },
    });
    assert.equal(asset.headers["content-encoding"], "gzip");
    assert.equal(gunzipSync(asset.body).toString(), script);
    assert.match(asset.headers["cache-control"], /immutable/);
    const cached = await get(port, "/assets/app-hash.js", {
      headers: { "If-None-Match": asset.headers.etag },
    });
    assert.equal(cached.status, 304);
    assert.equal(cached.body.length, 0);
    const html = await get(port, "/");
    assert.equal(html.headers["cache-control"], "no-cache");
    assert.equal((await get(port, "/assets/missing.js")).status, 404);
    assert.equal((await get(port, "/%2e%2e/server.mjs")).status, 404);
    assert.equal((await get(port, "/bad%")).status, 400);
    assert.equal(
      (
        await get(port, "/assets/app-hash.js", {
          headers: { "Accept-Encoding": "gzip;q=0" },
        })
      ).headers["content-encoding"],
      undefined,
    );
    const response = await get(port, "/api/collection/stop?camera_id=a", {
      method: "POST",
      body: '{"test":1}',
      headers: { "Content-Type": "application/json" },
    });
    assert.equal(response.status, 201);
    assert.deepEqual(JSON.parse(response.body), {
      method: "POST",
      path: "/api/collection/stop?camera_id=a",
      body: '{"test":1}',
    });
    const bytes = "x".repeat(1024 * 256);
    const upload = await get(port, "/api/upload", {
      method: "POST",
      body: bytes,
    });
    assert.equal(JSON.parse(upload.body).body.length, bytes.length);
    await close(backend);
    assert.equal((await get(port, "/api/health")).status, 502);
  } finally {
    if (server) await close(server);
    if (backend.listening) await close(backend);
    await rm(root, { recursive: true, force: true });
  }
});

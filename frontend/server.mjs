import { createServer, request as proxyRequest } from "node:http";
import { createReadStream, existsSync } from "node:fs";
import { stat } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { pipeline } from "node:stream";
import { createGzip } from "node:zlib";

const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

export function createFrontendServer({
  root = fileURLToPath(new URL("./dist", import.meta.url)),
  backend = "http://127.0.0.1:8000",
} = {}) {
  root = resolve(root);
  return createServer(async (req, res) => {
    if (
      req.url === "/api" ||
      req.url.startsWith("/api/") ||
      req.url.startsWith("/api?")
    ) {
      const target = new URL(backend);
      const upstream = proxyRequest(
        {
          hostname: target.hostname,
          port: target.port,
          path: req.url,
          method: req.method,
          headers: { ...req.headers, host: target.host },
        },
        (response) => {
          res.writeHead(response.statusCode, response.headers);
          pipeline(response, res, () => {});
        },
      );
      upstream.on("error", () => {
        if (!res.headersSent) {
          res.writeHead(502, {
            "Content-Type": "application/json; charset=utf-8",
          });
          res.end(JSON.stringify({ detail: "后端暂时不可用，请稍后重试" }));
        } else res.destroy();
      });
      req.on("aborted", () => upstream.destroy());
      res.on("close", () => {
        if (!res.writableEnded) upstream.destroy();
      });
      req.pipe(upstream);
      return;
    }
    if (!["GET", "HEAD"].includes(req.method)) {
      res.writeHead(405, { Allow: "GET, HEAD" });
      res.end();
      return;
    }
    try {
      const pathname = decodeURIComponent(req.url.split("?")[0]);
      if (pathname.includes("\0")) throw new URIError();
      let path = resolve(root, "." + pathname);
      if (path !== root && !path.startsWith(root + sep)) {
        res.writeHead(404);
        res.end();
        return;
      }
      if (pathname === "/" || !extname(pathname))
        path = resolve(root, "index.html");
      const info = await stat(path);
      if (!info.isFile()) {
        res.writeHead(404);
        res.end();
        return;
      }
      const extension = extname(path);
      const headers = {
        "Content-Type": types[extension] || "application/octet-stream",
        "Cache-Control": path.startsWith(resolve(root, "assets") + sep)
          ? "public, max-age=31536000, immutable"
          : "no-cache",
        ETag: `"${info.size.toString(16)}-${info.mtimeMs.toString(16)}"`,
        Vary: "Accept-Encoding",
      };
      if (
        (req.headers["if-none-match"] || "")
          .split(",")
          .some(
            (tag) =>
              tag.trim().replace(/^W\//, "") === headers.ETag ||
              tag.trim() === "*",
          )
      ) {
        res.writeHead(304, headers);
        res.end();
        return;
      }
      const gzip =
        info.size > 1024 &&
        [".js", ".css", ".html", ".svg", ".json"].includes(extension) &&
        (req.headers["accept-encoding"] || "")
          .split(",")
          .some(
            (item) =>
              /^gzip(?:\s*;|\s*$)/i.test(item.trim()) &&
              !/;\s*q=0(?:\.0*)?\s*$/i.test(item),
          );
      if (gzip) headers["Content-Encoding"] = "gzip";
      else headers["Content-Length"] = info.size;
      res.writeHead(200, headers);
      if (req.method === "HEAD") {
        res.end();
        return;
      }
      const input = createReadStream(path);
      if (gzip) pipeline(input, createGzip(), res, () => {});
      else pipeline(input, res, () => {});
    } catch (error) {
      res.writeHead(error instanceof URIError ? 400 : 404);
      res.end();
    }
  });
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  if (!existsSync(new URL("./dist/index.html", import.meta.url))) {
    throw new Error("缺少前端构建，请先执行 npm run build");
  }
  const server = createFrontendServer();
  server.listen(
    Number(process.env.DCW_FRONTEND_PORT || 5180),
    "0.0.0.0",
    () => {
      console.log(`Production frontend listening on ${server.address().port}`);
    },
  );
  for (const signal of ["SIGINT", "SIGTERM"])
    process.once(signal, () => {
      server.close();
      server.closeIdleConnections();
    });
}

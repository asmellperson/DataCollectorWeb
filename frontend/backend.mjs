import { fileURLToPath } from "node:url";

export const backendSocket =
  process.env.DCW_BACKEND_SOCKET ||
  fileURLToPath(new URL("../config/run/backend.sock", import.meta.url));

export const backendPaths = ["/api", "/docs", "/redoc", "/openapi.json"];

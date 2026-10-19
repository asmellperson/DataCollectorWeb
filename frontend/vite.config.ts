import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { backendPaths, backendSocket } from "./backend.mjs";
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-dom/client"],
          konva: ["konva", "react-konva"],
        },
      },
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5180,
    strictPort: true,
    proxy: Object.fromEntries(
      backendPaths.map((path) => [
        path,
        {
          target: {
            protocol: "http:",
            host: "localhost",
            port: 80,
            socketPath: backendSocket,
          },
        },
      ]),
    ),
  },
});

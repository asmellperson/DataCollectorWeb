import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
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
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});

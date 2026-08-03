import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND = process.env.VITE_BACKEND_ORIGIN || "http://localhost:8001";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    allowedHosts: true,
    hmr: { clientPort: 443, protocol: "wss" },
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
    },
  },
});

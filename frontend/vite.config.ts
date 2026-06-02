import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    allowedHosts: ["localhost", "127.0.0.1", "frontend"],
    proxy: {
      "/api/v1": "http://backend:8000",
      "/health": "http://backend:8000",
      "/ready": "http://backend:8000"
    }
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/testSetup.ts"
  }
});

import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The demo talks to the backend through the dev-server proxy, so the API needs
// no CORS setup. Point VITE_API_TARGET elsewhere if the backend isn't on
// localhost:8000.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  const apiTarget = env.VITE_API_TARGET ?? "http://localhost:8000";

  return {
    plugins: [react()],
    server: {
      host: true, // reachable from outside the container
      port: 5173,
      // Bind-mounted source on Docker Desktop doesn't deliver fs events.
      watch: env.VITE_USE_POLLING ? { usePolling: true } : undefined,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
  };
});

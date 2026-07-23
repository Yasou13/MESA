import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 20_000,
  fullyParallel: false,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:18765",
    channel: "chrome",
    viewport: { width: 1440, height: 1000 },
    colorScheme: "dark",
  },
  webServer: {
    command:
      "../../.venv/bin/mesa-benchmark dashboard --host 127.0.0.1 --port 18765 --results-root /tmp/mesa-benchmark-console-e2e",
    url: "http://127.0.0.1:18765/api/health",
    reuseExistingServer: true,
    timeout: 20_000,
  },
});

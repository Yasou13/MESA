import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import App from "./App";

vi.stubGlobal(
  "fetch",
  vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.endsWith("/api/catalog")
      ? { clients: [], datasets: [], profiles: [] }
      : url.endsWith("/api/settings/ollama")
        ? {
            url: null,
            model: null,
            source: "environment",
            online: false,
            models: [],
            error: null,
          }
      : url.endsWith("/api/system")
        ? {
            cpu_percent: 0,
            memory_percent: 0,
            disk_percent: 0,
            ollama: { online: false, model: null, latency_ms: null },
            gpu: null,
          }
        : [];
    return { ok: true, json: async () => payload };
  }),
);

Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
  configurable: true,
  value: vi.fn(() => null),
});

describe("MESA Benchmark Console", () => {
  it("renders the primary navigation", async () => {
    render(<App />);
    await screen.findByText("Kontrol sende, gürültü değil");
    expect(
      screen.getByRole("button", { name: "⌂ Genel Bakış" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "+ Yeni Benchmark" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "⌁ Sonuçlar" }),
    ).toBeInTheDocument();
  });
});

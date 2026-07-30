import { expect, test } from "@playwright/test";

test("benchmark wizard and operational navigation render", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("MESA", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: /Yeni Benchmark/ }).click();
  await expect(
    page.getByRole("heading", { name: "Ne ölçmek istiyorsun?" }),
  ).toBeVisible();
  await expect(page.getByText("Quality", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Dataset seç/ }).click();
  await expect(
    page.getByRole("heading", { name: "Hangi dataset kullanılacak?" }),
  ).toBeVisible();
  await expect(page.getByText("Mini Smoke", { exact: true })).toBeVisible();
});

test("local API and system surfaces stay available", async ({ page, request }) => {
  const health = await request.get("/api/health");
  expect(health.ok()).toBeTruthy();
  await page.goto("/");
  await page.getByRole("button", { name: /Sistem/ }).click();
  await expect(page.getByText("MODEL SERVİSİ")).toBeVisible();
  await expect(page.getByText("CPU", { exact: true }).first()).toBeVisible();
});

test("connection, dataset inspection and guide are discoverable", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Ollama/ }).click();
  await expect(page.getByRole("heading", { name: "Ollama bağlantısı" })).toBeVisible();
  await expect(page.getByPlaceholder("http://192.168.1.103:11434")).toBeVisible();
  await page.getByRole("button", { name: "Kapat" }).click();

  await page.getByRole("button", { name: /Datasetler/ }).click();
  await page.getByRole("button", { name: /Mini Smoke/ }).click();
  await expect(page.getByText(/Sistemin uçtan uca çalıştığını/)).toBeVisible();
  await expect(page.getByText("İÇERİK ÖRNEĞİ")).toBeVisible();

  await page.getByRole("button", { name: /Rehber/ }).click();
  await expect(
    page.getByRole("heading", { name: "Benchmark neyi kanıtlar, neyi kanıtlamaz?" }),
  ).toBeVisible();
  await expect(page.getByText("METRİK SÖZLÜĞÜ")).toBeVisible();
});

/**
 * End-to-end flow against the LIVE stack. Every assertion checks real backend output; when the
 * local model is unavailable the request step asserts the explicit MODEL_UNAVAILABLE state instead
 * of pretending an answer exists.
 */
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

const ROOT = path.resolve(HERE, "../..");
const SHOTS = path.join(HERE, "screenshots");
const adminKey = JSON.parse(fs.readFileSync(path.join(ROOT, "var/dev_keys.json"), "utf8")).keys.admin.key as string;
fs.mkdirSync(SHOTS, { recursive: true });

test("NanoGate console: real request → receipt → cache → privacy → lab → finops → infrastructure", async ({ page, request }) => {
  const modelUp = (await (await request.get("/healthz")).json()).components.model.ok as boolean;

  // 1. Login / auth
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in to the console" })).toBeVisible();
  await page.fill("#apikey", adminKey);
  await page.getByRole("button", { name: "Continue" }).click();

  // 2. Overview
  await expect(page.getByRole("heading", { name: /Good (morning|afternoon|evening), Dev/ })).toBeVisible();
  await expect(page.getByTestId("kpi-requests")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/01-overview.png`, fullPage: true });

  // 3. Send a real request (Local intelligence prompt) through the pipeline
  await page.getByRole("button", { name: "Try it" }).click();
  await page.getByRole("button", { name: "Local intelligence" }).click();
  await page.getByTestId("send-request").click();
  const result = page.getByTestId("composer-result");
  await expect(result).toBeVisible({ timeout: 180_000 });
  if (modelUp) await expect(result).toContainText(/LOCAL_|CACHE_|ROUTER_/);
  else await expect(result).toContainText("MODEL_UNAVAILABLE");
  await page.screenshot({ path: `${SHOTS}/02-request-sent.png`, fullPage: true });

  // 4. The real request appears in the live decision stream; 5. open its receipt
  const stream = page.getByTestId("decision-stream");
  await expect(stream.locator("tbody tr").first()).toBeVisible();
  await stream.locator("tbody tr").first().click();
  await expect(page.getByRole("dialog", { name: "Decision receipt" })).toBeVisible();
  await page.getByRole("link", { name: /Full receipt/ }).click();
  await expect(page.getByRole("heading", { name: "Decision Receipt" })).toBeVisible();
  await page.getByTestId("verify-receipt").click();
  await expect(page.getByTestId("seal-state")).toContainText("SEALED", { timeout: 20_000 });
  await page.getByTestId("tamper-demo").click();
  await expect(page.getByText("INTEGRITY FAILURE DETECTED")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/03-receipt.png`, fullPage: true });

  // 6. Cache paraphrase (real embedding + verifier)
  await page.goto("/cache");
  await page.getByRole("button", { name: "True paraphrase" }).click();
  await page.getByTestId("cache-test").click();
  await expect(page.getByTestId("cache-result")).toContainText("CACHE_VERIFIED");
  await page.screenshot({ path: `${SHOTS}/04-cache-paraphrase.png`, fullPage: true });

  // 7. Hard negative
  await page.getByRole("button", { name: "Hard negative" }).click();
  await page.getByTestId("cache-test").click();
  await expect(page.getByTestId("cache-result")).toContainText("CACHE_HARD_NEGATIVE");
  await expect(page.getByTestId("cache-result")).toContainText("ownership");
  await page.screenshot({ path: `${SHOTS}/05-cache-hard-negative.png`, fullPage: true });

  // 8. PII case: HR policy evaluation removes every remote path
  await page.goto("/policies");
  await page.getByRole("button", { name: "HR with PII" }).click();
  await page.getByTestId("policy-test").click();
  const pr = page.getByTestId("policy-result");
  await expect(pr).toContainText("SENSITIVE_LOCAL_ONLY");
  await expect(pr).toContainText("0 bytes may leave device");
  await page.screenshot({ path: `${SHOTS}/06-policy-pii.png`, fullPage: true });

  // 9. Router Lab (artifacts or explicit unavailable state)
  await page.goto("/router-lab");
  await expect(page.getByRole("heading", { name: "Router Lab" })).toBeVisible();
  const hasRouter = await page.getByTestId("threshold-slider").count();
  if (hasRouter) {
    await page.getByTestId("threshold-slider").fill("0.3");
    await expect(page.getByTestId("threshold-preview")).toContainText("Projected local coverage");
  } else {
    await expect(page.getByText("Router artifacts not trained yet")).toBeVisible();
  }
  await page.screenshot({ path: `${SHOTS}/07-router-lab.png`, fullPage: true });

  // 10. FinOps: measured and scenario are separate
  await page.goto("/finops");
  await expect(page.getByTestId("finops-measured")).toBeVisible();
  await page.getByRole("tab", { name: "Scenario" }).click();
  await expect(page.getByTestId("finops-scenario")).toContainText("Scenario assumption — not a measurement");
  await page.screenshot({ path: `${SHOTS}/08-finops-scenario.png`, fullPage: true });

  // 11. Infrastructure: actual telemetry or explicit unavailable
  await page.goto("/infrastructure");
  await expect(page.getByText("HP ZGX Nano G1n AI Station").first()).toBeVisible();
  await expect(page.getByText("NVML").first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/09-infrastructure.png`, fullPage: true });
});

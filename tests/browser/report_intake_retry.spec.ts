import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { readFile, writeFile, symlink } from "node:fs/promises";
import { join } from "node:path";

const password = "Synthetic-Retry-UI-Password-42";
type Handoff = { email: string; personId: string; intakeId: string; reportId: string };

async function register(page: Page, email: string, name: string) {
  await page.goto("/");
  const form = page.getByTestId("register-form");
  await form.getByLabel("Email").fill(email);
  await form.getByLabel("Password").fill(password);
  await form.getByLabel("Your display name").fill(name);
  await form.getByRole("button", { name: "Register securely" }).click();
  await expect(page.getByTestId("session-email")).toHaveText(email);
}

async function postRetry(context: BrowserContext, personId: string, intakeId: string, content: Buffer) {
  const csrf = (await context.cookies()).find((cookie) => cookie.name === "healthy_csrf")?.value ?? "";
  return context.request.post(`/api/v1/persons/${personId}/report-intakes/${intakeId}/retry`, {
    headers: { Origin: "http://127.0.0.1:3000", "X-CSRF-Token": csrf },
    multipart: { file: { name: "synthetic-scan.pdf", mimeType: "application/pdf", buffer: content } },
  });
}

async function downloadSummary(page: Page) {
  const pending = page.waitForEvent("download");
  await page.getByTestId("summary-export-link").click();
  const download = await pending;
  return readFile((await download.path())!, "utf8");
}

test("retry UI restores the failed ID and confirms only human-corrected Chinese data", async ({ page, context, browser }) => {
  test.setTimeout(90_000);
  const scan = process.env.HEALTHY_TEST_SCANNED_PDF_PATH;
  const wrong = process.env.HEALTHY_TEST_WRONG_RETRY_PDF_PATH;
  const modelSource = process.env.HEALTHY_TEST_RETRY_MODEL_SOURCE;
  const modelDirectory = process.env.HEALTHY_OCR_TESSDATA_DIR;
  const handoffPath = process.env.HEALTHY_TEST_RETRY_STATE_PATH;
  test.skip(!scan || !wrong || !modelSource || !modelDirectory || !handoffPath, "Requires disposable synthetic retry fixtures and an isolated test model directory");
  await page.setViewportSize({ width: 390, height: 844 });
  const email = `retry-ui-${crypto.randomUUID()}@example.com`;
  await register(page, email, "合成重試人物");
  const personId = (await page.getByTestId("person-card").first().getAttribute("data-person-id"))!;
  await page.getByTestId("person-card").first().click();
  await page.getByTestId("reports-link").click();
  await page.getByTestId("report-file-input").setInputFiles(scan!);
  const firstUpload = page.waitForResponse((response) => response.url().endsWith("/report-intakes") && response.request().method() === "POST");
  await page.getByTestId("report-upload-button").click();
  const failed = await (await firstUpload).json();
  expect(failed.status).toBe("failed");
  expect(failed.error_message).toContain("requires local eng and chi_tra");
  await expect(page.getByTestId("report-retry-section")).toBeVisible();
  await expect(page.getByTestId("report-retry-button")).toBeDisabled();

  await page.getByTestId("report-file-input").setInputFiles(wrong!);
  await page.getByTestId("report-retry-button").click();
  await expect(page.getByTestId("report-intake-error")).toContainText("original file");
  await expect(page.getByTestId("report-retry-section")).toBeVisible();
  // Restore only this test's missing model link. The installed model stays unchanged.
  await symlink(join(modelSource!, "chi_tra.traineddata"), join(modelDirectory!, "chi_tra.traineddata"));
  await page.getByTestId("report-file-input").setInputFiles(scan!);
  const retried = page.waitForResponse((response) => response.url().endsWith(`/${failed.id}/retry`));
  await page.getByTestId("report-retry-button").click();
  const recovered = await (await retried).json();
  expect(recovered.id).toBe(failed.id);
  expect(recovered.status).toBe("pending_review");
  await expect(page.getByTestId("report-review-section")).toBeVisible({ timeout: 35_000 });
  await expect(page.getByTestId("report-candidate-value").first()).toHaveValue("92");
  await expect(page.getByTestId("report-review-section")).toContainText("ocr_pdf:page-");
  const beforeConfirmation = await context.request.get(`/api/v1/persons/${personId}/summary-export`);
  expect(await beforeConfirmation.text()).toContain("No confirmed lab report");
  expect((await postRetry(context, personId, failed.id, await readFile(scan!))).status()).toBe(409);

  await page.getByTestId("report-candidate-value").first().fill("93.25");
  await page.getByTestId("report-review-save").click();
  await expect(page.getByTestId("report-intake-message")).toContainText("saved");
  await page.reload();
  await expect(page.getByTestId("report-candidate-value").first()).toHaveValue("93.25");
  await page.getByTestId("report-file-input").setInputFiles(scan!);
  await page.getByTestId("report-upload-button").click();
  await expect(page.getByTestId("report-candidate-value").first()).toHaveValue("93.25");
  await page.getByTestId("report-review-confirm").click();
  await expect(page.getByTestId("confirmed-health-report")).toContainText("93.25");
  await expect(page.getByTestId("report-retry-button")).toHaveCount(0);
  const rows = await (await context.request.get(`/api/v1/persons/${personId}/report-intakes`)).json();
  expect(rows).toHaveLength(1);
  expect(rows[0].id).toBe(failed.id);
  expect(rows[0].status).toBe("confirmed");
  expect((await postRetry(context, personId, failed.id, await readFile(scan!))).status()).toBe(409);
  await page.getByTestId("report-file-input").setInputFiles(scan!);
  await page.getByTestId("report-upload-button").click();
  await expect(page.getByTestId("confirmed-health-report")).toContainText("93.25");
  await expect(page.getByTestId("report-intake-message")).toContainText("already confirmed");
  await page.getByTestId("confirmed-report-history-link").click();
  await expect(page.getByTestId("history-item")).toContainText("93.25");
  const html = await downloadSummary(page);
  expect(html).toContain("93.2500");
  expect(html).toContain("血糖");
  expect(html).toContain("65-99");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  await page.goto("/");
  const personForm = page.getByTestId("person-form");
  await personForm.getByLabel("Display name").fill("另一合成人物");
  await personForm.getByLabel("Relationship").selectOption("child");
  await personForm.getByRole("button", { name: "Create Person" }).click();
  const childId = (await page.getByTestId("person-card").filter({ hasText: "另一合成人物" }).getAttribute("data-person-id"))!;
  expect((await postRetry(context, childId, failed.id, await readFile(scan!))).status()).toBe(404);
  const other = await browser.newContext();
  try {
    await register(await other.newPage(), `foreign-retry-${crypto.randomUUID()}@example.com`, "Foreign synthetic owner");
    expect((await postRetry(other, personId, failed.id, await readFile(scan!))).status()).toBe(404);
    expect((await other.request.get(`/api/v1/persons/${personId}/summary-export`)).status()).toBe(404);
  } finally { await other.close(); }
  await writeFile(handoffPath!, JSON.stringify({ email, personId, intakeId: failed.id, reportId: rows[0].report_id }), { mode: 0o600 });
});

test("after real service restart the reviewed report persists in History and downloaded summary", async ({ page, context }) => {
  const handoffPath = process.env.HEALTHY_TEST_RETRY_STATE_PATH;
  test.skip(!handoffPath, "Requires the synthetic handoff from the preceding service run");
  const state = JSON.parse(await readFile(handoffPath!, "utf8")) as Handoff;
  await page.goto("/");
  const login = page.getByTestId("login-form");
  await login.getByLabel("Email").fill(state.email);
  await login.getByLabel("Password").fill(password);
  await login.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("session-email")).toHaveText(state.email);
  await page.goto(`/reports?person_id=${state.personId}`);
  await expect(page.getByTestId("confirmed-health-report")).toContainText("93.25");
  await expect(page.getByTestId("report-retry-button")).toHaveCount(0);
  const rows = await (await context.request.get(`/api/v1/persons/${state.personId}/report-intakes`)).json();
  expect(rows).toHaveLength(1);
  expect(rows[0].id).toBe(state.intakeId);
  expect(rows[0].report_id).toBe(state.reportId);
  await page.getByTestId("confirmed-report-history-link").click();
  await expect(page.getByTestId("history-item")).toContainText("93.25");
  const html = await downloadSummary(page);
  expect(html).toContain("93.2500");
  expect(html).toContain("血糖");
  expect(html).toContain("mg/dL");
});

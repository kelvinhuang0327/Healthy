import { expect, test, type Page } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const originalPassword = "Synthetic-Original-Password-42";
const nextPassword = "Synthetic-Replacement-Password-91";

for (const scenario of [
  { width: 1280, height: 720, name: "合成摘要人物 <script>window.exportInjected=true</script>" },
  { width: 960, height: 900, name: "合成摘要人物 <script>window.exportInjected=true</script>" },
  { width: 1280, height: 720, name: "Person".repeat(20) },
  { width: 390, height: 844, name: "Person".repeat(20) },
]) {
  test(`person selection stays inside its card at ${scenario.width}px with ${scenario.name.length} characters`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: scenario.width, height: scenario.height });
    await register(page, scenario.name);
    const primary = page.getByTestId("person-card").first();
    await primary.click({ timeout: 5_000 });
    const form = page.getByTestId("person-form");
    await form.getByLabel("Display name").fill("另一合成人物");
    await form.getByLabel("Relationship").selectOption("child");
    await form.getByRole("button", { name: "Create Person" }).click();
    const child = page.getByTestId("person-card").filter({ hasText: "另一合成人物" });
    await expect(child).toBeVisible();
    await child.scrollIntoViewIfNeeded();
    const layout = await child.evaluate((element) => {
      const rectangle = (node: Element) => {
        const { left, right, top, bottom, width, height } = node.getBoundingClientRect();
        return { left, right, top, bottom, width, height };
      };
      const target = rectangle(element);
      const card = rectangle(element.closest("article")!);
      const hit = document.elementFromPoint(target.left + target.width / 2, target.top + target.height / 2);
      return {
        target, card,
        hit: hit?.closest("[data-testid]")?.getAttribute("data-testid"),
        font: getComputedStyle(element).fontFamily,
        personTrack: getComputedStyle(element.parentElement!).gridTemplateColumns,
        pageWidth: document.documentElement.scrollWidth,
        viewportWidth: innerWidth,
      };
    });
    await writeFile(testInfo.outputPath("person-layout.json"), JSON.stringify(layout, null, 2));
    await page.screenshot({ path: testInfo.outputPath("person-layout.png"), fullPage: true });
    await child.click({ timeout: 5_000 });
    await expect(child).toHaveAttribute("aria-pressed", "true");
    await expect(primary).toHaveAttribute("aria-pressed", "false");
    await expect(page.getByTestId("summary-export-link")).toHaveAttribute("href", `/api/v1/persons/${await child.getAttribute("data-person-id")}/summary-export`);
    expect(layout.target.right).toBeLessThanOrEqual(layout.card.right);
    expect(layout.hit).toBe("person-card");
    expect(layout.pageWidth).toBeLessThanOrEqual(layout.viewportWidth);
    await primary.click({ timeout: 5_000 });
    await expect(primary).toHaveAttribute("aria-pressed", "true");
  });
}

async function signIn(page: Page, email: string, password: string) {
  await page.goto("/");
  const form = page.getByTestId("login-form");
  await form.getByLabel("Email").fill(email);
  await form.getByLabel("Password").fill(password);
  await form.getByRole("button", { name: "Sign in" }).click();
}

async function register(page: Page, name: string): Promise<string> {
  const email = `account-summary-${crypto.randomUUID()}@example.com`;
  await page.goto("/");
  const form = page.getByTestId("register-form");
  await form.getByLabel("Email").fill(email);
  await form.getByLabel("Password").fill(originalPassword);
  await form.getByLabel("Your display name").fill(name);
  await form.getByRole("button", { name: "Register securely" }).click();
  await expect(page.getByTestId("session-email")).toHaveText(email);
  return email;
}

test("password errors recover, success signs out every session, and only the new password works", async ({ browser }) => {
  const first = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const other = await browser.newContext();
  const page = await first.newPage();
  const otherPage = await other.newPage();
  try {
    const email = await register(page, "Synthetic password owner");
    await signIn(otherPage, email, originalPassword);
    await expect(otherPage.getByTestId("session-email")).toHaveText(email);
    await page.getByRole("link", { name: "Account settings" }).click();
    const form = page.getByTestId("password-change-form");
    await form.getByLabel("Current password").fill(originalPassword);
    await form.getByLabel("New password", { exact: true }).fill(nextPassword);
    await form.getByLabel("Confirm new password").fill("Synthetic-Mismatch-Password");
    await form.getByRole("button").click();
    await expect(page.getByTestId("password-error")).toContainText("must match");
    await form.getByLabel("Confirm new password").fill(nextPassword);
    await form.getByLabel("Current password").fill("Synthetic-Wrong-Password");
    await form.getByRole("button").click();
    await expect(page.getByTestId("password-error")).toContainText("Current password");
    expect((await other.request.get("http://127.0.0.1:3000/api/v1/session")).status()).toBe(200);
    await form.getByLabel("Current password").fill(originalPassword);
    await form.getByRole("button").click();
    await expect(page.getByTestId("password-changed")).toContainText("All previous sessions");
    expect((await first.cookies()).some((cookie) => cookie.name === "healthy_session")).toBe(false);
    expect((await other.request.get("http://127.0.0.1:3000/api/v1/session")).status()).toBe(401);
    expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain(nextPassword);
    await signIn(page, email, originalPassword);
    await expect(page.getByTestId("login-form")).toBeVisible();
    await expect(page.getByTestId("session-email")).toHaveCount(0);
    await signIn(page, email, nextPassword);
    await expect(page.getByTestId("session-email")).toHaveText(email);
    await page.getByRole("link", { name: "Account settings" }).click();
    await expect(page.getByTestId("password-change-form")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally {
    await first.close();
    await other.close();
  }
});

test("Today and History download the selected person's escaped UTF-8 summary with no cross-person data", async ({ page, context, browser }, testInfo) => {
  await register(page, "合成摘要人物 <script>window.exportInjected=true</script>");
  const primary = page.getByTestId("person-card").first();
  const personId = await primary.getAttribute("data-person-id");
  expect(personId).toBeTruthy();
  await primary.click();
  const metric = page.getByTestId("metric-form");
  await metric.locator('input[name="weight_kg"]').fill("61.25");
  await metric.locator('[name="note"]').fill("中文合成備註 <img src=\"https://external.invalid\">");
  await metric.getByRole("button", { name: "Save metric" }).click();
  await expect(page.getByTestId("metric-list").getByTestId("metric-card")).toHaveCount(1);
  const responsePromise = page.waitForResponse((response) => response.url().endsWith(`/persons/${personId}/summary-export`));
  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("summary-export-link").click();
  const download = await downloadPromise;
  const response = await responsePromise;
  expect(response.headers()["cache-control"]).toBe("no-store");
  expect(response.headers()["content-security-policy"]).toContain("default-src 'none'");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  expect(download.suggestedFilename()).toMatch(new RegExp(`^healthy-summary-${personId}-.*\\.html$`));
  const path = testInfo.outputPath(download.suggestedFilename());
  await download.saveAs(path);
  const html = await readFile(path, "utf8");
  expect(html).toContain("中文合成備註");
  expect(html).toContain("61.25");
  expect(html).toContain("&lt;script&gt;");
  expect(html).not.toContain("<img");
  expect(html).not.toContain(originalPassword);
  const local = await context.newPage();
  const externalRequests: string[] = [];
  local.on("request", (request) => { if (request.url().startsWith("http")) externalRequests.push(request.url()); });
  await local.goto(pathToFileURL(path).href);
  await expect(local.getByRole("heading", { name: "Healthy health summary" })).toBeVisible();
  expect(await local.evaluate(() => "exportInjected" in window)).toBe(false);
  expect(externalRequests).toEqual([]);
  await local.setViewportSize({ width: 390, height: 844 });
  expect(await local.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await local.close();

  const personForm = page.getByTestId("person-form");
  await personForm.getByLabel("Display name").fill("另一合成人物");
  await personForm.getByLabel("Relationship").selectOption("child");
  await personForm.getByRole("button", { name: "Create Person" }).click();
  const child = page.getByTestId("person-card").filter({ hasText: "另一合成人物" });
  await child.click();
  const childId = await child.getAttribute("data-person-id");
  await expect(page.getByTestId("summary-export-link")).toHaveAttribute("href", `/api/v1/persons/${childId}/summary-export`);
  await page.goto(`/history?person_id=${childId}`);
  await expect(page.getByTestId("history-page")).toContainText("另一合成人物");
  await page.reload();
  const childDownloadPromise = page.waitForEvent("download");
  await page.getByTestId("summary-export-link").click();
  const childHtml = await readFile((await (await childDownloadPromise).path())!, "utf8");
  expect(childHtml).toContain("另一合成人物");
  expect(childHtml).toContain("No recorded metrics");
  expect(childHtml).not.toContain("中文合成備註");
  expect(childHtml).toContain("insufficient data");
  const foreign = await browser.newContext();
  try {
    await register(await foreign.newPage(), "Foreign synthetic owner");
    expect((await foreign.request.get(`http://127.0.0.1:3000/api/v1/persons/${personId}/summary-export`)).status()).toBe(404);
  } finally { await foreign.close(); }
});

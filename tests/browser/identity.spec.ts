import { expect, test, type BrowserContext, type Page } from "@playwright/test";

const password = "Synthetic-Only-Password-42";

async function register(
  page: Page,
  email: string,
  displayName: string,
): Promise<string> {
  await page.goto("/");
  await page.getByTestId("register-form").getByLabel("Email").fill(email);
  await page.getByTestId("register-form").getByLabel("Password").fill(password);
  await page
    .getByTestId("register-form")
    .getByLabel("Your display name")
    .fill(displayName);
  await page.getByRole("button", { name: "Register securely" }).click();
  await expect(page.getByText("Session active")).toBeVisible();
  const defaultPerson = page.getByTestId("person-card").first();
  await expect(defaultPerson).toContainText(displayName);
  await expect(defaultPerson).toContainText("Default Person");
  return (await defaultPerson.getAttribute("data-person-id")) ?? "";
}

async function assertNoCredentialStorage(context: BrowserContext, page: Page) {
  const storage = await page.evaluate(() => ({
    local: { ...localStorage },
    session: { ...sessionStorage },
    text: document.body.innerText,
  }));
  const browserStorage = JSON.stringify({
    local: storage.local,
    session: storage.session,
  });
  expect(browserStorage).not.toMatch(
    /(access_token|session_token|token_type|bearer|healthy_session)/i,
  );
  expect(storage.text).not.toContain("access_token");
  expect(storage.text).not.toContain("bearer");

  const cookies = await context.cookies();
  const sessionCookie = cookies.find((cookie) => cookie.name === "healthy_session");
  expect(sessionCookie).toBeDefined();
  expect(sessionCookie?.httpOnly).toBe(true);
  expect(sessionCookie?.sameSite).toBe("Lax");
}

test("register, manage Persons, logout, login, and deny foreign Person access", async ({
  browser,
}) => {
  const marker = Date.now();
  const consoleErrors: string[] = [];
  const resourceConsoleErrors: string[] = [];
  const expectedHttpFailures: string[] = [];
  const credentialBodies: string[] = [];

  const foreignContext = await browser.newContext();
  const foreignPage = await foreignContext.newPage();
  const foreignPersonId = await register(
    foreignPage,
    `foreign-${marker}@example.com`,
    "Foreign Synthetic Person",
  );
  expect(foreignPersonId).not.toBe("");
  await foreignContext.close();

  const context = await browser.newContext();
  const page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") {
      if (message.text().startsWith("Failed to load resource:")) {
        resourceConsoleErrors.push(message.text());
      } else {
        consoleErrors.push(message.text());
      }
    }
  });
  page.on("response", async (response) => {
    if (response.url().includes("/api/v1/")) {
      const text = await response.text().catch(() => "");
      if (
        /(access_token|session_token|token_type|bearer)/i.test(text) ||
        text.includes(password)
      ) {
        credentialBodies.push(text);
      }
      if ([401, 404].includes(response.status())) {
        expectedHttpFailures.push(
          `${response.status()} ${new URL(response.url()).pathname}`,
        );
      }
    }
  });

  const email = `owner-${marker}@example.com`;
  await register(page, email, "Owner Synthetic Person");
  await assertNoCredentialStorage(context, page);

  await page.getByTestId("person-form").getByLabel("Display name").fill("Child Person");
  await page.getByTestId("person-form").getByLabel("Relationship").selectOption("child");
  await page.getByRole("button", { name: "Create Person" }).click();
  await expect(page.getByTestId("person-card")).toHaveCount(2);
  await expect(page.getByText("Child Person")).toBeVisible();

  const foreignStatus = await page.evaluate(async (personId) => {
    const response = await fetch(`/api/v1/persons/${personId}`, {
      credentials: "same-origin",
    });
    return response.status;
  }, foreignPersonId);
  expect(foreignStatus).toBe(404);

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByTestId("login-form")).toBeVisible();

  await page.getByTestId("login-form").getByLabel("Email").fill(email);
  await page.getByTestId("login-form").getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("session-email")).toHaveText(email);
  await expect(page.getByTestId("person-card")).toHaveCount(2);
  await assertNoCredentialStorage(context, page);

  expect(credentialBodies).toEqual([]);
  expect(consoleErrors).toEqual([]);
  expect(expectedHttpFailures.sort()).toEqual([
    "401 /api/v1/notification-capabilities",
    "401 /api/v1/persons",
    "401 /api/v1/session",
    `404 /api/v1/persons/${foreignPersonId}`,
  ]);
  expect(resourceConsoleErrors).toHaveLength(expectedHttpFailures.length);
  await context.close();
});

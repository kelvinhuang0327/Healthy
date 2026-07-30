import { expect, test, type Page } from "@playwright/test";

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
  return (await defaultPerson.getAttribute("data-person-id")) ?? "";
}

test("selected Person actions reload from the API and complete idempotently", async ({
  page,
}) => {
  const marker = Date.now();
  const email = `actions-owner-${marker}@example.com`;
  const defaultPersonId = await register(page, email, "Actions Owner");
  expect(defaultPersonId).not.toBe("");

  await page.getByTestId("person-card").first().click();
  await expect(page.getByTestId("selected-person-pill")).toBeVisible();

  const form = page.getByTestId("action-form");
  await form.getByLabel("Title").fill("Evening walk");
  await form.getByLabel("Description").fill("Walk around the neighborhood");
  await form.getByLabel("Due at").fill("2026-08-01T18:30");
  await form.getByRole("button", { name: "Create action" }).click();

  const actionList = page.getByTestId("action-list");
  await expect(actionList.getByTestId("action-card")).toHaveCount(1);
  let actionCard = actionList.getByTestId("action-card").first();
  await expect(actionCard).toContainText("Evening walk");
  await expect(actionCard).toContainText("Walk around the neighborhood");
  await expect(actionCard).toContainText("Status: todo");
  const actionId = (await actionCard.getAttribute("data-action-id")) ?? "";
  expect(actionId).not.toBe("");

  const apiAction = await page.evaluate(async (personId) => {
    const response = await fetch(`/api/v1/persons/${personId}/actions`, {
      credentials: "same-origin",
    });
    return (await response.json())[0] as {
      id: string;
      description: string;
      due_at: string;
    };
  }, defaultPersonId);
  expect(apiAction.id).toBe(actionId);
  expect(apiAction.description).toBe("Walk around the neighborhood");
  expect(apiAction.due_at).toBe("2026-08-01T10:30:00Z");
  const displayedDueAt = await page.evaluate(
    (dueAt) => new Date(dueAt).toLocaleString(),
    apiAction.due_at,
  );
  await expect(actionCard).toContainText(displayedDueAt);

  await page.reload();
  actionCard = page.getByTestId("action-card").first();
  await expect(actionCard).toHaveAttribute("data-action-id", actionId);
  await expect(actionCard).toContainText("Walk around the neighborhood");

  await actionCard.getByRole("button", { name: "Complete action" }).click();
  actionCard = page.getByTestId("action-card").first();
  await expect(actionCard).toHaveAttribute("data-action-status", "done");
  await expect(actionCard).toContainText("Status: done");
  const firstCompletedAt = await actionCard.getAttribute("data-completed-at");
  expect(firstCompletedAt).toBeTruthy();

  await actionCard.getByRole("button", { name: "Complete again" }).click();
  actionCard = page.getByTestId("action-card").first();
  await expect(actionCard).toHaveAttribute("data-completed-at", firstCompletedAt ?? "");

  await page.getByTestId("person-form").getByLabel("Display name").fill("Other Person");
  await page
    .getByTestId("person-form")
    .getByLabel("Relationship")
    .selectOption("family");
  await page.getByRole("button", { name: "Create Person" }).click();
  const otherPerson = page.getByTestId("person-card").filter({ hasText: "Other Person" });
  await otherPerson.click();
  await expect(page.getByTestId("action-list").getByTestId("action-card")).toHaveCount(0);

  const storage = await page.evaluate(() => ({ ...localStorage }));
  const serializedStorage = JSON.stringify(storage);
  expect(Object.keys(storage).join(" ")).not.toMatch(/action/i);
  expect(serializedStorage).not.toContain(actionId);
  expect(serializedStorage).not.toContain("Evening walk");
  expect(serializedStorage).not.toContain(firstCompletedAt ?? "");
  expect(serializedStorage).not.toMatch(/"status"\s*:\s*"done"/);
});

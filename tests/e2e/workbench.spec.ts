import { expect, test } from "@playwright/test";

test("renders the empty workbench and opens settings", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "打开本地代码项目" })).toBeVisible();
  await expect(page.getByRole("button", { name: "选择 Git 仓库" })).toBeVisible();

  await page.getByRole("button", { name: "Agent 与连接设置" }).click();
  await expect(page.getByRole("heading", { name: "Agent 与模型" })).toBeVisible();

  await page.locator(".settings-header button").click();
  await expect(page.getByRole("heading", { name: "Agent 与模型" })).not.toBeVisible();
});

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const notice = (page: Page) => page.locator(".ah-notice");
const STAND = "http://localhost:8011";

// Отдельный файл с именем «00-»: тест идёт первым во всём наборе. Другие сценарии меняют бюджет и направления,
// а после такого изменения автопилот новый вариант объявления не предлагает.
test.describe.configure({ mode: "serial" });

test("тест по итогам анализа: автопилот предлагает, владелец соглашается и отменяет, площадка не тронута", async ({ page }) => {
  await page.goto("/");
  const business = (await (await page.request.get("/api/businesses")).json())[0];
  const { csrf_token: csrf } = await (await page.request.get("/api/me")).json();
  const base = `/api/businesses/${business.id}`;
  const changes = async () =>
    ((await (await page.request.get(`${base}/history`)).json()) as { ref: string }[]).filter((line) => line.ref.startsWith("change-")).length;
  const changesBefore = await changes();
  // Цель 300 ₽: «Маникюр у метро Профсоюзная» тратит больше, но выводов о нём ещё нет.
  const goal = await page.request.post(`${base}/goal`, { data: { rubles: 300 }, headers: { "X-CSRF-Token": csrf } });
  expect(goal.ok()).toBe(true);
  const run = await page.request.post(`${STAND}/api/stand/businesses/${business.id}/autopilot`);
  expect((await run.json()).accounts).toBe(1);

  await page.reload();
  const offer = page.locator(".ov-attention, .ov-more-item").filter({ has: page.getByRole("heading", { name: "Предлагаю проверить новый вариант объявления" }) });
  await expect(offer).toContainText("«Маникюр у метро Профсоюзная»");
  await expect(offer).toContainText("Бюджет не меняется");
  await offer.getByRole("button", { name: "Проверить вариант" }).click();
  await expect(notice(page)).toContainText("Готовлю тест. Пока вариант не отправлен, объявление работает как раньше");

  await page.reload();
  await expect(page.getByRole("heading", { name: "Предлагаю проверить новый вариант объявления" })).toHaveCount(0);
  const preparing = page.locator(".ov-attention, .ov-more-item").filter({ has: page.getByRole("heading", { name: "Готовлю A/B-тест" }) });
  await expect(preparing).toContainText("работает как раньше");
  await preparing.getByRole("button", { name: "Отменить тест" }).click();
  await expect(notice(page)).toContainText("Тест отменён. Показы не менялись, это объявление не предложу две недели");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Готовлю A/B-тест" })).toHaveCount(0);

  // Отмена — тот же отказ: следующий цикл не предлагает это объявление снова.
  await page.request.post(`${STAND}/api/stand/businesses/${business.id}/autopilot`);
  const today = await (await page.request.get(`${base}/today`)).json();
  expect(today.cards.filter((card: { kind: string }) => card.kind.startsWith("test_"))).toEqual([]);
  // Ни предложение, ни подготовка, ни отмена не меняли площадку: журнал изменений тот же.
  expect(await changes()).toBe(changesBefore);
});

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });
const notice = (page: Page) => page.locator(".ah-notice");
const STAND = "http://localhost:8011";

test.describe.configure({ mode: "serial" });

test.describe("Жизненный цикл A/B-тестирования и решений (ab-decisions.spec.ts)", () => {
  test("1. Экран «Реклама»: отзыв подготовленного объявления из блока «Готовлю к запуску»", async ({ page }) => {
    await page.goto("/");
    await nav(page, "Реклама").click();

    // Блок «Готовлю к запуску»
    const queue = page.locator(".ads-queue");
    await expect(queue).toBeVisible();
    await expect(queue.getByRole("heading", { level: 2 })).toHaveText("Готовлю к запуску");

    const queueItem = queue.locator("li").filter({ hasText: "Снятие покрытия — 300 ₽" });
    await expect(queueItem).toBeVisible();
    await expect(queueItem).toContainText("в запасе на замену");

    // Нажимаем «Не запускать» для этого объявления
    const removeBtn = queueItem.getByRole("button", { name: /Не запускать/ });
    await expect(removeBtn).toBeVisible();
    await removeBtn.click();

    // Проверяем нотис и удаление из списка подготовки
    await expect(notice(page)).toContainText("Не запускаю «Снятие покрытия — 300 ₽»");
    await expect(queue.locator("li").filter({ hasText: "Снятие покрытия — 300 ₽" })).toHaveCount(0);
  });

  test("2. Прототип (/?mock): итог проверки A/B-теста и отмена в Истории", async ({ page }) => {
    await page.goto("/?mock");

    // Переключаемся на прототипную ситуацию «вывод проверки»
    await page.locator(".ah-proto").getByRole("button", { name: "вывод проверки" }).click();

    // Карточка итога теста
    const attention = page.locator(".ov-attention");
    await expect(attention).toBeVisible();
    await expect(attention.getByRole("heading", { level: 2 })).toContainText("Проверка закончена: скидка лучше цены");

    // Переход к просмотру и отмене
    await attention.getByRole("button", { name: "Посмотреть и отменить" }).click();

    // Экран Истории
    await expect(page.locator(".ah-hist")).toBeVisible();
    const verdictRow = page.locator(".ah-hist-row").filter({ hasText: "Отключил вариант «Маникюр от 1" });
    await expect(verdictRow).toBeVisible();

    // Клик «Отменить» открывает пояснение последствий отмены
    const undoBtn = verdictRow.getByRole("button", { name: "Отменить" });
    await expect(undoBtn).toBeVisible();
    await undoBtn.click();

    await expect(verdictRow.locator(".ah-hist-note")).toContainText("Оба варианта снова поделят бюджет поровну");
  });
});

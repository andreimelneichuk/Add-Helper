import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

test.use({
  viewport: { width: 390, height: 844 },
  userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
});

test.describe.configure({ mode: "serial" });

const mobileNav = (page: Page, name: string) => page.locator(".ah-nav").getByRole("button", { name });

async function assertNoHorizontalScroll(page: Page, screenName: string) {
  const overflow = await page.evaluate(() => {
    const docWidth = document.documentElement.clientWidth;
    const scrollWidth = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth);
    return { docWidth, scrollWidth, hasOverflow: scrollWidth > docWidth };
  });

  expect(
    overflow.hasOverflow,
    `Экран «${screenName}» имеет горизонтальный скролл: scrollWidth=${overflow.scrollWidth} > clientWidth=${overflow.docWidth}`
  ).toBe(false);
}

test.describe("Задача 15. Мобильная адаптивность (mobile-view.spec.ts)", () => {
  test("1. Проверка мобильного таб-бара (.ah-nav) и скрытия десктопного рельса (.ah-rail)", async ({ page }) => {
    await page.goto("/");

    // 1. Десктопная боковая панель скрыта
    await expect(page.locator(".ah-rail")).toBeHidden();

    // 2. Мобильный нижний таб-бар отображается
    const navBar = page.locator(".ah-nav");
    await expect(navBar).toBeVisible();

    // Проверяем кнопки разделов в таб-баре
    const tabs = ["Обзор", "Статистика", "Реклама", "История", "Настройки", "Чат"];
    for (const tab of tabs) {
      await expect(navBar.getByRole("button", { name: tab })).toBeVisible();
    }

    // По умолчанию выбран «Обзор»
    await expect(navBar.getByRole("button", { name: "Обзор" })).toHaveAttribute("aria-current", "page");

    // Шапка: статус виден
    await expect(page.locator(".ah-status")).toBeVisible();
    await expect(page.locator(".ah-bar-pause")).toBeVisible();
    await expect(navBar.getByRole("button", { name: "Настройки" })).toBeVisible();
  });

  test("2. Отсутствие горизонтального скролла на всех экранах кабинета (iPhone 14, 390x844)", async ({ page }) => {
    await page.goto("/");

    // 1. Экран «Обзор»
    await expect(page.locator(".ov-kpis")).toBeVisible();
    await assertNoHorizontalScroll(page, "Обзор");

    // 2. Экран «Статистика»
    await mobileNav(page, "Статистика").click();
    await expect(page.locator(".stats")).toBeVisible();
    await assertNoHorizontalScroll(page, "Статистика");

    // 3. Экран «Реклама»
    await mobileNav(page, "Реклама").click();
    await expect(page.locator(".ads")).toBeVisible();
    await assertNoHorizontalScroll(page, "Реклама");

    // 4. Экран «История»
    await mobileNav(page, "История").click();
    await expect(page.locator(".ah-hist")).toBeVisible();
    await assertNoHorizontalScroll(page, "История");

    // 5. Экран «Чат с ИИ»
    await mobileNav(page, "Чат").click();
    await expect(page.locator(".ah-chat")).toBeVisible();
    await assertNoHorizontalScroll(page, "Чат");

    // 6. Экран «Настройки»
    await mobileNav(page, "Настройки").click();
    await expect(page.locator(".set")).toBeVisible();
    await assertNoHorizontalScroll(page, "Настройки");
  });

  test("3. Корректность диалога подтверждения (Proposal pattern .ah-confirm) на мобильном экране", async ({ page }) => {
    await page.goto("/");
    await mobileNav(page, "Реклама").click();
    await expect(page.locator(".ads")).toBeVisible();

    // Находим карточку объявления
    const card = page.locator(".ads-card").filter({ has: page.getByRole("button", { name: /^Отключить/ }) }).first();
    await expect(card).toBeVisible();
    const adTitle = await card.locator(".ads-title").innerText();

    // Нажимаем «Отключить» — появляется липкий диалог подтверждения внизу
    await card.getByRole("button", { name: /^Отключить/ }).click();
    const confirmDialog = page.locator(".ah-confirm");
    await expect(confirmDialog).toBeVisible();
    await expect(confirmDialog).toContainText(`Отключить «${adTitle}»?`);

    // Проверяем отсутствие горизонтального скролла с открытым диалогом
    await assertNoHorizontalScroll(page, "Диалог подтверждения отключения");

    // Проверяем, что кнопки «Применить» и «Не надо» видны и не перекрывают друг друга
    const applyBtn = confirmDialog.getByRole("button", { name: "Применить" });
    const cancelBtn = confirmDialog.getByRole("button", { name: "Не надо" });
    await expect(applyBtn).toBeVisible();
    await expect(cancelBtn).toBeVisible();

    // Проверяем геометрию кнопок внутри экрана (не выходят за пределы 390px)
    const applyBox = await applyBtn.boundingBox();
    const cancelBox = await cancelBtn.boundingBox();
    expect(applyBox).not.toBeNull();
    expect(cancelBox).not.toBeNull();
    if (applyBox && cancelBox) {
      expect(applyBox.x + applyBox.width).toBeLessThanOrEqual(390);
      expect(cancelBox.x + cancelBox.width).toBeLessThanOrEqual(390);
    }

    // Нажимаем «Не надо» — диалог скрывается
    await cancelBtn.click();
    await expect(confirmDialog).toHaveCount(0);
  });

  test("4. Мобильная адаптивность экрана онбординга клиента", async ({ context, page }) => {
    // Устанавливаем куку гостя для открытия стартового экрана мастера онбординга
    await context.addCookies([
      {
        name: "ah_guest",
        value: "1",
        domain: "localhost",
        path: "/",
      },
    ]);

    await page.goto("/?start=1");

    // Шапка онбординга на мобильном: бренд и логотип отображаются
    const brand = page.locator(".ah-brand");
    await expect(brand).toBeVisible();
    await expect(brand).toContainText("Ad Helper");

    // Шаг 0: Форма ввода сайта
    const urlInput = page.getByRole("textbox", { name: "Ссылка на сайт или страницу" });
    await expect(urlInput).toBeVisible();
    await expect(page.getByRole("button", { name: "Изучить" })).toBeVisible();

    // Проверяем отсутствие горизонтального скролла на шаге онбординга
    await assertNoHorizontalScroll(page, "Онбординг: Шаг 0");
  });
});

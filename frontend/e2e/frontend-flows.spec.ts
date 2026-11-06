import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });
const notice = (page: Page) => page.locator(".ah-notice");

test.describe.configure({ mode: "serial" });

test.describe("Комплексное тестирование экранов и интерактивных элементов (frontend-flows.spec.ts)", () => {
  test("1. История: переключение всех фильтров, раскрытие отмены и кнопка «Оставить»", async ({ page }) => {
    await page.goto("/");
    await nav(page, "История").click();

    const hist = page.locator(".ah-hist");
    await expect(hist).toBeVisible();

    // 1. Проверяем все чипы фильтров: «Все», «Деньги», «Объявления», «Проверки», «Ваши»
    const filters = ["Все", "Деньги", "Объявления", "Проверки", "Ваши"];
    for (const filter of filters) {
      const chip = hist.locator(".ah-hist-filters").getByRole("button", { name: filter });
      await expect(chip).toBeVisible();
      await chip.click();
      await expect(chip).toHaveAttribute("aria-pressed", "true");
    }

    // Возвращаемся ко всем записям
    await hist.locator(".ah-hist-filters").getByRole("button", { name: "Все" }).click();

    // 2. Находим запись с возможностью отмены
    const undoableRow = hist.locator(".ah-hist-row").filter({ has: page.getByRole("button", { name: "Отменить" }) }).first();
    if (await undoableRow.count() > 0) {
      await undoableRow.getByRole("button", { name: "Отменить" }).click();

      // Появилось пояснение последствий отмены
      await expect(undoableRow.locator(".ah-hist-note")).toBeVisible();

      // Нажатие кнопки «Оставить» отменяет диалог без выполнения отмены
      const keepBtn = undoableRow.getByRole("button", { name: "Оставить" });
      await expect(keepBtn).toBeVisible();
      await keepBtn.click();

      // Пояснение скрылось, кнопка «Отменить» снова на месте
      await expect(undoableRow.locator(".ah-hist-note")).toHaveCount(0);
      await expect(undoableRow.getByRole("button", { name: "Отменить" })).toBeVisible();
    }
  });

  test("2. Статистика: валидация диапазона дат «Свой период» и детальный отчёт направления", async ({ page }) => {
    await page.goto("/");
    await nav(page, "Статистика").click();

    // 1. Открываем форму «Свой период»
    await page.getByRole("button", { name: "Свой период" }).click();
    const ownForm = page.getByRole("form", { name: "Свой период" });
    await expect(ownForm).toBeVisible();

    const sinceInput = ownForm.locator("input[type='date']").first();
    const untilInput = ownForm.locator("input[type='date']").last();
    const submitBtn = ownForm.getByRole("button", { name: "Показать" });

    // 2. Кейс ошибки: дата начала позже даты окончания
    await sinceInput.fill("2026-11-04");
    await untilInput.fill("2026-11-01");
    await expect(ownForm.getByRole("alert")).toHaveText("Начало позже конца.");
    await expect(submitBtn).toBeDisabled();

    // 3. Кейс ошибки: дата из будущего (стенд зафиксирован на 2026-11-05)
    await sinceInput.fill("2026-11-01");
    await untilInput.fill("2026-11-20");
    await expect(ownForm.getByRole("alert")).toHaveText("Будущих дней в статистике ещё нет.");
    await expect(submitBtn).toBeDisabled();

    // 4. Валидный диапазон: прошедшие даты (1 - 4 ноября)
    await sinceInput.fill("2026-11-01");
    await untilInput.fill("2026-11-04");
    await expect(ownForm.locator(".ah-error")).toHaveCount(0);
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();

    // Чип выбранного периода отображает заданный интервал
    await expect(page.getByRole("button", { name: /1.*–.*4/ })).toBeVisible();

    // 5. Переход в отчёт направления из таблицы
    const dirButton = page.locator(".ah-table").getByRole("button", { name: /Отчёт по направлению/ }).first();
    const dirName = await dirButton.innerText();
    await dirButton.click();

    // Открылся детальный экран отчёта направления
    await expect(page.getByRole("heading", { name: dirName })).toBeVisible();
    await expect(page.locator(".stats-dir")).toBeVisible();
    await expect(page.getByRole("button", { name: "Назад к статистике" })).toBeVisible();

    // Возврат назад к общей статистике
    await page.getByRole("button", { name: "Назад к статистике" }).click();
    await expect(page.locator(".stats-dir")).toHaveCount(0);
    await expect(page.locator(".ah-table")).toBeVisible();
  });

  test("3. Настройки: профиль бизнеса, добавление/удаление услуги и каналы уведомлений", async ({ page }) => {
    await page.goto("/");
    await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();

    const profileSection = page.locator(".set-section[aria-labelledby='set-profile-title']");
    await expect(profileSection).toBeVisible();
    await expect(profileSection.locator(".set-service").first()).toBeVisible();

    // 1. Добавление новой услуги
    const initialServices = await profileSection.locator(".set-service").count();
    await profileSection.getByRole("button", { name: "Добавить услугу" }).click();
    await expect(profileSection.locator(".set-service")).toHaveCount(initialServices + 1);

    // 2. Ввод данных новой услуги
    const newServiceRow = profileSection.locator(".set-service").last();
    await newServiceRow.locator("input[aria-label^='Услуга']").fill("Дизайн ногтей");
    await newServiceRow.locator("input[aria-label^='Цена услуги']").fill("500");

    // 3. Удаление только что добавленной услуги кнопкой «Убрать»
    await newServiceRow.getByRole("button", { name: /Убрать услугу/ }).click();
    await expect(profileSection.locator(".set-service")).toHaveCount(initialServices);

    // 4. Проверка секций уведомлений и аккаунтов
    const channelsSection = page.locator(".set-section[aria-labelledby='set-channels-title']");
    await expect(channelsSection).toBeVisible();
    await expect(channelsSection.locator(".set-row", { hasText: "Почта" })).toBeVisible();
    await expect(channelsSection.locator(".set-row", { hasText: "Telegram" })).toBeVisible();

    const accountsSection = page.locator(".set-section[aria-labelledby='set-account-title']");
    await expect(accountsSection).toBeVisible();
    await expect(accountsSection.locator(".set-row", { hasText: "Яндекс Директ" })).toBeVisible();
  });

  test("4. Диалог подтверждения (Confirm Dialog): появление при отключении рекламы и отмена без применения", async ({ page }) => {
    await page.goto("/");
    await nav(page, "Реклама").click();

    // 1. Находим работающее объявление и нажимаем «Отключить»
    const card = page.locator(".ads-card").filter({ has: page.getByRole("button", { name: /^Отключить/ }) }).first();
    const adTitle = await card.locator(".ads-title").innerText();
    await card.getByRole("button", { name: /^Отключить/ }).click();

    // 2. Появляется плашка подтверждения .ah-confirm
    const confirmDialog = page.locator(".ah-confirm");
    await expect(confirmDialog).toBeVisible();
    await expect(confirmDialog).toContainText(`Отключить «${adTitle}»`);

    // 3. Нажимаем «Не надо» (отклонение подтверждения)
    await confirmDialog.getByRole("button", { name: "Не надо" }).click();

    // 4. Диалог закрылся, объявление осталось активным
    await expect(confirmDialog).toHaveCount(0);
    await expect(card.getByRole("button", { name: /^Отключить/ })).toBeVisible();
  });
});

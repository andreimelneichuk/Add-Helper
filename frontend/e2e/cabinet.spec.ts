import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const notice = (page: Page) => page.locator(".ah-notice");
const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });

test.describe.configure({ mode: "serial" });

test.describe("Задача 15. Кабинет и управление рекламой (cabinet.spec.ts)", () => {
  test("1. Вход по сессионной куке: сводка «Сегодня», показатели KPI, график дней и клиенты", async ({ page }) => {
    // Вход под демо-владельцем в существующий бизнес через сессионную куку
    await page.goto("/");
    await expect(page.locator(".ah-brand")).toHaveText("Студия маникюра «Лак»");
    await expect(page.locator(".ah-status")).toBeVisible();
    await expect(page.locator(".ah-proto")).toHaveCount(0);

    // 1. Проверка показателей сводки KPI
    const kpis = page.locator(".ov-kpis");
    await expect(kpis).toBeVisible();
    await expect(kpis).toContainText("Потрачено в");
    await expect(kpis).toContainText("Обращения, 7 дней");
    await expect(kpis).toContainText("Цена обращения");

    // 2. Блок клиентов: валидация максимального числа и запись
    const clientsForm = page.locator(".ov-clients");
    if (await clientsForm.count() > 0) {
      const clientsInput = clientsForm.locator("input#ov-clients");
      if (await clientsInput.count() > 0) {
        // Попытка записать заведомо большее число клиентов, чем обращений
        await clientsInput.fill("9999");
        await clientsForm.getByRole("button", { name: "Записать" }).click();
        await expect(clientsForm.getByRole("alert")).toContainText(/Обращений было/);

        // Ввод корректного числа клиентов
        await clientsInput.fill("2");
        await clientsForm.getByRole("button", { name: "Записать" }).click();
        await expect(notice(page)).toContainText(/Записал 2 из/);
      }
    }

    // 3. График дней: переключение метрик «Расход» / «Обращения»
    const chartPanel = page.locator(".ov-chart-panel");
    await expect(chartPanel).toBeVisible();

    const leadsChip = chartPanel.getByRole("button", { name: "Обращения" });
    const costChip = chartPanel.getByRole("button", { name: "Расход" });

    await leadsChip.click();
    await expect(leadsChip).toHaveAttribute("aria-pressed", "true");
    await expect(costChip).toHaveAttribute("aria-pressed", "false");

    await costChip.click();
    await expect(costChip).toHaveAttribute("aria-pressed", "true");

    // Клик по столбцу дня на графике
    const pastBar = chartPanel.locator(".ov-bar:not([disabled])").first();
    if (await pastBar.count() > 0) {
      await pastBar.click();
      await expect(pastBar).toHaveAttribute("aria-pressed", "true");
      await expect(chartPanel.locator(".ov-chart-note")).toBeVisible();
    }

    // 4. Переход к объявлению из таблицы обзора
    const firstAdLink = page.locator(".ov-ad-name").first();
    await expect(firstAdLink).toBeVisible();
    const adTitle = await firstAdLink.innerText();
    await firstAdLink.click();

    // Открылся детальный экран объявления
    await expect(page.locator(".ad-screen")).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: adTitle })).toBeVisible();

    // Возврат в обзор
    await nav(page, "Обзор").click();
    await expect(page.locator(".ov-chart-panel")).toBeVisible();
  });

  test("2. Переключение периодов в статистике: «7 дней», месячный период и «Свой период» с валидацией", async ({ page }) => {
    await page.goto("/");
    await nav(page, "Статистика").click();
    await expect(page.locator(".stats")).toBeVisible();

    // 1. Проверка чипа «7 дней»
    const weekChip = page.getByRole("button", { name: "7 дней" });
    await expect(weekChip).toBeVisible();
    await weekChip.click();
    await expect(weekChip).toHaveAttribute("aria-pressed", "true");

    // Таблица статистики за 7 дней отображает данные
    await expect(page.getByRole("region", { name: "Разбивка по услугам и объявлениям" })).toBeVisible();
    await expect(page.locator(".stats-nums")).toContainText("Обращения");

    // 2. Переключение на месячный период («С 1 ноября» / месяц)
    const monthChip = page.locator(".ah-chips .ah-chip").nth(1);
    await expect(monthChip).toBeVisible();
    await monthChip.click();
    await expect(monthChip).toHaveAttribute("aria-pressed", "true");
    await expect(weekChip).toHaveAttribute("aria-pressed", "false");

    // 3. Раскрытие формы «Свой период»
    const ownChip = page.getByRole("button", { name: /Свой период/ });
    await expect(ownChip).toBeVisible();
    await ownChip.click();

    const ownForm = page.getByRole("form", { name: "Свой период" });
    await expect(ownForm).toBeVisible();

    const sinceInput = ownForm.getByLabel("С");
    const untilInput = ownForm.getByLabel("По");

    // Валидация: дата конца в будущем
    await untilInput.fill("2026-11-20");
    await expect(ownForm.getByRole("alert")).toContainText("Будущих дней в статистике ещё нет.");
    await expect(ownForm.getByRole("button", { name: "Показать" })).toBeDisabled();

    // Валидация: дата начала позже даты конца
    await sinceInput.fill("2026-11-05");
    await untilInput.fill("2026-11-01");
    await expect(ownForm.getByRole("alert")).toContainText("Начало позже конца.");
    await expect(ownForm.getByRole("button", { name: "Показать" })).toBeDisabled();

    // Ввод корректного диапазона
    await sinceInput.fill("2026-11-01");
    await untilInput.fill("2026-11-04");
    await expect(ownForm.getByRole("button", { name: "Показать" })).toBeEnabled();
    await ownForm.getByRole("button", { name: "Показать" }).click();

    // Статистика обновилась под выбранный период
    await expect(page.locator(".stats-note").first()).toContainText("нояб");

    // 4. Переход в отчёт направления
    const dirBtn = page.locator(".stats-row-group button.ah-link").first();
    if (await dirBtn.count() > 0) {
      const dirName = await dirBtn.innerText();
      await dirBtn.click();

      // Открылся отчёт направления
      const dirReport = page.locator(".stats-dir");
      await expect(dirReport).toBeVisible();
      await expect(dirReport.getByRole("heading", { level: 2 })).toHaveText(dirName);

      // Кнопка «Назад к статистике»
      await dirReport.getByRole("button", { name: "Назад к статистике" }).click();
      await expect(page.locator(".stats-grid")).toBeVisible();
    }
  });

  test("3. Пауза и возобновление показов кампаний из шапки", async ({ page }) => {
    await page.goto("/");

    // Если кампания была на паузе из-за предыдущих сценариев, сначала включим
    const pauseBtn = page.locator(".ah-bar").getByRole("button", { name: /Пауза|Включить/ });
    await expect(pauseBtn).toBeVisible();

    const isPaused = (await page.locator(".ah-status").textContent())?.includes("На паузе");
    if (isPaused) {
      await page.locator(".ah-bar").getByRole("button", { name: "Включить" }).click();
      await expect(notice(page)).toContainText("Снял паузу");
      await page.reload();
      await expect(page.locator(".ah-status")).not.toHaveText("На паузе");
    }

    // 1. Нажатие кнопки «Пауза» в шапке
    await page.locator(".ah-bar").getByRole("button", { name: "Пауза" }).click();
    await expect(notice(page)).toContainText("Поставил на паузу");

    // 2. Статус сменился на «На паузе»
    await expect(page.locator(".ah-status")).toHaveText("На паузе");
    await expect(page.locator(".ah-bar").getByRole("button", { name: "Включить" })).toBeVisible();

    // 3. Перезагрузка страницы — статус «На паузе» сохраняется
    await page.reload();
    await expect(page.locator(".ah-status")).toHaveText("На паузе");
    await expect(page.locator(".ah-bar").getByRole("button", { name: "Включить" })).toBeVisible();

    // 4. Снятие с паузы кнопкой «Включить»
    await page.locator(".ah-bar").getByRole("button", { name: "Включить" }).click();
    await expect(notice(page)).toContainText("Снял паузу");

    // 5. Проверка записи в истории
    await nav(page, "История").click();
    await expect(page.getByText(/Снял паузу: вы попросили/).first()).toBeVisible();
    await expect(page.getByText(/Поставил рекламу на паузу/).first()).toBeVisible();
  });

  test("4. Управление рекламой: отключение и включение объявлений", async ({ page }) => {
    await page.goto("/");
    await nav(page, "Реклама").click();
    await expect(page.locator(".ads")).toBeVisible();

    // Находим активное объявление с кнопкой «Отключить»
    const card = page.locator(".ads-card").filter({ has: page.getByRole("button", { name: /^Отключить/ }) }).first();
    await expect(card).toBeVisible();
    const adTitle = await card.locator(".ads-title").innerText();

    // 1. Клик «Отключить» открывает диалог подтверждения
    await card.getByRole("button", { name: /^Отключить/ }).click();
    const confirmDialog = page.locator(".ah-confirm");
    await expect(confirmDialog).toBeVisible();
    await expect(confirmDialog).toContainText(`Отключить «${adTitle}»?`);

    // 2. Клик «Не надо» отменяет отключение
    await confirmDialog.getByRole("button", { name: "Не надо" }).click();
    await expect(confirmDialog).toHaveCount(0);

    // 3. Клик «Отключить» и «Применить»
    await card.getByRole("button", { name: /^Отключить/ }).click();
    await page.locator(".ah-confirm").getByRole("button", { name: "Применить" }).click();
    await expect(notice(page)).toContainText(`Отключил «${adTitle}»`);

    // 4. Проверка фильтра «Отключены»
    await page.getByRole("button", { name: /^Отключены/ }).click();
    await expect(page.locator(".ads-card-off .ads-title", { hasText: adTitle }).first()).toBeVisible();

    // 5. Включение объявления обратно
    const offCard = page.locator(".ads-card-off").filter({ hasText: adTitle }).first();
    await offCard.getByRole("button", { name: /^Включить/ }).click();
    await expect(notice(page)).toContainText(`Включил «${adTitle}»`);

    // Возврат в фильтр «Все»
    await page.getByRole("button", { name: /^Все/ }).click();
    await expect(page.locator(".ads-card", { hasText: adTitle }).first()).not.toHaveClass(/ads-card-off/);
  });

  test("5. Настройки: изменение месячного/суточного бюджета с валидацией лимитов", async ({ page }) => {
    await page.goto("/");
    await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();
    await expect(page.locator(".set")).toBeVisible();

    const budgetForm = page.getByRole("form", { name: "Бюджет месяца" });
    await expect(budgetForm).toBeVisible();

    const budgetInput = budgetForm.getByLabel("Бюджет в месяц, ₽");
    const currentVal = await budgetInput.inputValue();

    // 1. Валидация: ввод суммы меньше уже потраченного в текущем месяце
    await budgetInput.fill("500");
    await expect(budgetForm.getByRole("alert")).toContainText("уже потрачено");
    await expect(budgetForm.getByRole("button", { name: "Изменить бюджет" })).toBeDisabled();

    // 2. Определение минимально допустимой суммы
    const spentText = (await budgetForm.locator(".set-hint").first().textContent()) ?? "";
    const spentMatch = spentText.match(/потрачено ([\d\s ]+)/);
    const spentAmount = spentMatch ? Number(spentMatch[1]!.replace(/\D/g, "")) : 5000;
    const newTarget = Math.max(spentAmount + 2000, 32000);

    // 3. Ввод валидной суммы
    await budgetInput.fill(String(newTarget));
    await expect(budgetForm.getByRole("alert")).toHaveCount(0);
    const submitBtn = budgetForm.getByRole("button", { name: "Изменить бюджет" });
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();

    // 4. Появление группы подтверждения бюджета
    const confirmGroup = budgetForm.getByRole("group", { name: "Подтверждение бюджета" });
    await expect(confirmGroup).toBeVisible();
    await expect(confirmGroup).toContainText(`Поставить`);

    // Клик «Применить»
    await confirmGroup.getByRole("button", { name: "Применить" }).click();
    await expect(notice(page)).toContainText("Бюджет месяца теперь");

    // 5. Перезагрузка страницы — бюджет обновлён
    await page.reload();
    await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();
    await expect(page.getByLabel("Бюджет в месяц, ₽")).toHaveValue(String(newTarget));

    // Возвращаем исходный бюджет
    if (currentVal && currentVal !== String(newTarget) && Number(currentVal) >= spentAmount) {
      await page.getByLabel("Бюджет в месяц, ₽").fill(currentVal);
      await page.getByRole("button", { name: "Изменить бюджет" }).click();
      await page.getByRole("group", { name: "Подтверждение бюджета" }).getByRole("button", { name: "Применить" }).click();
    }
  });

  test("6. Настройки: валидация расписания показов (не менее 8 часов в неделю Яндекса)", async ({ page }) => {
    await page.goto("/");
    await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();

    const scheduleBlock = page.getByRole("region", { name: "Где и когда показывать" });
    await expect(scheduleBlock).toBeVisible();

    // Отключаем почти все дни недели (оставляем только один день)
    for (const day of ["пн", "вт", "ср", "чт", "пт", "сб"]) {
      await scheduleBlock.getByRole("button", { name: day, exact: true }).click();
    }

    // Ставим время меньше 8 часов (с 10 до 16 = 6 часов в 1 день)
    await scheduleBlock.getByLabel("Показывать с").selectOption("10");
    await scheduleBlock.getByLabel("Показывать до").selectOption("16");

    // Появляется алерт ограничения площадки Яндекса
    await expect(scheduleBlock.getByRole("alert")).toContainText("не принимает меньше 8 часов показа в неделю");
    await expect(scheduleBlock.getByRole("button", { name: "Изменить время" })).toBeDisabled();
  });
});

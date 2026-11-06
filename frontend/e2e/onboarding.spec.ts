import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const STAND = "http://localhost:8011";
const FRONT = "http://localhost:5175";

const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });

test.describe.configure({ mode: "serial" });

test.describe("Задача 15. Онбординг клиента (onboarding.spec.ts)", () => {
  test.beforeEach(async ({ context }) => {
    // Чистая сессия нового клиента без авторизации в кабинете
    await context.addCookies([{ name: "ah_guest", value: "1", url: FRONT }]);
  });

  test("Детальная проверка каждого элемента управления, кнопок и валидаций (шаги 0–3)", async ({ page }) => {
    test.setTimeout(90_000);
    await page.goto("/?start");
    await page.evaluate(() => {
      window.localStorage.removeItem("adhelper.startDraft");
      window.localStorage.removeItem("adhelper.startBusiness");
    });
    await page.reload();

    // === ШАГ 0: «О бизнесе» ===
    await expect(page.getByRole("heading", { level: 1, name: "Где о вас можно прочитать?" })).toBeVisible();
    const siteInput = page.getByLabel("Ссылка на сайт или страницу");
    await expect(siteInput).toBeVisible();
    await expect(siteInput).toHaveValue("");

    // 1. Нажатие «Изучить» при пустом поле ввода -> алерт валидации
    await page.getByRole("button", { name: "Изучить" }).click();
    await expect(page.getByRole("alert")).toContainText("Вставьте ссылку или заполните профиль вручную.");
    await expect(page.getByRole("heading", { level: 1, name: "Где о вас можно прочитать?" })).toBeVisible();

    // 2. Ввод одних пробелов -> алерт валидации
    await siteInput.fill("   ");
    await page.getByRole("button", { name: "Изучить" }).click();
    await expect(page.getByRole("alert")).toContainText("Вставьте ссылку или заполните профиль вручную.");

    // 3. Чат и подсказка «Можно спросить в чате» скрыты на этапе онбординга
    await expect(page.getByRole("button", { name: "Можно спросить в чате" })).toHaveCount(0);

    // 4. Кнопка «Заполнить вручную» переводит на шаг «Профиль» с чистым списком
    await page.getByRole("button", { name: "Заполнить вручную" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Вот что я понял о вас" })).toBeVisible();

    // Кнопка «Назад» возвращает на шаг 0
    await page.getByRole("button", { name: "Назад" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Где о вас можно прочитать?" })).toBeVisible();

    // 5. Ввод сайта и автопарсинг бэкендом
    await page.getByLabel("Ссылка на сайт или страницу").fill("nogti.ru");
    await page.getByRole("button", { name: "Изучить" }).click();

    // Появляется индикатор чтения сайта, поле становится readOnly
    await expect(page.getByRole("status")).toContainText("Читаю сайт");
    await expect(page.getByLabel("Ссылка на сайт или страницу")).toHaveAttribute("readonly", "");

    // Автопарсинг завершается, переходим на шаг «Вот что я понял о вас»
    await expect(page.getByRole("heading", { level: 1, name: "Вот что я понял о вас" })).toBeVisible({ timeout: 25_000 });

    // === ШАГ 1: «Профиль» («Вот что я понял о вас») ===
    // 6. Проверка извлечённых фактов
    await expect(page.getByText("«Маникюр с покрытием гель-лаком — 1 700 ₽»")).toBeVisible();
    await expect(page.getByText("«Адрес: Москва, ул. Тверская, 7»")).toBeVisible();
    // Выдуманный факт без цитаты на странице отброшен
    await expect(page.getByText("Лучшие мастера города")).toHaveCount(0);

    // 7. Кнопка «Изменить» на карточке факта
    await page.getByRole("button", { name: "Изменить: Чем занимаетесь" }).click();
    const editor = page.locator(".st-fact-editing");
    await expect(editor).toBeVisible();

    // Переключение кнопок категорий в редакторе (Услуга, Особенность, Адрес, Сфера)
    await editor.getByRole("button", { name: "Особенность" }).click();
    await editor.getByRole("button", { name: "Адрес" }).click();
    await editor.getByRole("button", { name: "Сфера" }).click();

    // Кнопка «Отмена» закрывает редактор без изменений
    await editor.getByRole("button", { name: "Отмена" }).click();
    await expect(page.locator(".st-fact-editing")).toHaveCount(0);

    // 8. Кнопка «Добавить факт»
    await page.getByRole("button", { name: "Добавить факт" }).click();
    const addEditor = page.locator(".st-fact-editing");
    const saveBtn = addEditor.getByRole("button", { name: "Сохранить" });
    await expect(saveBtn).toBeDisabled();

    // Выбор категории «Услуга», ввод значения и сохранение
    await addEditor.getByRole("button", { name: "Услуга" }).click();
    await addEditor.locator("input.ah-input").fill("Педикюр аппаратный, 2 500 ₽");
    await expect(saveBtn).toBeEnabled();
    await saveBtn.click();
    await expect(page.getByText("Педикюр аппаратный, 2 500 ₽")).toBeVisible();

    // 9. Удаление факта через кнопку «Убрать»
    await page.getByRole("button", { name: "Изменить: Услуга" }).last().click();
    await page.locator(".st-fact-editing").getByRole("button", { name: "Убрать" }).click();
    await expect(page.getByText("Педикюр аппаратный, 2 500 ₽")).toHaveCount(0);

    // 10. Валидация: если открыт FactEditor, переход дальше заблокирован
    await page.getByRole("button", { name: "Добавить факт" }).click();
    await page.getByRole("button", { name: "Всё верно" }).click();
    await expect(page.getByRole("alert")).toContainText("Сохраните или отмените правку факта.");
    await page.locator(".st-fact-editing").getByRole("button", { name: "Отмена" }).click();

    // 11. Клик «Всё верно» переводит на шаг «Бюджет»
    await page.getByRole("button", { name: "Всё верно" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Сколько готовы тратить в месяц?" })).toBeVisible();

    // === ШАГ 2: «Бюджет» ===
    // 12. Проверка кнопок готовых пресетов
    const budgetButtons = page.locator(".st-budgets .st-budget");
    await expect(budgetButtons.first()).toBeVisible();
    await budgetButtons.first().click();
    await expect(budgetButtons.first()).toHaveAttribute("aria-pressed", "true");

    // 13. Поле своей суммы: валидация минимального порога
    const customBudget = page.getByLabel("Своя сумма в месяц, ₽");
    await customBudget.fill("1200");
    await page.getByRole("button", { name: "Продолжить" }).click();
    await expect(page.getByRole("alert")).toContainText(/от 1.667|от 1.000/);

    // 14. Ввод валидной своей суммы и расчёт суточного расхода
    await customBudget.fill("21000");
    await expect(page.locator(".st-budget-own").getByText("около 700 ₽ в день")).toBeVisible();

    // 15. Кнопка «Назад» возвращает на шаг «Профиль»
    await page.getByRole("button", { name: "Назад" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Вот что я понял о вас" })).toBeVisible();

    // Возврат на шаг бюджета и переход дальше
    await page.getByRole("button", { name: "Всё верно" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Сколько готовы тратить в месяц?" })).toBeVisible();
    await page.getByRole("button", { name: "Продолжить" }).click();

    // === ШАГ 3: «Реклама» ===
    await expect(page.getByRole("heading", { level: 1, name: "Вот ваши объявления" })).toBeVisible();

    // 16. Ожидание завершения генерации объявлений
    const adCard = page.locator(".st-ad").first();
    await expect(adCard).toBeVisible({ timeout: 30_000 });

    // 17. Кнопка «Убрать» объявление
    const ads = page.locator(".st-ad");
    const count = await ads.count();
    for (let i = 0; i < count; i++) {
      const u = ads.nth(i).getByRole("button", { name: /^Убрать/ });
      if (await u.count() > 0) await u.click();
      await expect(ads.nth(i)).toHaveClass(/is-off/);
    }

    // Если все объявления отключены, переход дальше заблокирован
    await page.getByRole("button", { name: "Нравится" }).click();
    await expect(page.getByRole("alert")).toContainText("Оставьте хотя бы одно объявление.");

    // Возвращаем объявления
    for (let i = 0; i < count; i++) {
      const returnBtn = ads.nth(i).getByRole("button", { name: /^Вернуть/ });
      if (await returnBtn.count() > 0) await returnBtn.click();
      await expect(ads.nth(i)).not.toHaveClass(/is-off/);
    }

    // 18. Кнопка «Назад» на шаге рекламы возвращает на шаг бюджета
    await page.getByRole("button", { name: "Назад" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Сколько готовы тратить в месяц?" })).toBeVisible();

    // Возврат на шаг рекламы
    await page.getByRole("button", { name: "Продолжить" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Вот ваши объявления" })).toBeVisible();

    // 19. Клик «Нравится» переводит на шаг «Подключите кабинет Яндекса»
    await page.getByRole("button", { name: "Нравится" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Подключите кабинет Яндекса" })).toBeVisible();
  });

  test("Сквозной сценарий: полный онбординг от URL до создания бизнеса и запуска кампании", async ({ page, context }) => {
    test.setTimeout(120_000);
    await page.goto("/?start");
    await page.evaluate(() => {
      window.localStorage.removeItem("adhelper.startDraft");
      window.localStorage.removeItem("adhelper.startBusiness");
    });

    // 1. Ввод URL сайта
    await page.getByLabel("Ссылка на сайт или страницу").fill("nogti.ru");
    await page.getByRole("button", { name: "Изучить" }).click();

    // 2. Автопарсинг услуг и цен
    await page.getByRole("button", { name: "Всё верно" }).click({ timeout: 25_000 });

    // 3. Выбор бюджета
    await page.getByRole("button", { name: "Продолжить" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Вот ваши объявления" })).toBeVisible();

    // Ожидание объявлений и подтверждение
    await expect(page.locator(".st-ad").first()).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "Нравится" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Подключите кабинет Яндекса" })).toBeVisible();

    // 4. Попытка нажать «Продолжить» без входа через Яндекс
    await page.getByRole("button", { name: "Продолжить" }).click();
    await expect(page.getByRole("alert")).toContainText("Войдите через Яндекс");

    // Авторизация Яндекс ID через подставной стенд
    await page.getByRole("button", { name: "Войти через Яндекс" }).click();
    await expect(page.getByRole("button", { name: "Подключить кабинет" })).toBeVisible({ timeout: 20_000 });

    // Проверка наличия сессионной куки ah_session
    const cookies = await context.cookies(FRONT);
    const sessionCookie = cookies.find((c) => c.name === "ah_session")?.value;
    expect(sessionCookie).toBeDefined();

    // Проверка сценария отмены подключения в Яндексе
    await page.request.post(`${STAND}/stand-yandex/deny`);
    await page.getByRole("button", { name: "Подключить кабинет" }).click();
    await expect(page.getByText(/отменили подключение/)).toBeVisible();

    // Повторное успешное подключение кабинета
    await page.getByRole("button", { name: "Подключить кабинет" }).click();
    await expect(page.getByText(/stand-direct-\d+/)).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "Продолжить" }).click();

    // 5. Шаг маркировки: радиокнопки рекламодателя и валидация ИНН
    await expect(page.getByRole("heading", { level: 1, name: "Укажите ИНН для маркировки" })).toBeVisible();
    await page.getByLabel("ИНН для маркировки").fill("7707083894");
    await page.getByLabel("Наименование, как в ЕГРЮЛ").fill("ООО «Ноготок»");
    await page.getByLabel("ОКВЭД основного вида деятельности").fill("96.02");
    await page.getByRole("button", { name: "Продолжить" }).click();
    await expect(page.getByRole("alert")).toContainText("ошибка в цифрах");

    // Ввод валидного ИНН юридического лица
    await page.getByLabel("ИНН для маркировки").fill("7707083893");
    await page.getByRole("button", { name: "Продолжить" }).click();

    // 6. Шаг кода статистики Яндекс Метрики
    await expect(page.getByRole("heading", { level: 1, name: "Поставьте код статистики на сайт" })).toBeVisible();
    await page.getByRole("button", { name: "Скопировать письмо разработчику" }).click();
    await expect(page.getByText(/Здравствуйте! Поставьте, пожалуйста, на сайт/)).toBeVisible();

    // Проверка установки счетчика
    await page.getByRole("button", { name: "Проверить установку" }).click();
    await expect(page.getByText(/Код найден на сайте, счётчик 98765432/)).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "Продолжить" }).click();

    // 7. Шаг запуска: чек-лист готовности и создание кампании
    await expect(page.getByRole("heading", { level: 1, name: "Что готово к запуску" })).toBeVisible();
    const readyList = page.getByRole("list", { name: "Готовность к запуску" });
    await expect(readyList).toContainText("Маркировка: организация, ИНН ••••••3893");
    await expect(readyList).toContainText("Код Метрики на сайте есть, счётчик 98765432");

    // Запуск рекламы
    await page.getByRole("button", { name: "Запустить рекламу" }).click();
    await expect(page.getByText(/Реклама запущена: Яндекс проверяет объявления/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: "Запустить рекламу" })).toHaveCount(0);

    // 8. Переход в рабочий кабинет и проверка созданного бизнеса
    await page.getByRole("button", { name: "Открыть кабинет" }).click();
    await expect(page).toHaveURL(/:\d+\/$/);

    // В кабинете отображается созданный бизнес
    await expect(page.locator(".ah-bar")).toContainText("nogti.ru");

    // В истории отображаются записи запуска и маркировки
    await nav(page, "История").click();
    await expect(page.getByRole("heading", { name: /^Создал кампанию/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /^Передал данные для маркировки/ })).toBeVisible();
  });
});

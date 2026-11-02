import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const notice = (page: Page) => page.locator(".ah-notice");
const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });

test.describe.configure({ mode: "serial" });

test("обзор показывает настоящие данные, а не прототип", async ({ page }) => {
  await page.goto("/?new");
  await expect(page.locator(".ah-brand")).toHaveText("Студия маникюра «Лак»");
  await expect(page.locator(".ah-proto")).toHaveCount(0);
  await expect(page.getByText("Салон «Пример»")).toHaveCount(0);
});

test("отключение объявления доходит до журнала и видно в истории", async ({ page }) => {
  await page.goto("/?new");
  await nav(page, "Реклама").click();
  // Карточки решений по новым объявлениям стоят выше и кнопки «Отключить» не имеют.
  const card = page.locator(".ads-card").filter({ has: page.getByRole("button", { name: /^Отключить/ }) }).first();
  const name = await card.locator(".ads-title").innerText();
  await card.getByRole("button", { name: /^Отключить/ }).click();
  await page.getByRole("button", { name: "Применить" }).click();
  await expect(notice(page)).toContainText(`Отключил «${name}»`);

  await page.reload();
  await nav(page, "Реклама").click();
  await page.getByRole("button", { name: /^Отключены/ }).click();
  await expect(page.locator(".ads-card-off .ads-title", { hasText: name }).first()).toBeVisible();

  await nav(page, "История").click();
  await expect(page.getByRole("heading", { name: `Отключил объявление: «${name}»` })).toBeVisible();
});

test("отмена возвращает объявление новой записью, исходная остаётся", async ({ page }) => {
  await page.goto("/?new");
  await nav(page, "История").click();
  const row = page.locator(".ah-hist-row", { hasText: "Отключил объявление" }).first();
  await row.getByRole("button", { name: "Отменить" }).click();
  await page.getByRole("button", { name: "Да, отменить" }).click();
  await expect(notice(page)).toContainText("Отменил");
  await expect(page.locator(".ah-hist-row", { hasText: "Включил объявление" }).first()).toBeVisible();
  await expect(page.locator(".ah-hist-row", { hasText: "Отключил объявление" }).first()).toBeVisible();
});

test("пауза из шапки останавливает показы и переживает перезагрузку", async ({ page }) => {
  await page.goto("/?new");
  await page.locator(".ah-bar").getByRole("button", { name: "Пауза" }).click();
  await expect(notice(page)).toContainText("Поставил на паузу");
  await page.reload();
  await expect(page.locator(".ah-status")).toHaveText("На паузе");
});

test("пауза владельца снимается кнопкой, остановленное не владельцем остаётся", async ({ page }) => {
  await page.goto("/?new");
  await expect(page.locator(".ah-status")).toHaveText("На паузе");
  await page.locator(".ah-bar").getByRole("button", { name: "Включить" }).click();
  await expect(notice(page)).toContainText("Снял паузу");
  // «Наращивание» стояло до паузы владельца: его кнопка не включает.
  await expect(notice(page)).toContainText("Часть направлений не включил");
  await page.reload();
  await expect(page.locator(".ah-status")).not.toHaveText("На паузе");
  await nav(page, "История").click();
  await expect(page.getByText(/Снял паузу: вы попросили/).first()).toBeVisible();
});

test("бюджет ниже потраченного не принимается, снижение доходит до сервиса", async ({ page }) => {
  await page.goto("/?new");
  await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();
  const form = page.getByRole("form", { name: "Бюджет месяца" });
  const input = form.getByLabel("Бюджет в месяц, ₽");
  await input.fill("1000");
  await expect(form.getByRole("alert")).toContainText("уже потрачено");
  await expect(form.getByRole("button", { name: "Изменить бюджет" })).toBeDisabled();

  const spentText = (await form.locator(".set-hint").first().textContent()) ?? "";
  const spent = Number(spentText.match(/потрачено ([\d\s ]+)/)![1]!.replace(/\D/g, ""));
  const target = Math.max(spent + 1000, 30000);
  await input.fill(String(target));
  await form.getByRole("button", { name: "Изменить бюджет" }).click();
  await form.getByRole("group", { name: "Подтверждение бюджета" }).getByRole("button", { name: "Применить" }).click();
  await expect(notice(page)).toContainText(`Бюджет месяца теперь`);

  await page.reload();
  await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();
  await expect(page.getByLabel("Бюджет в месяц, ₽")).toHaveValue(String(target));
});

test("регион показа меняется через журнал, видно в истории, отмена возвращает прежний", async ({ page }) => {
  await page.goto("/?new");
  await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();
  const block = page.getByRole("region", { name: "Где и когда показывать" });
  await expect(block).toContainText("Сейчас: у направлений разный регион");
  await block.getByRole("button", { name: "Санкт-Петербург", exact: true }).click();
  await block.getByRole("button", { name: "Изменить регион" }).click();
  await block.getByRole("group", { name: "Подтверждение показа" }).getByRole("button", { name: "Применить" }).click();
  await expect(block.getByRole("status")).toContainText("Регион показов теперь: Санкт-Петербург");

  await page.reload();
  await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();
  await expect(page.getByRole("region", { name: "Где и когда показывать" })).toContainText("Сейчас: Санкт-Петербург.");

  await nav(page, "История").click();
  const row = page.locator(".ah-hist-row", { hasText: "Изменил регион показов" }).first();
  await expect(row).toContainText("Санкт-Петербург");
  await row.getByRole("button", { name: "Отменить" }).click();
  await page.getByRole("button", { name: "Да, отменить" }).click();
  await expect(notice(page)).toContainText("Отменил");
  // Отменено для одного направления: у остальных новый регион, общего значения больше нет.
  await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();
  await expect(page.getByRole("region", { name: "Где и когда показывать" })).toContainText("разный регион");
});

test("расписание короче порога Яндекса не отправляется", async ({ page }) => {
  await page.goto("/?new");
  await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();
  const block = page.getByRole("region", { name: "Где и когда показывать" });
  for (const day of ["пн", "вт", "ср", "чт", "пт", "сб"]) await block.getByRole("button", { name: day, exact: true }).click();
  await block.getByLabel("Показывать с").selectOption("10");
  await block.getByLabel("Показывать до").selectOption("17");
  await expect(block.getByRole("alert")).toContainText("не принимает меньше 8 часов показа в неделю");
  await expect(block.getByRole("button", { name: "Изменить время" })).toBeDisabled();
});

test("сайт разбирается в черновик, в профиль попадает только подтверждённое", async ({ page }) => {
  await page.goto("/?new");
  await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();
  const site = page.getByLabel("Сайт");
  await site.fill("nogti.ru");
  await page.getByRole("button", { name: "Заполнить с сайта" }).click();
  const found = page.getByRole("group", { name: "Нашёл на сайте" });
  await expect(found).toContainText("«Маникюр с покрытием гель-лаком — 1 700 ₽»", { timeout: 15_000 });
  // Факт без цитаты со страницы код отбросил.
  await expect(found).not.toContainText("Лучшие мастера");
  await found.getByRole("checkbox", { name: /Адрес/ }).uncheck();
  await found.getByRole("button", { name: "Перенести в профиль" }).click();
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Сохранил" })).toBeVisible();

  await page.reload();
  await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();
  await expect(page.getByLabel("Чем занимаетесь")).toHaveValue("маникюр");
  const services = page.locator(".set-service input[aria-label^='Услуга']");
  await expect(services.last()).toHaveValue("Маникюр с покрытием гель-лаком");
  await expect(page.getByLabel("Адрес", { exact: true })).not.toHaveValue("Москва, ул. Тверская, 7");
});

test("до входа сайт разбирается в шаги запуска, выдуманного там нет", async ({ page }) => {
  await page.goto("/?new&start");
  await page.getByLabel("Ссылка на сайт или страницу").fill("nogti.ru");
  await page.getByRole("button", { name: "Изучить" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Вот что я понял о вас" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("«Маникюр с покрытием гель-лаком — 1 700 ₽»")).toBeVisible();
  // Факт без цитаты со страницы код отбросил, подставных фактов прототипа нет.
  await expect(page.getByText("Лучшие мастера")).toHaveCount(0);
  await expect(page.getByText("Скидка 20% на первый визит")).toHaveCount(0);
  await page.getByRole("button", { name: "Изменить: Чем занимаетесь" }).click();
  await page.getByLabel("Чем занимаетесь").fill("маникюр и педикюр");
  await page.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.getByText("маникюр и педикюр")).toBeVisible();
  await page.getByRole("button", { name: "Всё верно" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Сколько готовы тратить в месяц?" })).toBeVisible();
});

test("бюджет запуска проверяется по правилам сервиса", async ({ page }) => {
  await page.goto("/?new&start");
  await page.evaluate(() => window.localStorage.removeItem("adhelper.startDraft"));
  await page.getByRole("button", { name: "Заполнить вручную" }).click();
  await page.getByRole("button", { name: "Добавить факт" }).click();
  await page.getByLabel("Новый факт").fill("Маникюр у дома");
  await page.getByRole("button", { name: "Сохранить" }).click();
  await page.getByRole("button", { name: "Всё верно" }).click();
  // Минимум 1 550 ₽ пришёл из правил plan-1, в прототипе он 1 000 ₽.
  await page.getByLabel("Своя сумма в месяц, ₽").fill("1200");
  await page.getByRole("button", { name: "Продолжить" }).click();
  await expect(page.getByRole("alert")).toContainText(/от 1.550/);
});

test("черновик после входа не затирает бизнес, который уже есть", async ({ page }) => {
  // Стенд входит под демо-владельцем, у него есть бизнес: черновик переносить нельзя.
  await page.goto("/?new&start");
  await page.evaluate(() => window.localStorage.setItem("adhelper.startDraft", JSON.stringify({ site: "https://nogti.ru/", facts: [], budget: 20000 })));
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("У вас уже есть бизнес");
  await page.getByRole("button", { name: "Продолжить" }).click();
  await expect(page).toHaveURL(/\/\?new$/);
  await page.goto("/?new");
  await page.locator(".ah-bar").getByRole("button", { name: "Настройки" }).click();
  await expect(page.getByLabel("Сайт")).not.toHaveValue("https://nogti.ru/");
});

test("после подключения кабинета мастер ведёт маркировку, код статистики и сводку через сервис", async ({ page }) => {
  await page.goto("/?new");
  // Сайт в профиле нужен шагу «Статистика»; бизнес мастера запоминает браузер после переноса черновика.
  const id = await page.evaluate(async () => {
    const me = await (await fetch("/api/me")).json();
    const business = (await (await fetch("/api/businesses")).json())[0];
    const base = `/api/businesses/${business.id}`;
    const profile = await (await fetch(`${base}/profile`)).json();
    await fetch(`${base}/profile`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token },
      body: JSON.stringify({ ...profile, website: "https://nogti.ru/" }),
    });
    return business.id as string;
  });
  await page.evaluate((b) => window.localStorage.setItem("adhelper.startBusiness", b), id);
  await page.goto("/?new&start&connect=denied");
  await expect(page.getByRole("heading", { level: 1, name: "Подключите кабинет Яндекса" })).toBeVisible();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveURL(/\/\?new&start$/);
  await page.getByRole("button", { name: "Продолжить" }).click();

  await page.getByLabel("ИНН для маркировки").fill("7707083894");
  await page.getByLabel("Наименование, как в ЕГРЮЛ").fill("ООО «Лак»");
  await page.getByLabel("ОКВЭД основного вида деятельности").fill("96.02");
  await page.getByRole("button", { name: "Продолжить" }).click();
  await expect(page.getByRole("alert")).toContainText("ошибка в цифрах");
  await page.getByLabel("ИНН для маркировки").fill("7707083893");
  await page.getByRole("button", { name: "Продолжить" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Поставьте код статистики на сайт" })).toBeVisible();
  await page.getByRole("button", { name: "Проверить установку" }).click();
  await expect(page.getByText(/Код найден на сайте, счётчик 98765432/)).toBeVisible({ timeout: 15000 });
  await page.getByRole("button", { name: "Продолжить" }).click();

  const ready = page.getByRole("list", { name: "Готовность к запуску" });
  await expect(ready).toContainText("Маркировка: организация, ИНН ••••••3893");
  await expect(ready).toContainText("Код Метрики на сайте есть, счётчик 98765432");
  await expect(page.getByText(/когда Яндекс откроет доступ к API/)).toBeVisible();
  await page.getByRole("button", { name: "Открыть кабинет" }).click();
  await expect(page).toHaveURL(/\/\?new$/);
});

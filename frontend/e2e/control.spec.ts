import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const notice = (page: Page) => page.locator(".ah-notice");
const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });
const STAND = "http://localhost:8011";
// Вход владельца из сценария запуска: следующий сценарий проверяет его объявления у Яндекса.
let launched = "";

test.describe.configure({ mode: "serial" });

test("обзор показывает настоящие данные, а не прототип", async ({ page }) => {
  await page.goto("/?new");
  await expect(page.locator(".ah-brand")).toHaveText("Студия маникюра «Лак»");
  await expect(page.locator(".ah-proto")).toHaveCount(0);
  await expect(page.getByText("Салон «Пример»")).toHaveCount(0);
});

// Первым после обзора: другие сценарии меняют направления, а после изменения тест не предлагается.
test("тест по итогам анализа: автопилот предлагает, владелец соглашается и отменяет, площадка не тронута", async ({ page }) => {
  await page.goto("/?new");
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

test("«Сегодня»: карточки в порядке правил сервиса, пауза первой, финал ленты", async ({ page }) => {
  await page.goto("/?new");
  const business = (await (await page.request.get("/api/businesses")).json())[0];
  const { rules, cards } = await (await page.request.get(`/api/businesses/${business.id}/today`)).json();
  expect(rules).toBe("today-2");
  // Пауза владельца из прошлого шага — первая карточка, итог недели ниже.
  expect(cards[0].kind).toBe("paused");
  expect(cards.map((c: { kind: string }) => c.kind)).toContain("summary");
  expect(cards.length).toBeLessThanOrEqual(5);
  const main = page.locator(".ov-attention");
  await expect(main.getByRole("heading")).toHaveText(cards[0].title);
  await expect(main.getByRole("button", { name: "Включить" })).toBeVisible();
  const more = page.getByRole("region", { name: "Ещё на сегодня" });
  await expect(more.getByRole("heading")).toHaveText(cards.slice(1).map((c: { title: string }) => c.title));
  await expect(more).toContainText("На сегодня всё.");
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
  // Просьба меняла все направления: можно отменить целиком или только это.
  await expect(row.locator(".ah-hist-note")).toContainText("Вернётся прежний регион показа");
  await row.getByRole("button", { name: "Только это направление" }).click();
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

test("до входа объявления пишет писатель сервиса по фактам сайта, выбор переходит в бизнес", async ({ page }) => {
  await page.goto("/?new&start");
  await page.evaluate(() => window.localStorage.removeItem("adhelper.startDraft"));
  await page.getByLabel("Ссылка на сайт или страницу").fill("nogti.ru");
  await page.getByRole("button", { name: "Изучить" }).click();
  await page.getByRole("button", { name: "Всё верно" }).click({ timeout: 15_000 });
  await page.getByRole("button", { name: "Продолжить" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Вот ваши объявления" })).toBeVisible();
  // Подставных объявлений прототипа нет: текст написан по цене и адресу с сайта.
  await expect(page.getByText("Маникюр с покрытием гель-лаком за 1700 ₽ на Тверской, 7.")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/Опирается на: цена услуги «Маникюр с покрытием гель-лаком»/)).toBeVisible();
  await expect(page.getByText("Скидка 20% на первый визит")).toHaveCount(0);
  await page.getByRole("button", { name: "Убрать «Маникюр на Тверской»" }).click();
  await page.getByRole("button", { name: "Нравится" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Подключите кабинет Яндекса" })).toBeVisible();

  // Полный вход стенд не проходит: вход подменён, выбор переносим тем же вызовом, что и после входа.
  await page.route("**/api/auth/yandex/login**", (route) => route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: "<p>login</p>" }));
  await page.getByRole("button", { name: "Войти через Яндекс" }).click();
  await page.waitForURL(/\/api\/auth\/yandex\/login\?then=start$/);
  const draft = await page.evaluate(() => JSON.parse(window.localStorage.getItem("adhelper.startDraft") ?? "null"));
  expect(draft.ads.kept).toEqual([0]);
  const items = await page.evaluate(async (ads) => {
    const me = await (await fetch("/api/me")).json();
    const business = (await (await fetch("/api/businesses")).json())[0];
    const base = `/api/businesses/${business.id}`;
    await fetch(`${base}/creative-sets/adopt`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token },
      body: JSON.stringify(ads),
    });
    window.localStorage.removeItem("adhelper.startDraft");
    return (await (await fetch(`${base}/launch-check`)).json()).items.map((i: { text: string }) => i.text);
  }, draft.ads);
  expect(items).toContain("Объявления: выбрано 1");
});

test("бюджет запуска проверяется по правилам сервиса", async ({ page }) => {
  await page.goto("/?new&start");
  await page.evaluate(() => window.localStorage.removeItem("adhelper.startDraft"));
  await page.getByRole("button", { name: "Заполнить вручную" }).click();
  await page.getByRole("button", { name: "Добавить факт" }).click();
  await page.getByLabel("Новый факт").fill("Маникюр у дома");
  await page.getByRole("button", { name: "Сохранить" }).click();
  await page.getByRole("button", { name: "Всё верно" }).click();
  // Минимум 1 667 ₽ пришёл из правил plan-2, в прототипе он 1 000 ₽.
  await page.getByLabel("Своя сумма в месяц, ₽").fill("1200");
  await page.getByRole("button", { name: "Продолжить" }).click();
  await expect(page.getByRole("alert")).toContainText(/от 1.667/);
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
  // Объявления демо-кабинета выбраны не в мастере: кнопки запуска нет, сводка говорит, чего ждать.
  await expect(page.getByText(/здесь появится кнопка запуска/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Запустить рекламу" })).toHaveCount(0);
  await page.getByRole("button", { name: "Открыть кабинет" }).click();
  await expect(page).toHaveURL(/\/\?new$/);
});


test("новый владелец проходит мастер и запускает рекламу: кампания через журнал, видна в кабинете", async ({ page, context, request }) => {
  // Свой владелец без бизнеса: стенд выдаёт ему вход, прокси Vite не подменяет его демо-входом.
  const { cookie } = await (await request.post(`${STAND}/api/stand/owners`)).json();
  await context.addCookies([{ name: "ah_session", value: cookie, url: "http://localhost:5175" }]);
  launched = cookie;
  await page.goto("/?new&start");
  await page.evaluate(() => {
    window.localStorage.removeItem("adhelper.startDraft");
    window.localStorage.removeItem("adhelper.startBusiness");
  });
  await page.getByLabel("Ссылка на сайт или страницу").fill("nogti.ru");
  await page.getByRole("button", { name: "Изучить" }).click();
  await page.getByRole("button", { name: "Всё верно" }).click({ timeout: 15_000 });
  await page.getByRole("button", { name: "Продолжить" }).click();
  await expect(page.getByText("Маникюр с покрытием гель-лаком за 1700 ₽ на Тверской, 7.")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Нравится" }).click();

  // Вход в Яндекс подменён: владелец уже вошёл, возвращаемся в мастер, как после настоящего входа.
  await page.route("**/api/auth/yandex/login**", (route) => route.fulfill({ status: 302, headers: { location: "/?new&start" } }));
  await page.getByRole("button", { name: "Войти через Яндекс" }).click();
  await expect(page.getByRole("button", { name: "Подключить кабинет" })).toBeVisible();

  // OAuth Яндекса стенд не проходит: кабинет записывает стенд, мастер возвращается на `connect=ok`,
  // как после настоящего подключения.
  await page.route("**/accounts/connect**", async (route) => {
    const business = (await (await page.request.get("/api/businesses")).json())[0];
    await request.post(`${STAND}/api/stand/businesses/${business.id}/connect`);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authorize_url: "/?new&start&connect=ok" }) });
  });
  await page.getByRole("button", { name: "Подключить кабинет" }).click();
  await expect(page).toHaveURL(/\/\?new&start$/);
  await expect(page.getByText(/stand-direct/)).toBeVisible();
  await page.getByRole("button", { name: "Продолжить" }).click();

  await page.getByLabel("ИНН для маркировки").fill("7707083893");
  await page.getByLabel("Наименование, как в ЕГРЮЛ").fill("ООО «Ноготок»");
  await page.getByLabel("ОКВЭД основного вида деятельности").fill("96.02");
  await page.getByRole("button", { name: "Продолжить" }).click();
  await page.getByRole("button", { name: "Проверить установку" }).click();
  await expect(page.getByText(/Код найден на сайте, счётчик 98765432/)).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Продолжить" }).click();

  const ready = page.getByRole("list", { name: "Готовность к запуску" });
  await expect(ready).toContainText("Объявления: выбрано 2");
  await expect(ready).toContainText(/Запуск: 1 кампания \(поиск и сайты — 18\s000\s₽\)/);
  await page.getByRole("button", { name: "Запустить рекламу" }).click();
  const made = page.getByRole("list", { name: "Созданные кампании" });
  await expect(made).toContainText(/лимит 18\s000\s₽ на месяц/);
  await expect(page.getByText(/Реклама запущена: Яндекс проверяет объявления/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Запустить рекламу" })).toHaveCount(0);

  // Сервис помнит запуск: повторная сводка кнопку не даёт, второй кампании не будет.
  const again = await page.evaluate(async () => {
    const business = (await (await fetch("/api/businesses")).json())[0];
    return (await fetch(`/api/businesses/${business.id}/launch-check`)).json();
  });
  expect([again.launched, again.launch_available]).toEqual(["done", false]);

  await page.goto("/?new");
  await nav(page, "История").click();
  await expect(page.getByRole("heading", { name: /^Создал кампанию/ })).toBeVisible();
  // Маркировка ушла в Яндекс до кампании, в истории без реквизитов: только конец ИНН.
  await expect(page.getByRole("heading", { name: /^Передал данные для маркировки/ })).toBeVisible();
  await expect(page.getByText("7707083893")).toHaveCount(0);
  await expect(page.getByText(/лимит 18\s000\s₽ до/)).toBeVisible();
});

test("проверка Яндекса: отклонённое переписано по правилам, после решения владельца проходит; отказ из-за сайта ждёт владельца", async ({ page, context, request }) => {
  test.skip(!launched, "нужен запуск из прошлого сценария");
  // Три цикла проверки и писатель стенда: дольше обычного сценария.
  test.setTimeout(150_000);
  await context.addCookies([{ name: "ah_session", value: launched, url: "http://localhost:5175" }]);
  const business = (await (await page.request.get("/api/businesses")).json())[0];
  const stand = `${STAND}/api/stand/businesses/${business.id}`;
  const verdict = async (headline: string, state: string, reasons: string[] = []) => {
    const reply = await (await request.post(`${stand}/verdicts`, { data: { headline, state, reasons } })).json();
    expect(reply.ads).toBe(1);
  };
  type Row = { id: number; headline: string; text: string; state: string; reason: string };
  const creatives = async (): Promise<Row[]> => (await (await page.request.get(`/api/businesses/${business.id}/creatives`)).json()).creatives;

  await page.goto("/?new");
  await nav(page, "Реклама").click();
  await expect(page.getByText("на проверке Яндекса").first()).toBeVisible();

  // Оба отклонены. Сайт текстом не починить: объявление ждёт владельца и держит место.
  // Другое переписывает писатель по просьбе из правил, а не по тексту площадки; без судьи
  // новый текст ждёт решения владельца.
  await verdict("Маникюр с покрытием", "rejected", ["Сайт недоступен для проверки"]);
  await verdict("Маникюр на Тверской", "rejected", ["Использование превосходной степени без подтверждения"]);
  await request.post(`${stand}/moderate`);
  let repair: Row | undefined;
  await expect(async () => {
    repair = (await creatives()).find((c) => c.reason === "moderation_rejected" && c.state === "needs_owner");
    expect(repair).toBeDefined();
  }).toPass({ timeout: 20_000 });
  expect((await creatives()).some((c) => c.headline === "Маникюр на Тверской" && c.state === "rejected")).toBe(true);

  await page.reload();
  await expect(page.getByText(/«Маникюр с покрытием гель-лаком» не прошло проверку Яндекса/)).toBeVisible();
  await nav(page, "Реклама").click();
  await expect(page.getByText("отклонено Яндексом").first()).toBeVisible();
  const card = page.getByRole("region", { name: "Нужно ваше решение" }).getByRole("article", { name: `Решение по «${repair!.headline}»` });
  await card.getByRole("button", { name: "Запустить" }).click();
  await expect(notice(page)).toContainText("Разрешил запуск");

  // Сервис отправляет переписанное через журнал, Яндекс одобряет — начинаются показы.
  await request.post(`${stand}/moderate`);
  expect((await creatives()).find((c) => c.id === repair!.id)?.state).toBe("moderation");
  await verdict(repair!.headline, "accepted");
  await request.post(`${stand}/moderate`);
  expect((await creatives()).find((c) => c.id === repair!.id)?.state).toBe("running");
  await nav(page, "История").click();
  await expect(page.getByRole("heading", { name: /^Отправил объявление на проверку/ }).first()).toBeVisible();
  await page.reload();
  await nav(page, "Реклама").click();
  await expect(page.getByText("на проверке Яндекса")).toHaveCount(0);
});

test("переделка объявления: выдумка отклонена до писателя, выбранный вариант встаёт в очередь", async ({ page, request }) => {
  await page.goto("/?new");
  await nav(page, "Реклама").click();
  await page.getByRole("button", { name: "Открыть «Педикюр у метро Профсоюзная»" }).first().click();
  await page.getByRole("button", { name: "Переделать" }).click();

  // Скидки нет в профиле: сервис отказывает сразу, писатель не вызывается.
  await page.getByLabel("Или напишите своими словами").fill("Скидка 30% новым");
  await page.getByRole("button", { name: "Сделать" }).click();
  await expect(page.getByRole("alert")).toContainText("сначала добавьте это в профиль");

  await page.getByRole("button", { name: "Короче" }).click();
  const variants = page.getByRole("radiogroup", { name: "Новые варианты" });
  await expect(variants.getByText("Вариант 1")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Сейчас в показах")).toBeVisible();
  await expect(variants).toContainText("Профсоюзная, 12");
  await variants.getByRole("radio").first().check();
  await page.getByRole("button", { name: "Поставить в показы" }).click();
  await expect(notice(page)).toContainText("старое объявление работает");

  // Выбранный вариант виден в списке объявлений сервиса как ждущий запуска.
  const businesses = await (await request.get("/api/businesses")).json();
  const board = await (await request.get(`/api/businesses/${businesses[0].id}/creatives`)).json();
  expect(board.creatives.some((c: { state: string; text: string }) => c.state === "launch_pending" && c.text === "Ждём вас: Москва, ул. Профсоюзная, 12.")).toBe(true);
});

test("правка цены: объявления со старой ценой названы, замену пишет писатель и ждёт решения", async ({ page, request }) => {
  const settings = page.locator(".ah-bar").getByRole("button", { name: "Настройки" });
  await page.goto("/?new");
  await settings.click();
  const price = page.getByLabel("Цена услуги 1, ₽");
  await expect(price).toHaveValue("1500");
  await price.fill("1600");
  await page.getByRole("button", { name: "Сохранить" }).click();
  const status = page.getByRole("status").filter({ hasText: "Сохранил" });
  await expect(status).toContainText("«Маникюр с покрытием 1500 ₽» (1500)");
  await expect(status).toContainText("Пишу замену");

  // Замену пишет воркер стенда: цена в ней из нового профиля, без судьи её запускает владелец.
  const businesses = await (await request.get("/api/businesses")).json();
  const base = `/api/businesses/${businesses[0].id}`;
  type Row = { headline: string; text: string; state: string; reason: string };
  await expect(async () => {
    const board = await (await request.get(`${base}/creatives`)).json();
    const fresh = board.creatives.filter((c: Row) => c.reason === "fact_changed" && c.state === "needs_owner");
    expect(fresh.length).toBeGreaterThan(0);
    for (const c of fresh) expect(`${c.headline} ${c.text}`).not.toContain("1500");
    expect(fresh.some((c: Row) => c.headline.includes("1600"))).toBe(true);
  }).toPass({ timeout: 20_000 });

  await page.reload();
  await page.locator(".ah-rail").getByRole("button", { name: "Реклама" }).click();
  await expect(page.getByRole("region", { name: "Нужно ваше решение" })).toContainText("1600");

  // Вернуть цену: замена с 1600 сама устарела и снимается, остальные сценарии видят прежний профиль.
  await settings.click();
  await expect(page.getByLabel("Цена услуги 1, ₽")).toHaveValue("1600");
  await page.getByLabel("Цена услуги 1, ₽").fill("1500");
  await page.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Сохранил" })).toContainText("старых цифр нет");
  const board = await (await request.get(`${base}/creatives`)).json();
  expect(board.creatives.filter((c: Row) => c.reason === "fact_changed" && c.state === "needs_owner" && c.headline.includes("1600"))).toHaveLength(0);
});

test("чат: просьба становится командой из набора и меняет только после «Применить», вне набора честный отказ", async ({ page }) => {
  await page.goto("/?new");
  await nav(page, "Чат с ИИ").click();
  const input = page.getByLabel("Сообщение для ИИ");
  const last = page.getByRole("log", { name: "Сообщения" }).locator(".ah-chat-msg-ai").last();
  const say = async (text: string) => { await input.fill(text); await input.press("Enter"); };
  const apply = () => page.getByRole("group", { name: "Подсказки" }).getByRole("button", { name: "Применить" }).click();

  await say("сделай скидку 30% новым клиентам");
  await expect(last).toContainText("Скидки и акции беру только из профиля");
  await say("увеличь бюджет");
  await expect(last).toContainText("Какой бюджет поставить?");

  // Дни: каждое направление сохраняет свои часы, у «Наращивания» часы не прочитаны, его не трогаем.
  await say("не показывай рекламу по воскресеньям");
  await expect(last).toContainText("Понял так: не показывать рекламу: воскресенье.");
  await apply();
  await expect(last).toContainText("Больше не показываю рекламу: воскресенье");
  await expect(last).toContainText("У 1 направления часы ещё не прочитаны");
  await nav(page, "История").click();
  await expect(page.locator(".ah-hist-row", { hasText: "Изменил время показов" }).first()).toContainText("пн–сб");

  // Отмена: сначала последствия от сервиса, затем просьба целиком по всем направлениям.
  const changed = page.locator(".ah-hist-row", { hasText: "Изменил время показов" }).first();
  await changed.getByRole("button", { name: "Отменить" }).click();
  await expect(changed.locator(".ah-hist-note")).toContainText("Вернутся прежние часы показа");
  await expect(changed.locator(".ah-hist-note")).toContainText("Просьба меняла 3 направления");
  await changed.getByRole("button", { name: "Отменить для всех 3 направлений" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Отменил для всех 3 направлений" })).toBeVisible();
  // Обе отмены записаны новыми строками, исходные помечены и больше не предлагают отмену.
  await expect(page.locator(".ah-hist-row", { hasText: "Отмена. Изменил время показов" })).toHaveCount(3);
  const originals = page.locator(".ah-hist-row", { hasText: "Показываю рекламу пн–сб" }).filter({ hasNotText: "Отмена." });
  for (const row of [originals.nth(0), originals.nth(1), originals.nth(2)]) {
    await expect(row).toContainText("Отменено");
    await expect(row.getByRole("button", { name: "Отменить" })).toHaveCount(0);
  }

  // Цена услуги: через профиль, старые объявления названы. Затем возвращаем прежнюю.
  // Педикюр не трогаем: правка его цены честно снимает ждущее «Педикюр за 2200 ₽» из parity.
  await nav(page, "Чат с ИИ").click();
  await say("маникюр с покрытием теперь 1600");
  await expect(last).toContainText("«Маникюр с покрытием» теперь");
  await page.getByRole("group", { name: "Подсказки" }).getByRole("button", { name: "Не надо" }).click();
  await expect(last).toContainText("ничего не меняю");
  await say("маникюр с покрытием теперь 1600");
  await apply();
  await expect(last).toContainText("Цена «Маникюр с покрытием» в профиле теперь");
  await expect(last).toContainText("«Маникюр с покрытием 1500 ₽» (1500)");
  await say("маникюр с покрытием теперь 1500");
  await apply();
  await expect(last).toContainText("старых цифр нет");
});


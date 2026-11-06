import { StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { createApi, createBusiness, createGuestApi, openSession } from "./api";
import { AppShell } from "./app/AppShell";
import { loadDraft, loadStartReading, loadStartSite, loadWizard } from "./app/draft";
import { LiveStore, loadLive } from "./app/live";
import { initialState, Store } from "./app/store";
import { CONNECT_RESULT } from "./app/screens/LiveSettings";
import { Login } from "./app/screens/Login";
import "./app/base.css";

// `/` — кабинет на данных сервиса, а без входа или бизнеса — запуск (`/?start`), шаги идут через сервис.
// Подставной прототип (`/?mock`) есть только в сборке для разработки: владелец его не увидит.
function Root() {
  const params = new URLSearchParams(window.location.search);
  if (import.meta.env.DEV && params.has("mock")) return <AppShell />;
  if (params.has("start")) return <GuestStart />;
  return <Live />;
}

function GuestStart() {
  const store = useMemo(() => {
    const isNew = new URLSearchParams(window.location.search).get("start") === "new";
    const savedSite = loadStartSite();
    const draft = loadDraft();
    const created = new Store({
      ...initialState("start"),
      // Объявления прототипа в настоящем запуске не показываем: их пишет сервис на шаге «Реклама».
      ads: [],
      ...(savedSite ? { site: savedSite } : {}),
      ...(isNew ? { createAsNew: true } : {}),
      ...(draft ? {
        site: draft.site || savedSite,
        facts: draft.facts,
        f: { ...initialState("start").f, budget: draft.budget },
        step: draft.step ?? 0,
        draftOff: draft.draftOff ?? {},
        createAsNew: isNew ? true : draft.createAsNew,
      } : {}),
    });
    created.reader = createGuestApi();
    created.writer = created.reader as ReturnType<typeof createGuestApi>;
    created.transferDeps = { session: openSession, createBusiness, business: createApi, open: createApi };
    return created;
  }, []);
  useEffect(() => {
    const guest = createGuestApi();
    // Без ответа сервиса остаются пределы прототипа; сервис всё равно проверит сумму при сохранении.
    guest.budgetOptions().then((budgetOptions) => store.set({ budgetOptions }), () => undefined);
    openSession().then((session) => {
      const search = new URLSearchParams(window.location.search);
      const isNew = search.get("start") === "new";
      const hasConnect = search.has("connect");
      const wizardId = loadWizard();
      const draft = loadDraft();
      if (session && session.businesses.length > 0 && !isNew && !hasConnect && !wizardId && !draft) {
        window.location.replace("/");
        return;
      }
      if (session?.me?.email) {
        store.set({ account: { email: session.me.email }, businesses: session.businesses });
        if (draft && store.transferDeps) {
          void store.resume(store.transferDeps, login);
        }
      }
    }).catch(() => undefined);
    const params = new URLSearchParams(window.location.search);
    const login = params.get("login");
    const connect = params.get("connect");
    if (login !== null || connect !== null) window.history.replaceState(null, "", "/?start");
    if (connect !== null) {
      // С подключения кабинета: черновик уже перенесён, мастер продолжает с бизнесом.
      void store.resumeConnect({ session: openSession, open: createApi }, connect === "ok" ? null : CONNECT_RESULT[connect] ?? CONNECT_RESULT.failed!);
      return;
    }
    const pending = loadStartReading();
    if (pending && store.reader && store.state.step === 0 && !login) {
      void store.resumeReading(pending.id, pending.token, pending.url);
      return;
    }
    if (login !== null) {
      void store.resume({ session: openSession, createBusiness, business: createApi, open: createApi }, login);
    }
  }, [store]);
  return <AppShell store={store} />;
}

// Новый экран входа, а после входа:
// - человека (бизнеса) нет -> запускаем стартовый опрос
// - человек есть -> запускаем основное меню со всеми функциями
function Live() {
  const [store, setStore] = useState<LiveStore | "start" | "login" | "failed" | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);

  useEffect(() => {
    // В режиме разработки эффект запускается дважды. Без отмены второй магазин придёт позже
    // и заменит первый, в котором владелец уже успел перейти в другой раздел.
    let cancelled = false;
    const params = new URLSearchParams(window.location.search);
    const loginParam = params.get("login");
    if (loginParam !== null) {
      const err = loginParam === "denied"
        ? "Вход не состоялся: доступ в Яндексе не дан."
        : loginParam === "unavailable"
          ? "Вход сейчас не работает. Попробуйте позже."
          : "Вход не состоялся. Попробуйте ещё раз.";
      setLoginError(err);
      window.history.replaceState(null, "", "/");
    }

    openSession().then(async (session) => {
      if (cancelled) return;
      if (!session) {
        return setStore("login");
      }
      if (!session.businesses || session.businesses.length === 0) {
        return setStore("start");
      }
      let activeId: string | null = null;
      try { activeId = localStorage.getItem("adhelper.activeBusinessId"); } catch {}
      const business = session.businesses.find((b) => b.id === activeId) ?? session.businesses[0]!;
      const api = createApi(business.id, session.me.csrf_token);
      // Номер бизнеса нужен уже конструктору: по нему поднимается лента чата.
      const live = new LiveStore(api, { ...(await loadLive(api, business.name)), businesses: session.businesses, activeBusinessId: business.id });
      if (cancelled) return;
      const connect = params.get("connect");
      if (connect !== null) {
        live.set({ view: "settings", notice: CONNECT_RESULT[connect] ?? CONNECT_RESULT.failed! });
        window.history.replaceState(null, "", "/");
      }
      setStore(live);
    }).catch(() => cancelled || setStore("failed"));
    return () => { cancelled = true; };
  }, []);
  if (store === null) return <p className="ah-boot" role="status">Загружаем данные…</p>;
  if (store === "failed") return <p className="ah-boot" role="alert">Сервис не ответил. Обновите страницу через минуту.</p>;
  if (store === "login") return <Login error={loginError} />;
  if (store === "start") return <GuestStart />;
  return <AppShell store={store} />;
}

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);

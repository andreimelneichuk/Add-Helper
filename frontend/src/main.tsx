import { StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { createApi, createBusiness, createGuestApi, openSession } from "./api";
import { AppShell } from "./app/AppShell";
import { LiveStore, loadLive } from "./app/live";
import { initialState, Store } from "./app/store";
import { CONNECT_RESULT } from "./app/screens/LiveSettings";
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
    const created = new Store(initialState("start"));
    created.reader = createGuestApi();
    created.writer = created.reader as ReturnType<typeof createGuestApi>;
    return created;
  }, []);
  useEffect(() => {
    const guest = createGuestApi();
    // Без ответа сервиса остаются пределы прототипа; сервис всё равно проверит сумму при сохранении.
    guest.budgetOptions().then((budgetOptions) => store.set({ budgetOptions }), () => undefined);
    const params = new URLSearchParams(window.location.search);
    const login = params.get("login");
    const connect = params.get("connect");
    if (login !== null || connect !== null) window.history.replaceState(null, "", "/?start");
    if (connect !== null) {
      // С подключения кабинета: черновик уже перенесён, мастер продолжает с бизнесом.
      void store.resumeConnect({ session: openSession, open: createApi }, connect === "ok" ? null : CONNECT_RESULT[connect] ?? CONNECT_RESULT.failed!);
      return;
    }
    void store.resume({ session: openSession, createBusiness, business: createApi, open: createApi }, login);
  }, [store]);
  return <AppShell store={store} />;
}

// Есть вход и бизнес: настоящие данные. Нет: запуск с первого шага.
function Live() {
  const [store, setStore] = useState<LiveStore | "start" | "failed" | null>(null);
  useEffect(() => {
    // В режиме разработки эффект запускается дважды. Без отмены второй магазин придёт позже
    // и заменит первый, в котором владелец уже успел перейти в другой раздел.
    let cancelled = false;
    const params = new URLSearchParams(window.location.search);
    openSession().then(async (session) => {
      const business = session?.businesses[0];
      if (!session || !business) return cancelled || setStore("start");
      const api = createApi(business.id, session.me.csrf_token);
      const live = new LiveStore(api, await loadLive(api, business.name));
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
  if (store === "start") return <GuestStart />;
  return <AppShell store={store} />;
}

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);

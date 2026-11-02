import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createApi, openSession } from "./api";
import { App } from "./App";
import { AppShell } from "./app/AppShell";
import { LiveStore, loadLive } from "./app/live";
import { CONNECT_RESULT } from "./app/screens/LiveSettings";
import { Cabinet } from "./cabinet/Cabinet";
import "./styles.css";

type Session = Awaited<ReturnType<typeof openSession>>;

// Есть сервис и вход — кабинет на его данных. Нет — прототип мастера на тестовых данных.
function Root() {
  const params = new URLSearchParams(window.location.search);
  // Старый кабинет остаётся до конца этапа R7. Подключение кабинета начинают только в новом интерфейсе,
  // а Яндекс возвращает на `/?connect=…`, поэтому итог показываем там же.
  if (!params.has("new") && !params.has("connect")) return <Current />;
  // Запуск ещё не подключён к сервису, поэтому он всегда на подставных данных.
  if (params.has("start") || params.has("mock")) return <AppShell />;
  return <Live />;
}

// Есть вход и бизнес: настоящие данные. Нет: прототип с видимой полосой ситуаций, спутать нельзя.
function Live() {
  const params = new URLSearchParams(window.location.search);
  const [store, setStore] = useState<LiveStore | "mock" | "failed" | null>(null);
  useEffect(() => {
    // В режиме разработки эффект запускается дважды. Без отмены второй магазин придёт позже
    // и заменит первый, в котором владелец уже успел перейти в другой раздел.
    let cancelled = false;
    openSession().then(async (session) => {
      const business = session?.businesses[0];
      if (!session || !business) return cancelled || setStore("mock");
      const api = createApi(business.id, session.me.csrf_token);
      const live = new LiveStore(api, await loadLive(api, business.name));
      if (cancelled) return;
      const connect = params.get("connect");
      if (connect !== null) {
        live.set({ view: "settings", notice: CONNECT_RESULT[connect] ?? CONNECT_RESULT.failed! });
        window.history.replaceState(null, "", "/?new");
      }
      setStore(live);
    }).catch(() => cancelled || setStore("failed"));
    return () => { cancelled = true; };
  }, []);
  if (store === null) return null;
  if (store === "failed") return <p role="alert">Сервис не ответил. Обновите страницу через минуту.</p>;
  return store === "mock" ? <AppShell /> : <AppShell store={store} />;
}

function Current() {
  const [session, setSession] = useState<Session | "loading">("loading");
  useEffect(() => {
    openSession().then(setSession);
  }, []);
  if (session === "loading") return null;
  const business = session?.businesses[0];
  if (!session || !business) return <App />;
  return <Cabinet me={session.me} business={business} />;
}

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);

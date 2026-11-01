import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { openSession } from "./api";
import { App } from "./App";
import { Cabinet } from "./cabinet/Cabinet";
import "./styles.css";

type Session = Awaited<ReturnType<typeof openSession>>;

// Есть сервис и вход — кабинет на его данных. Нет — прототип мастера на тестовых данных.
function Root() {
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

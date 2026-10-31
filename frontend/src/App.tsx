import { useState } from "react";
import { History } from "./screens/History";
import { Home } from "./screens/Home";
import { Wizard } from "./wizard/Wizard";
import { STORAGE_KEY } from "./wizard/state";

type Screen = "wizard" | "home" | "history";

export function App() {
  const [screen, setScreen] = useState<Screen>("wizard");
  return (
    <>
      <p className="banner">Тестовый режим: все цифры условные, реклама не запускается, деньги не тратятся.</p>
      {screen === "wizard" && <Wizard onFinish={() => setScreen("home")} />}
      {screen === "home" && (
        <Home
          onHistory={() => setScreen("history")}
          onRestart={() => { localStorage.removeItem(STORAGE_KEY); setScreen("wizard"); }}
        />
      )}
      {screen === "history" && <History onHome={() => setScreen("home")} />}
    </>
  );
}

import { useMemo } from "react";
import { AppShell } from "../app/AppShell";
import { installFixtures } from "../app/fixtures";
import { initialState } from "../app/store";
import { mockFixtures } from "./fixtures";
import { MockStore } from "./MockStore";

// Подставной прототип: данные ставятся до создания магазина.
installFixtures(mockFixtures);

export function MockApp() {
  const store = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return new MockStore({ ...initialState(params.has("start") ? "start" : "work"), ...(params.get("view") === "ads" ? { view: "ads" as const } : {}) });
  }, []);
  return <AppShell store={store} />;
}

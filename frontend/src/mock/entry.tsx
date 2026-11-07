import { useMemo } from "react";
import { AppShell } from "../app/AppShell";
import { installFixtures } from "../app/fixtures";
import { initialState } from "../app/store";
import { mockFixtures } from "./fixtures";
import { MockStore } from "./MockStore";

// Подставной прототип: данные ставятся до создания магазина.
installFixtures(mockFixtures);

export function MockApp() {
  const store = useMemo(() => new MockStore(initialState(new URLSearchParams(window.location.search).has("start") ? "start" : "work")), []);
  return <AppShell store={store} />;
}

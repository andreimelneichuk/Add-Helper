import { AppShell } from "../app/AppShell";
import { installFixtures } from "../app/fixtures";
import { mockFixtures } from "./fixtures";

// Подставной прототип: данные ставятся до создания магазина.
installFixtures(mockFixtures);

export function MockApp() {
  return <AppShell />;
}

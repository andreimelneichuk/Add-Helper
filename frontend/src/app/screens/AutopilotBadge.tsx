import { AUTOPILOT_CHANGED } from "../autopilotEvents";
import { useEffect, useState } from "react";
import type { AutopilotView } from "../../api";
import { liveApi } from "../live";
import type { Store } from "../store";

const WORDS: Record<AutopilotView["mode"], string> = { watch: "только следит", confirm: "предлагает", auto: "действует сам" };

/** Плашка в шапке: что сейчас делает автопилот. Нажатие открывает его настройки. */
export function AutopilotBadge({ store, businessId, screen }: { store: Store; businessId: string | null; screen: string }) {
  const [view, setView] = useState<AutopilotView | null>(null);
  useEffect(() => {
    const api = liveApi(store);
    if (!api || typeof api.autopilot !== "function" || !businessId) return;
    let alive = true;
    const read = () => api.autopilot().then((v) => { if (alive) setView(v); }, () => undefined);
    void read();
    window.addEventListener(AUTOPILOT_CHANGED, read);
    return () => { alive = false; window.removeEventListener(AUTOPILOT_CHANGED, read); };
  }, [store, businessId, screen]);
  if (!view) return null;
  const text = view.status === "stopped" ? "остановлен" : WORDS[view.mode];
  return (
    <button type="button" className="ah-btn ah-btn-small ah-bar-autopilot" onClick={() => store.goView("settings")} aria-label={`Автопилот: ${text}. Открыть настройки`}>
      Автопилот: {text}
    </button>
  );
}

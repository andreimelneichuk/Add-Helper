import { useState } from "react";
import { MIN_BUDGET, MIN_GOAL, rub } from "../data";
import { fixtures } from "../fixtures";
import type { SettingKey } from "../data";
import { useStore } from "../store";
import type { Store } from "../store";
import { LiveSettings } from "./LiveSettings";
import "./Settings.css";

const settingKeys = () => Object.keys(fixtures.OPTIONS) as SettingKey[];
const OWN_MIN: Partial<Record<SettingKey, number>> = { budget: MIN_BUDGET, goal: MIN_GOAL };

// Своё значение задаётся кнопкой и подтверждается: смена бюджета меняет лимит в Яндексе.
function OwnAmount({ store, name, min }: { store: Store; name: SettingKey; min: number }) {
  const current = useStore(store, (x) => x.f[name]);
  const [draft, setDraft] = useState("");
  const value = Number(draft);
  const low = draft !== "" && value < min;
  const apply = () => {
    if (value < min || current === value) return;
    store.ask({ action: "setting", value: [name, value] });
    setDraft("");
  };
  return (
    <div className="set-own">
      <label className="set-own-field">
        <span className="ah-label">Своя сумма, ₽</span>
        <input className="ah-input" inputMode="numeric" autoComplete="off" placeholder={`От ${min}`} value={draft} onChange={(e) => setDraft(e.target.value.replace(/\D/g, "").slice(0, 8))} onKeyDown={(e) => { if (e.key === "Enter") apply(); }} />
      </label>
      <button type="button" className="ah-btn" disabled={draft === "" || low} onClick={apply}>Задать</button>
      {low && <div className="set-own-note">Минимум {rub(min)}</div>}
    </div>
  );
}

function Options({ store }: { store: Store }) {
  const f = useStore(store, (x) => x.f);
  return (
    <>
      {settingKeys().map((key) => {
        const group = fixtures.OPTIONS[key];
        return (
          <div key={key} role="group" aria-label={group.label}>
            <div className="ah-label">{group.label}</div>
            <div className="set-options">
              {group.values.map((v) => {
                const active = f[key] === v;
                return (
                  <button key={v} type="button" className="ah-chip" aria-pressed={active} onClick={() => { if (!active) store.ask({ action: "setting", value: [key, v] }); }}>
                    {typeof v === "number" ? rub(v) : v}
                  </button>
                );
              })}
              {typeof f[key] === "number" && !group.values.includes(f[key]) && (
                <span className="ah-chip" aria-current="true">{rub(Number(f[key]))}</span>
              )}
            </div>
            {OWN_MIN[key] !== undefined && <OwnAmount store={store} name={key} min={OWN_MIN[key]} />}
          </div>
        );
      })}
    </>
  );
}

export function Settings({ store }: { store: Store }) {
  const s = useStore(store, (x) => ({ live: !!x.live, price: x.price }));
  if (s.live) return <LiveSettings store={store} />;
  const profile = fixtures.TEXT.profile(s.price);
  return (
    <div className="set">
      <section className="ah-panel set-main" aria-labelledby="set-title">
        <h2 id="set-title" className="ah-panel-title">Настройки</h2>
        <Options store={store} />
        <p className="set-hint">Можно написать и в чат: «не показывай по воскресеньям». Любое изменение попадает в историю.</p>
      </section>
      <section className="ah-panel set-profile" aria-labelledby="set-profile-title">
        <h2 id="set-profile-title" className="ah-panel-title">Профиль бизнеса</h2>
        {profile.map(([label, value]) => (
          <div key={label}><span className="set-profile-label">{label}:</span> {value}</div>
        ))}
        <button type="button" className="ah-btn" onClick={() => store.openChat("Новая цена на маникюр 1400")}>Что-то изменилось</button>
      </section>
    </div>
  );
}

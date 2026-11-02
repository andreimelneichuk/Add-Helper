import { useState } from "react";
import type { ComponentType } from "react";
import { ACCOUNT, MIN_BUDGET, rub } from "../../data";
import { AdArt, Compose } from "../AdDetail";
import type { State, Store } from "../../store";

type StepProps = { store: Store; s: State };

const BUDGETS: [number, string][] = [[15000, "Примерно 25–35 обращений"], [20000, "Примерно 30–45 обращений"], [25000, "Примерно 40–55 обращений"]];

export const adsLeft = (s: State) => s.ads.filter((a) => !s.draftOff[a.id]).length;
export const innReady = (s: State) => s.inn.length === 10 || s.inn.length === 12;

function AboutStep({ store, s }: StepProps) {
  return (
    <>
      <label className="st-field">
        <span className="ah-label">Ссылка на сайт или страницу</span>
        <input className="ah-input st-input-big" inputMode="url" autoComplete="url" value={s.site} placeholder="salon-primer.ru" onChange={(e) => store.set({ site: e.target.value, block: "" })} />
      </label>
      <p className="ah-muted">Сайта нет? Подойдёт соцсеть или карточка на картах, либо расскажите о бизнесе в чате. Регистрироваться пока не нужно.</p>
    </>
  );
}

function FactEditor({ store, s, label, id }: StepProps & { label: string; id: string | null }) {
  const value = s.factEdit?.value ?? "";
  return (
    <div className="st-fact st-fact-editing">
      <label className="st-field">
        <span className="ah-label">{label}</span>
        {/* Шаг это форма: Enter здесь сохраняет факт, а не ведёт дальше. */}
        <input className="ah-input" autoFocus value={value} onChange={(e) => store.set({ factEdit: { id, value: e.target.value } })} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); store.saveFact(); } }} />
      </label>
      <div className="st-fact-actions">
        <button type="button" className="ah-btn ah-btn-primary" disabled={!value.trim()} onClick={store.saveFact}>Сохранить</button>
        <button type="button" className="ah-btn" onClick={() => store.set({ factEdit: null, block: "" })}>Отмена</button>
        {id !== null && <button type="button" className="ah-btn st-fact-remove" onClick={() => store.removeFact(id)}>Убрать</button>}
      </div>
    </div>
  );
}

function ProfileStep({ store, s }: StepProps) {
  const adding = s.factEdit?.id === null;
  return (
    <>
      <div className="st-grid-2">
        {s.facts.map((f) => (s.factEdit?.id === f.id ? <FactEditor key={f.id} store={store} s={s} label={f.label} id={f.id} /> : (
          <div key={f.id} className="st-fact">
            <div className="st-fact-head"><span>{f.label}</span><span>{f.source}</span></div>
            <div className="st-fact-value">{f.value}</div>
            <button type="button" className="ah-btn st-fact-edit" aria-label={`Изменить: ${f.label}`} onClick={() => store.set({ factEdit: { id: f.id, value: f.value }, block: "" })}>Изменить</button>
          </div>
        )))}
        {adding && <FactEditor store={store} s={s} label="Новый факт" id={null} />}
      </div>
      {!adding && <button type="button" className="ah-btn st-fact-add" onClick={() => store.set({ factEdit: { id: null, value: "" }, block: "" })}>Добавить факт</button>}
    </>
  );
}

// Та же грубая оценка, что у готовых вариантов: 1,5–2,25 обращения на 1 000 ₽, округлено до 5.
function estimate(budget: number) {
  const near5 = (n: number) => Math.max(5, Math.round(n / 5) * 5);
  return `Примерно ${near5(budget * 0.0015)}–${near5(budget * 0.00225)} обращений`;
}

function BudgetStep({ store, s }: StepProps) {
  const custom = s.budgetDraft !== null;
  const typed = Number(s.budgetDraft ?? 0);
  return (
    <>
      <div className="st-budgets">
        {BUDGETS.map(([value, hint]) => (
          <button key={value} type="button" className="st-budget" aria-pressed={!custom && s.f.budget === value} onClick={() => store.set({ f: { ...s.f, budget: value }, budgetDraft: null, block: "" })}>
            <strong>{rub(value)}</strong>
            <span>{hint}</span>
          </button>
        ))}
      </div>
      <label className={custom ? "st-budget-own is-on" : "st-budget-own"}>
        <span className="ah-label">Своя сумма в месяц, ₽</span>
        <input className="ah-input" inputMode="numeric" autoComplete="off" placeholder={`От ${MIN_BUDGET}`} value={s.budgetDraft ?? ""} onChange={(e) => {
          const draft = e.target.value.replace(/\D/g, "").slice(0, 8);
          const n = Number(draft);
          store.set({ budgetDraft: draft || null, block: "", ...(n >= MIN_BUDGET ? { f: { ...s.f, budget: n } } : {}) });
        }} />
        {custom && <span className="ah-muted">{typed >= MIN_BUDGET ? estimate(typed) : `Минимум ${rub(MIN_BUDGET)}`}</span>}
      </label>
      <p className="ah-muted">Это оценка, а не обещание. Больше выбранной суммы не потрачу: лимит будет стоять в самом Яндексе.</p>
    </>
  );
}

function AdsStep({ store, s }: StepProps) {
  const [editing, setEditing] = useState<string | null>(null);
  const target = s.ads.find((a) => a.id === editing);
  return (
    <>
    <div className="st-ads">
      {s.ads.map((a) => {
        const off = !!s.draftOff[a.id];
        const action = off ? "Вернуть" : "Убрать";
        return (
          <article key={a.id} className={off ? "st-ad is-off" : "st-ad"}>
            <AdArt ad={a} hook={a.hook} />
            <div className="st-ad-body">
              <h2 className="st-ad-title">{a.title}</h2>
              <p className="st-ad-text">{a.text}</p>
              <p className="st-ad-facts">{a.facts}</p>
              <button type="button" className="ah-btn st-ad-toggle" aria-label={`${action} «${a.name}»`} onClick={() => store.set({ draftOff: { ...s.draftOff, [a.id]: !off }, block: "" })}>{action}</button>
              <button type="button" className="ah-btn st-ad-toggle" aria-label={`Переделать «${a.name}»`} onClick={() => setEditing(a.id)}>Переделать</button>
            </div>
          </article>
        );
      })}
    </div>
    {target && (
      <Compose key={target.id} s={s} base={target} current="Сейчас" useLabel="Взять этот вариант" onCancel={() => setEditing(null)} onUse={(ad) => { store.set({ ads: s.ads.map((a) => (a.id === ad.id ? ad : a)) }); setEditing(null); }} />
    )}
    </>
  );
}

function YandexStep({ store, s }: StepProps) {
  return (
    <div className="st-link">
      <div className="st-link-text">
        <div className="st-link-title">Кабинет Яндекса</div>
        <div className="ah-muted">{s.yandex ? "Подключён: логин salon-primer" : "Вход и доступ к кабинету одним согласием. Кабинета нет? Покажу, как завести за 3 минуты."}</div>
      </div>
      <button type="button" className={s.yandex ? "ah-btn st-link-done" : "ah-btn ah-btn-primary"} onClick={() => store.set({ yandex: true, block: "" })}>{s.yandex ? "Подключено" : "Войти через Яндекс"}</button>
    </div>
  );
}

function InnStep({ store, s }: StepProps) {
  return (
    <label className="st-field st-inn">
      <span className="ah-label">ИНН для маркировки</span>
      <input className="ah-input" inputMode="numeric" autoComplete="off" maxLength={12} value={s.inn} placeholder="10 или 12 цифр" onChange={(e) => store.set({ inn: e.target.value.replace(/\D/g, ""), block: "" })} />
    </label>
  );
}

const CODE_LETTER = "Здравствуйте! Поставьте, пожалуйста, на сайт salon-primer.ru код статистики Яндекс Метрики на все страницы. Без него реклама не увидит заявки и звонки.";

function CodeStep({ store, s }: StepProps) {
  // Буфер обмена может быть недоступен, поэтому текст письма показываем и на экране.
  const copy = () => { void navigator.clipboard?.writeText(CODE_LETTER).catch(() => undefined); store.set({ codeCopied: true }); };
  return (
    <div className="st-link">
      <div className="st-link-text">
        <div className="st-link-title">Код статистики на сайте</div>
        <div className="ah-muted">{s.metrica ? "Код найден на сайте, обращения считаются" : "Поставьте код сами или перешлите письмо разработчику, а потом проверьте установку."}</div>
        {s.codeCopied && <p className="st-letter">{CODE_LETTER}</p>}
      </div>
      <div className="st-link-actions">
        <button type="button" className={s.metrica ? "ah-btn st-link-done" : "ah-btn ah-btn-primary"} onClick={() => store.set({ metrica: true, block: "" })}>{s.metrica ? "Проверено" : "Проверить установку"}</button>
        {!s.metrica && <button type="button" className="ah-btn" onClick={copy}>{s.codeCopied ? "Письмо скопировано" : "Скопировать письмо разработчику"}</button>}
      </div>
    </div>
  );
}

function LaunchStep({ s }: StepProps) {
  const rows = [
    { label: "Бюджет", value: `${rub(Number(s.f.budget))} в месяц` },
    { label: "Где покажем", value: "Поиск и сайты Яндекса" },
    { label: "Кому", value: "Жителям Сокольников" },
    { label: "Объявления", value: String(adsLeft(s)) },
    { label: "На счёте в Яндексе", value: rub(ACCOUNT.running) },
  ];
  return (
    <dl className="st-grid-2 st-rows" aria-label="Итоговая сводка">
      {rows.map((r) => (
        <div key={r.label} className="st-fact">
          <dt className="st-fact-head">{r.label}</dt>
          <dd className="st-fact-value">{r.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export const STEP_BODIES: ComponentType<StepProps>[] = [AboutStep, ProfileStep, BudgetStep, AdsStep, YandexStep, InnStep, CodeStep, LaunchStep];

import { useState } from "react";
import type { ComponentType } from "react";
import { ACCOUNT, MIN_BUDGET, rub } from "../../data";
import { AdArt, Compose } from "../AdDetail";
import { RealAds, RealCode, RealLaunch, RealMarking, RealYandex } from "./RealSteps";
import type { State, Store } from "../../store";

type StepProps = { store: Store; s: State };

const PRESETS = [15000, 20000, 25000];
// Сумму в день считает код: это деление, а не прогноз.
const perDay = (month: number) => `около ${rub(month / 30)} в день`;

export const adsLeft = (s: State) => s.ads.filter((a) => !s.draftOff[a.id]).length;
export const innReady = (s: State) => s.inn.length === 10 || s.inn.length === 12;

function AboutStep({ store, s }: StepProps) {
  const reading = s.reading !== null;
  return (
    <>
      <label className="st-field">
        <span className="ah-label">Ссылка на сайт или страницу</span>
        <input className="ah-input st-input-big" inputMode="url" autoComplete="url" value={s.site} placeholder="salon-primer.ru" readOnly={reading} onChange={(e) => store.set({ site: e.target.value, block: "" })} />
      </label>
      {reading && <p role="status" className="st-reading">Читаю сайт: главную и страницы с ценами и контактами. Обычно это занимает одну-две минуты.</p>}
      {store.reader ? (
        <>
          <p className="ah-muted">Регистрироваться пока не нужно. Сайта нет или он не читается? Заполните профиль сами.</p>
          <button type="button" className="ah-btn st-by-hand" onClick={store.fillByHand}>Заполнить вручную</button>
        </>
      ) : (
        <p className="ah-muted">Сайта нет? Подойдёт соцсеть или карточка на картах, либо расскажите о бизнесе в чате. Регистрироваться пока не нужно.</p>
      )}
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
            {f.quote && <blockquote className="st-fact-quote">«{f.quote}»</blockquote>}
            <button type="button" className="ah-btn st-fact-edit" aria-label={`Изменить: ${f.label}`} onClick={() => store.set({ factEdit: { id: f.id, value: f.value }, block: "" })}>Изменить</button>
          </div>
        )))}
        {adding && <FactEditor store={store} s={s} label="Новый факт" id={null} />}
      </div>
      {!adding && <button type="button" className="ah-btn st-fact-add" onClick={() => store.set({ factEdit: { id: null, value: "" }, block: "" })}>Добавить факт</button>}
    </>
  );
}

function BudgetStep({ store, s }: StepProps) {
  const custom = s.budgetDraft !== null;
  const typed = Number(s.budgetDraft ?? 0);
  const min = s.budgetOptions?.min ?? MIN_BUDGET;
  const max = s.budgetOptions?.max ?? Infinity;
  return (
    <>
      <div className="st-budgets">
        {(s.budgetOptions?.presets ?? PRESETS).map((value) => (
          <button key={value} type="button" className="st-budget" aria-pressed={!custom && s.f.budget === value} onClick={() => store.set({ f: { ...s.f, budget: value }, budgetDraft: null, block: "" })}>
            <strong>{rub(value)}</strong>
            <span>{perDay(value)}</span>
          </button>
        ))}
      </div>
      <label className={custom ? "st-budget-own is-on" : "st-budget-own"}>
        <span className="ah-label">Своя сумма в месяц, ₽</span>
        <input className="ah-input" inputMode="numeric" autoComplete="off" placeholder={`От ${min}`} value={s.budgetDraft ?? ""} onChange={(e) => {
          const draft = e.target.value.replace(/\D/g, "").slice(0, 8);
          const n = Number(draft);
          store.set({ budgetDraft: draft || null, block: "", ...(n >= min && n <= max ? { f: { ...s.f, budget: n } } : {}) });
        }} />
        {custom && <span className="ah-muted">{typed >= min ? perDay(typed) : `Минимум ${rub(min)}`}</span>}
      </label>
      <p className="ah-muted">Прогноз обращений покажу, когда подключите кабинет: его считает Яндекс по вашему району и услугам. Больше выбранной суммы не потрачу: лимит будет стоять в самом Яндексе.</p>
    </>
  );
}

function AdsStep({ store, s }: StepProps) {
  if (store.writer) return <RealAds store={store} s={s} />;
  return <MockAds store={store} s={s} />;
}

function MockAds({ store, s }: StepProps) {
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
  if (store.reader && s.account && store.wizard) return <RealYandex store={store} s={s} />;
  if (store.reader) {
    const signed = s.account !== null;
    return (
      <div className="st-link">
        <div className="st-link-text">
          <div className="st-link-title">Вход через Яндекс</div>
          <div className="ah-muted">
            {signed
              ? `${s.account?.email ? `Вы вошли: ${s.account.email}. ` : ""}Профиль и бюджет сохранены в сервисе. Кабинет Директа подключите в настройках: ИНН, код статистики и запуск появятся, когда Яндекс откроет доступ к API.`
              : "Войдите тем логином, где будет реклама. Профиль и бюджет сохранятся в сервисе, ничего вводить заново не придётся."}
          </div>
        </div>
        {!signed && <button type="button" className="ah-btn ah-btn-primary" onClick={store.login}>Войти через Яндекс</button>}
      </div>
    );
  }
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
  if (store.reader) return <RealMarking store={store} s={s} />;
  return (
    <label className="st-field st-inn">
      <span className="ah-label">ИНН для маркировки</span>
      <input className="ah-input" inputMode="numeric" autoComplete="off" maxLength={12} value={s.inn} placeholder="10 или 12 цифр" onChange={(e) => store.set({ inn: e.target.value.replace(/\D/g, ""), block: "" })} />
    </label>
  );
}

const CODE_LETTER = "Здравствуйте! Поставьте, пожалуйста, на сайт salon-primer.ru код статистики Яндекс Метрики на все страницы. Без него реклама не увидит заявки и звонки.";

function CodeStep({ store, s }: StepProps) {
  if (store.reader) return <RealCode store={store} s={s} />;
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

function LaunchStep({ store, s }: StepProps) {
  if (store.reader) return <RealLaunch store={store} s={s} />;
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

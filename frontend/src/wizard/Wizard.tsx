import { useEffect, useState, type ReactNode } from "react";
import { isValidInn } from "../inn";
import { formatRubles, rublesToMicros } from "../money";
import {
  INITIAL,
  LOW_BUDGET_RUBLES,
  STEPS,
  adsFromProfile,
  forecast,
  loadState,
  nextStep,
  previousStep,
  saveState,
  suggestedClientPrice,
  type WizardState,
} from "./state";

const CODE_SAMPLE = '<script src="https://mc.yandex.ru/metrika/tag.js"></script> <!-- образец -->';

interface FieldProps {
  label: string;
  hint?: string;
  value: string;
  onChange: (value: string) => void;
  numeric?: boolean;
  error?: string | undefined;
}

function Field({ label, hint, value, onChange, numeric, error }: FieldProps) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {hint && <span className="muted">{hint}</span>}
      <input
        value={value}
        inputMode={numeric ? "numeric" : "text"}
        aria-invalid={Boolean(error)}
        onChange={(event) => onChange(numeric ? event.target.value.replace(/\D/g, "") : event.target.value)}
      />
      {error && <span className="error" role="alert">{error}</span>}
    </label>
  );
}

export function Wizard({ onFinish }: { onFinish: () => void }) {
  const [state, setState] = useState<WizardState>(loadState);
  const [innTouched, setInnTouched] = useState(false);
  useEffect(() => saveState(state), [state]);

  const set = (patch: Partial<WizardState>) => setState((current) => ({ ...current, ...patch }));
  const forward = () => set({ step: nextStep(state.step) });
  const index = STEPS.indexOf(state.step);
  const budget = Number(state.budget);
  const check = Number(state.averageCheck);
  const clientPrice = Number(state.clientPrice);
  const estimate = forecast(budget, clientPrice);
  const innValid = isValidInn(state.inn, state.innKind);
  const ads = adsFromProfile(state).filter((ad) => !state.rejectedAds.includes(ad.id));

  let question: string;
  let body: ReactNode;
  let canContinue = true;
  let action = "Дальше";

  switch (state.step) {
    case "login":
      question = "Войдите, чтобы мы сохранили ваши настройки";
      canContinue = state.consent;
      action = "Войти через Яндекс";
      body = (
        <label className="check">
          <input type="checkbox" checked={state.consent} onChange={(event) => set({ consent: event.target.checked })} />
          <span>Даю согласие на обработку данных для ведения рекламы</span>
        </label>
      );
      break;
    case "account":
      question = "Подключите рекламный кабинет";
      canContinue = state.accountConnected;
      body = (
        <>
          <p>Кабинет — это место в Яндексе, где лежат деньги на рекламу. Мы сможем запускать рекламу, но снять деньги со счёта не сможем.</p>
          {state.accountConnected ? (
            <p className="ok" role="status">Подключён кабинет salon-test. На счёте {formatRubles(rublesToMicros(12_000))}.</p>
          ) : (
            <button type="button" className="secondary" onClick={() => set({ accountConnected: true })}>Подключить кабинет</button>
          )}
        </>
      );
      break;
    case "business":
      question = "Расскажите о бизнесе";
      canContinue = Boolean(state.businessName.trim() && state.service.trim());
      body = (
        <>
          <Field label="Сайт или страница" hint="Необязательно в этой версии" value={state.site} onChange={(site) => set({ site })} />
          <Field label="Название" value={state.businessName} onChange={(businessName) => set({ businessName })} />
          <Field label="Главная услуга" hint="Например, маникюр с покрытием" value={state.service} onChange={(service) => set({ service })} />
          <Field label="Цена услуги, ₽" hint="Покажем в рекламе только то, что вы укажете" numeric value={state.servicePrice} onChange={(servicePrice) => set({ servicePrice })} />
          <Field label="Адрес или район" value={state.address} onChange={(address) => set({ address })} />
          <Field label="Что отличает вас" hint="Например, стерильные инструменты" value={state.feature} onChange={(feature) => set({ feature })} />
        </>
      );
      break;
    case "marking":
      question = "Укажите данные для маркировки";
      canContinue = innValid && Boolean(state.legalName.trim());
      body = (
        <>
          <p>По закону реклама должна быть промаркирована. Это делает Яндекс, ему нужны ваши данные.</p>
          <label className="field">
            <span className="field-label">Кто вы</span>
            <select value={state.innKind} onChange={(event) => set({ innKind: event.target.value as WizardState["innKind"] })}>
              <option value="person">ИП или самозанятый</option>
              <option value="company">Организация</option>
            </select>
          </label>
          <Field label="Название или ФИО" value={state.legalName} onChange={(legalName) => set({ legalName })} />
          <Field
            label="ИНН"
            hint={state.innKind === "company" ? "10 цифр. Ради безопасности после перезагрузки страницы его нужно ввести снова" : "12 цифр. Ради безопасности после перезагрузки страницы его нужно ввести снова"}
            numeric
            value={state.inn}
            onChange={(inn) => { setInnTouched(true); set({ inn }); }}
            error={innTouched && state.inn && !innValid ? "Похоже, в ИНН ошибка. Проверьте цифры" : undefined}
          />
        </>
      );
      break;
    case "tracking":
      question = "Вставьте код статистики на сайт";
      canContinue = state.trackingChecked;
      body = (
        <>
          <p>Код считает заявки и звонки с сайта. Без него мы не поймём, работает ли реклама.</p>
          <pre className="code">{CODE_SAMPLE}</pre>
          {state.trackingChecked ? (
            <p className="ok" role="status">Код найден на сайте. В прототипе проверка условная.</p>
          ) : (
            <button type="button" className="secondary" onClick={() => set({ trackingChecked: true })}>Проверить</button>
          )}
        </>
      );
      break;
    case "budget":
      question = "Сколько вы готовы тратить в месяц?";
      canContinue = budget > 0;
      body = (
        <>
          <Field label="Бюджет в месяц, ₽" numeric value={state.budget} onChange={(value) => set({ budget: value })} />
          <p className="muted">Больше этой суммы не потратится: лимит выставляем прямо в Яндексе.</p>
          {budget > 0 && budget < LOW_BUDGET_RUBLES && (
            <p className="warn" role="alert">С таким бюджетом обращений может быть очень мало, и выводы о рекламе придётся ждать дольше.</p>
          )}
        </>
      );
      break;
    case "price":
      question = "Сколько можно отдать за нового клиента?";
      canContinue = clientPrice > 0;
      body = (
        <>
          <Field
            label="Средний чек, ₽"
            numeric
            value={state.averageCheck}
            onChange={(averageCheck) => {
              const suggestion = suggestedClientPrice(Number(averageCheck));
              set({ averageCheck, clientPrice: suggestion > 0 ? String(suggestion) : "" });
            }}
          />
          <Field label="Цена клиента, ₽" hint={check > 0 ? "Предложили 30% чека, можно изменить" : undefined} numeric value={state.clientPrice} onChange={(value) => set({ clientPrice: value })} />
          {estimate && (
            <p>
              Примерно {estimate.low}–{estimate.high} клиентов в месяц. Это грубая оценка, а не обещание: точнее скажем, когда появятся данные.
            </p>
          )}
        </>
      );
      break;
    case "ads":
      question = "Проверьте объявления";
      canContinue = ads.length > 0;
      body = (
        <>
          <p className="muted">В объявлениях только то, что вы указали о бизнесе.</p>
          {adsFromProfile(state).map((ad) => {
            const rejected = state.rejectedAds.includes(ad.id);
            return (
              <article className={rejected ? "card rejected" : "card"} key={ad.id}>
                <h3>{ad.title}</h3>
                <p>{ad.text}</p>
                <button
                  type="button"
                  className="link"
                  onClick={() => set({ rejectedAds: rejected ? state.rejectedAds.filter((id) => id !== ad.id) : [...state.rejectedAds, ad.id] })}
                >
                  {rejected ? "Вернуть" : "Не показывать"}
                </button>
              </article>
            );
          })}
          {ads.length === 0 && <p className="warn" role="alert">Оставьте хотя бы одно объявление.</p>}
        </>
      );
      break;
    case "launch":
      question = state.launched ? "Готово. В настоящем сервисе реклама ушла бы на проверку Яндексом" : "Всё верно?";
      action = state.launched ? "Перейти на главную" : "Запустить";
      body = (
        <ul className="summary">
          <li>Бюджет: {formatRubles(rublesToMicros(budget))} в месяц, больше не потратится</li>
          <li>Цена клиента: {formatRubles(rublesToMicros(clientPrice))}</li>
          <li>Объявлений: {ads.length}</li>
          <li>Где: Яндекс, поиск и реклама на сайтах и в приложениях</li>
          <li>Первые показы обычно после проверки Яндексом. Перед запуском пополните кабинет</li>
        </ul>
      );
      break;
  }

  const onPrimary = () => {
    if (state.step !== "launch") return forward();
    if (!state.launched) return set({ launched: true });
    setState(INITIAL);
    onFinish();
  };

  return (
    <main className="screen">
      <p className="muted" aria-label="Прогресс">Шаг {index + 1} из {STEPS.length}</p>
      <div className="progress" aria-hidden="true"><span style={{ width: `${((index + 1) / STEPS.length) * 100}%` }} /></div>
      <h1>{question}</h1>
      {body}
      <div className="actions">
        <button type="button" className="primary" disabled={!canContinue} onClick={onPrimary}>{action}</button>
        {index > 0 && !state.launched && (
          <button type="button" className="link" onClick={() => set({ step: previousStep(state.step) })}>Назад</button>
        )}
      </div>
    </main>
  );
}

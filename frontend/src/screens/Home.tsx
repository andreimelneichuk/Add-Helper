import { formatRubles } from "../money";
import { HOME } from "../testData";

export function Home({ onHistory, onRestart }: { onHistory: () => void; onRestart: () => void }) {
  const clientPrice = HOME.confirmedClients > 0 ? Math.round(HOME.spent / HOME.confirmedClients) : null;
  return (
    <main className="screen">
      <h1>Реклама работает</h1>
      <p className="ok" role="status">Проблем нет. Данные обновлены {HOME.updatedAt}.</p>
      <section>
        <h2>Что получено за {HOME.periodLabel}</h2>
        <dl className="metrics">
          <div><dt>Расходы</dt><dd>{formatRubles(HOME.spent)}</dd></div>
          <div><dt>Обращения</dt><dd>{HOME.requests}</dd></div>
          <div><dt>Клиенты, подтверждено вами</dt><dd>{HOME.confirmedClients}</dd></div>
          <div><dt>Цена клиента</dt><dd>{clientPrice === null ? "Пока мало данных" : formatRubles(clientPrice)}</dd></div>
        </dl>
      </section>
      <section>
        <h2>Что изменилось</h2>
        <p>Вчера перенесли 2 000 ₽ с «Педикюра» на «Маникюр»: там обращения дешевле.</p>
        <button type="button" className="link" onClick={onHistory}>Вся история</button>
      </section>
      <section>
        <h2>Деньги</h2>
        <p>Потрачено {formatRubles(HOME.spent)} из {formatRubles(HOME.budget)}.</p>
        <div className="progress" aria-hidden="true"><span style={{ width: `${(HOME.spent * 100) / HOME.budget}%` }} /></div>
        <p>К концу месяца ожидаем около {formatRubles(HOME.forecastSpent)}. В кабинете осталось {formatRubles(HOME.balance)}.</p>
      </section>
      <button type="button" className="link" onClick={onRestart}>Пройти настройку заново</button>
    </main>
  );
}

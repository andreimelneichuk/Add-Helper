import { useState, type ReactNode } from "react";

export type Column<Row> = {
  id: string;
  label: string;
  left?: boolean;
  title?: string;
  // Чем сортировать; без него столбец не сортируется. null уходит в конец.
  sort?: (row: Row) => number | string | null;
  cell: (row: Row) => ReactNode;
  foot?: ReactNode;
};

type Order = { id: string; desc: boolean } | null;

// Таблица отчёта: щелчок по заголовку сортирует, повторный — в обратную сторону.
export function SortTable<Row>({
  rows,
  columns,
  rowKey,
  onRow,
  initial = null,
  compact,
  empty = "Нет данных.",
}: {
  rows: Row[];
  columns: Column<Row>[];
  rowKey: (row: Row) => string;
  onRow?: (row: Row) => void;
  initial?: Order;
  compact?: boolean;
  empty?: string;
}) {
  const [order, setOrder] = useState<Order>(initial);
  if (rows.length === 0) return <p className="cab-empty">{empty}</p>;
  const column = order && columns.find((c) => c.id === order.id);
  const sorted = column?.sort
    ? [...rows].sort((a, b) => {
        const x = column.sort!(a);
        const y = column.sort!(b);
        if (x === y) return 0;
        if (x === null) return 1;
        if (y === null) return -1;
        return (x < y ? -1 : 1) * (order!.desc ? -1 : 1);
      })
    : rows;
  const toggle = (id: string) =>
    setOrder((now) => (now?.id === id ? { id, desc: !now.desc } : { id, desc: true }));
  const hasFoot = columns.some((c) => c.foot !== undefined);

  return (
    <div className="cab-table-wrap">
      <table className={compact ? "cab-table cab-table-compact" : "cab-table"}>
        <thead>
          <tr>
            {columns.map((c) => {
              const active = order?.id === c.id;
              return (
                <th
                  key={c.id}
                  className={c.left ? "cab-left" : undefined}
                  title={c.title}
                  aria-sort={active ? (order.desc ? "descending" : "ascending") : undefined}
                >
                  {c.sort ? (
                    <button type="button" className={active ? "cab-sort cab-sort-on" : "cab-sort"} onClick={() => toggle(c.id)}>
                      {c.label}
                      <span className="cab-sort-mark" aria-hidden="true">{active ? (order.desc ? "↓" : "↑") : "↕"}</span>
                    </button>
                  ) : (
                    c.label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={rowKey(row)} className={onRow ? "cab-row-link" : undefined} onClick={onRow ? () => onRow(row) : undefined}>
              {columns.map((c) => (
                <td key={c.id} className={c.left ? "cab-left" : undefined}>{c.cell(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
        {hasFoot && (
          <tfoot>
            <tr>
              {columns.map((c) => (
                <th key={c.id} className={c.left ? "cab-left" : undefined}>{c.foot ?? null}</th>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

import { DataModel as Model } from "../api";
import ERDiagram from "./ERDiagram";

const STATUS: Record<string, { label: string; cls: string }> = {
  used: { label: "used", cls: "tag tag-ok" },
  skipped: { label: "skipped", cls: "tag tag-warn" },
  rejected: { label: "rejected", cls: "tag tag-warn" },
  unused: { label: "unused", cls: "tag" },
};

export default function DataModel({ model }: { model: Model }) {
  const origins = Object.entries(model.origin).reduce<Record<string, number>>((acc, [, t]) => {
    acc[t] = (acc[t] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="datamodel">
      <p>
        The analysis table has <strong>one row per “{model.base_table}” row</strong> <span className="muted">({model.base_reason}).</span>
      </p>

      {model.tables.length > 1 && (
        <>
          <h4>How files connect</h4>
          <ERDiagram model={model} />
        </>
      )}

      <h4>Files</h4>
      <table className="table">
        <thead>
          <tr>
            <th>Table</th>
            <th>Rows</th>
            <th>Columns</th>
            <th>Columns in analysis table</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {model.tables.map((t) => {
            const inAnalysis = origins[t.name] ?? 0;
            const agg = origins[`${t.name} (aggregated)`] ?? 0;
            const unused = model.unused_tables.includes(t.name);
            return (
              <tr key={t.name}>
                <td>
                  <code>{t.name}</code> {t.name === model.base_table && <span className="tag tag-ok">base</span>}
                </td>
                <td>{t.rows.toLocaleString()}</td>
                <td>{t.cols}</td>
                <td>{inAnalysis + agg > 0 ? `${inAnalysis + agg}${agg ? ` (${agg} summarised)` : ""}` : "-"}</td>
                <td>{unused && <span className="tag tag-warn">not connected</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <h4>Links found</h4>
      {model.relationships.length === 0 ? (
        <p className="muted">No reliable key links were found between these tables.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Link</th>
              <th>Type</th>
              <th>Rows that match</th>
              <th>How it was used</th>
            </tr>
          </thead>
          <tbody>
            {model.relationships.map((r, i) => (
              <tr key={i}>
                <td>
                  <code>
                    {r.child_table}.{r.child_col}
                  </code>{" "}
                  →{" "}
                  <code>
                    {r.parent_table}.{r.parent_col}
                  </code>
                </td>
                <td>{r.cardinality}</td>
                <td>{(r.child_row_match * 100).toFixed(0)}%</td>
                <td>
                  <span className={(STATUS[r.status] ?? STATUS.unused).cls}>{(STATUS[r.status] ?? STATUS.unused).label}</span>{" "}
                  <span className="muted">{r.method === "aggregate" ? "summarised to one row per key. " : r.method === "join" ? "joined. " : ""}{r.reason}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {model.steps.length > 0 && (
        <>
          <h4>How files were joined</h4>
          <ol>
            {model.steps.map((s, i) => (
              <li key={i}>
                {s.detail} <span className="muted">(rows {s.rows_before.toLocaleString()} → {s.rows_after.toLocaleString()})</span>
              </li>
            ))}
          </ol>
        </>
      )}

      {model.warnings.length > 0 && (
        <>
          <h4>Things to check</h4>
          <ul>
            {model.warnings.map((w, i) => (
              <li key={i} className={w.severity === "warn" ? "warn-text" : ""}>
                {w.severity === "warn" ? "⚠ " : ""}
                {w.text}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

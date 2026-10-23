import { useState } from "react";
import { Finding } from "../api";

const SHOWN = 10;

/** A finding's table: ten rows, with a button to show all of them. */
export default function FindingTable({ f }: { f: Finding }) {
  const [all, setAll] = useState(false);
  if (!f.table) return null;
  const rows = f.table.rows;
  const total = f.table.total ?? rows.length;
  const shown = all ? rows : rows.slice(0, SHOWN);
  return (
    <div className="tablewrap">
      <table className="table">
        <thead>
          <tr>{f.table.columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i}>{r.map((v, j) => <td key={j} className={j > 0 ? "num" : ""}>{String(v)}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {total > SHOWN && (
        <p className="muted small">
          Showing {shown.length} of {total} rows.{" "}
          <button className="link" onClick={() => setAll(!all)}>{all ? "Show top 10" : `Show all ${total}`}</button>
        </p>
      )}
    </div>
  );
}

import { Finding } from "../api";

export default function FindingTable({ f }: { f: Finding }) {
  if (!f.table) return null;
  return (
    <div className="tablewrap">
      <table className="table">
        <thead>
          <tr>{f.table.columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {f.table.rows.map((r, i) => (
            <tr key={i}>{r.map((v, j) => <td key={j} className={j > 0 ? "num" : ""}>{String(v)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

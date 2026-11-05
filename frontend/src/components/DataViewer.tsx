import { useEffect, useState } from "react";
import State from "./State";
import { api, CleaningStep, DataCompare, DataPage, DataSource } from "../api";

const PAGE = 25;

/** Lets the user inspect the data exactly as the analysis used it, compare with the original, and see what cleaning changed. */
export default function DataViewer({ runId, log, after }: { runId: string; log?: CleaningStep[]; after?: React.ReactNode }) {
  const [source, setSource] = useState("clean");
  const [sources, setSources] = useState<DataSource[]>([]);
  const [offset, setOffset] = useState(0);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("");
  const [desc, setDesc] = useState(false);
  const [page, setPage] = useState<DataPage | null>(null);
  const [cmp, setCmp] = useState<DataCompare | null>(null);
  const [err, setErr] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(false);

  useEffect(() => {
    api.getCompare(runId).then(setCmp).catch(() => undefined);
    api.getSources(runId).then(setSources).catch(() => undefined);
  }, [runId]);

  useEffect(() => {
    setErr("");
    api.getData(runId, source, offset, PAGE, query, sort, desc).then(setPage).catch((e) => setErr(e.message));
  }, [runId, source, offset, query, sort, desc]);

  const changed = cmp?.columns.filter((c) => c.changed) ?? [];
  // every figure here is already in the run's own comparison and log; nothing is recalculated
  const colsKept = cmp ? cmp.columns.filter((c) => c.in_clean).length : 0;
  const colsOut = cmp ? cmp.columns.filter((c) => c.in_raw && !c.in_clean).map((c) => c.name) : [];
  const rowsOut = cmp ? Math.max(0, cmp.rows_before - cmp.rows_after) : 0;
  const fixes = (log ?? []).filter((s) => s.count > 0).length;
  const shownCols = (cmp?.columns ?? []).filter((c) => !onlyChanged || c.changed);

  function clickSort(col: string) {
    setOffset(0);
    if (sort === col) setDesc((d) => !d);
    else {
      setSort(col);
      setDesc(false);
    }
  }

  return (
    <div className="dataviewer">
      {cmp && (
        <div className="dt-tiles" data-kind="numbers" aria-label="Your data at a glance">
          <div className="dt-tile"><b>{cmp.rows_after.toLocaleString()}</b><span>Rows kept{rowsOut > 0 ? ` of ${cmp.rows_before.toLocaleString()}` : ""}</span></div>
          <div className="dt-tile"><b>{colsKept.toLocaleString()}</b><span>Columns</span></div>
          <div className="dt-tile"><b>{log ? fixes.toLocaleString() : changed.length.toLocaleString()}</b><span>{log ? (fixes === 1 ? "Fix made" : "Fixes made") : (changed.length === 1 ? "Column changed" : "Columns changed")}</span></div>
          <div className="dt-tile"><b>{rowsOut.toLocaleString()}</b><span>{rowsOut === 1 ? "Row left out" : "Rows left out"}</span></div>
        </div>
      )}
      {cmp && (rowsOut > 0 || colsOut.length > 0) && (
        <p className="dt-note" data-kind="caution">
          <b>Left out of the analysis:</b>{" "}
          {[rowsOut > 0 ? `${rowsOut.toLocaleString()} ${rowsOut === 1 ? "row" : "rows"} (removed while cleaning)` : "", colsOut.length ? `${colsOut.length === 1 ? "column" : "columns"} ${colsOut.join(", ")}` : ""].filter(Boolean).join(" and ")}. The original file is still there under Dataset.
        </p>
      )}
      {after}
      <p className="muted read">Browse the cleaned table the charts use, the original, or any uploaded file.</p>

      {cmp && (
        <details className="changes fold-working" open={changed.length > 0 && changed.length <= 8}>
          <summary>
            What cleaning changed: {changed.length === 0 ? "no column changed" : `${changed.length} of ${cmp.columns.length} columns`}
          </summary>
          <label className="check small">
            <input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} />
            <span>Show only changed columns</span>
          </label>
          <div className="tablewrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type before → after</th>
                  <th>Missing before → after</th>
                  <th>Distinct values</th>
                </tr>
              </thead>
              <tbody>
                {shownCols.map((c) => (
                  <tr key={c.name} className={c.changed ? "row-changed" : ""}>
                    <td>{c.name}</td>
                    <td>{c.in_raw ? c.type_before : "(added)"} → {c.in_clean ? c.type_after : "(left out)"}</td>
                    <td>{c.missing_before ?? "–"} → {c.missing_after ?? "–"}</td>
                    <td>{c.unique_after ?? "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      <div className="viewer-bar">
        <label className="srcpick">
          <span className="muted small">Dataset</span>
          <select value={source} onChange={(e) => { setSource(e.target.value); setOffset(0); setSort(""); }} aria-label="Which dataset to view">
            {(sources.length ? sources : [{ id: "clean", label: "Cleaned (used for analysis)" }, { id: "raw", label: "Original" }]).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
        </label>
        <form className="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setQuery(q); }}>
          <input aria-label="Search the data" placeholder="Search any value…" value={q} onChange={(e) => setQ(e.target.value)} />
          <button type="submit" className="chip">Search</button>
          {query && <button type="button" className="chip" onClick={() => { setQ(""); setQuery(""); setOffset(0); }}>Clear</button>}
        </form>
        <span className="spacer" />
        <a className="primary small" href={api.downloadUrl(runId, source)} download>Download this table (CSV)</a>
      </div>

      {err && <State kind="error" title="Could not load the table" compact>{err}. Pick the dataset again, or reload the page.</State>}
      {!page && !err && <State kind="loading" title="Loading the table" compact />}
      {page && (
        <>
          <div className="tablewrap data-scroll">
            <table className="table datatable">
              <thead>
                <tr>
                  <th className="rownum">#</th>
                  {page.columns.map((c) => (
                    <th key={c} onClick={() => clickSort(c)} className="sortable" aria-sort={sort === c ? (desc ? "descending" : "ascending") : "none"}>
                      {c} {sort === c ? (desc ? "▼" : "▲") : ""}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {page.rows.map((r, i) => (
                  <tr key={i}>
                    <td className="rownum">{page.offset + i + 1}</td>
                    {r.map((v, j) => (
                      <td key={j} className={v === null ? "na" : typeof v === "number" ? "num" : ""}>{v === null ? "missing" : String(v)}</td>
                    ))}
                  </tr>
                ))}
                {page.rows.length === 0 && (
                  <tr><td colSpan={page.columns.length + 1}>No rows match.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <button className="chip" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Previous</button>
            <span className="muted">
              Rows {page.total === 0 ? 0 : page.offset + 1}–{Math.min(page.offset + PAGE, page.total)} of {page.total.toLocaleString()}
            </span>
            <button className="chip" disabled={offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>Next →</button>
          </div>
        </>
      )}
    </div>
  );
}

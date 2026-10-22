import { useEffect, useState } from "react";
import { api, GlossaryEntry } from "../api";

type Draft = { workspace: string; entry: GlossaryEntry };

export default function Glossary() {
  const [spaces, setSpaces] = useState<Record<string, GlossaryEntry> | null>(null);
  const [error, setError] = useState("");
  const [edit, setEdit] = useState<Draft | null>(null);

  const load = () => api.listGlossary().then((r) => setSpaces(r.workspaces)).catch((e) => setError(e instanceof Error ? e.message : "Could not load the glossary"));
  useEffect(() => { load(); }, []);

  const save = async (workspace: string, entry: GlossaryEntry) => {
    setError("");
    try {
      await api.putGlossary(workspace, entry);
      setEdit(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
    }
  };
  const drop = async (workspace: string) => {
    if (!window.confirm(`Delete everything saved for "${workspace}"?`)) return;
    setError("");
    try {
      await api.deleteGlossary(workspace);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete");
    }
  };
  const without = (e: GlossaryEntry, kind: "answers" | "definitions", key: string): GlossaryEntry => {
    const next = { ...e, [kind]: { ...e[kind] } } as GlossaryEntry;
    delete (next[kind] as Record<string, unknown>)[key];
    return next;
  };

  return (
    <div className="card glossary">
      <h2>Business glossary</h2>
      <p className="muted">Answers and definitions saved per company. The next run with the same company name pre-fills them. This page has no login: anyone who can open the app can edit it.</p>
      {error && <div className="error">{error}</div>}
      {!spaces && !error && <p className="muted"><span className="spinner" /> Loading…</p>}
      {spaces && Object.keys(spaces).length === 0 && <p className="muted">Nothing saved yet. Answers are saved when you press Continue on the questions of a run.</p>}
      {spaces && Object.entries(spaces).map(([ws, e]) => (
        <section key={ws} className="gws">
          <header>
            <h3>{ws}</h3>
            <span>
              <button className="chip" onClick={() => setEdit({ workspace: ws, entry: structuredClone(e) })}>Edit</button>{" "}
              <button className="chip danger" onClick={() => drop(ws)}>Delete all</button>
            </span>
          </header>
          {Object.entries(e.answers).length > 0 && (
            <table className="gtable"><tbody>
              {Object.entries(e.answers).map(([id, a]) => (
                <tr key={id}>
                  <td>{a.question || id}</td>
                  <td><b>{a.label || a.value}</b></td>
                  <td><button className="chip danger" aria-label={`Delete ${id}`} onClick={() => save(ws, without(e, "answers", id))}>Delete</button></td>
                </tr>
              ))}
            </tbody></table>
          )}
          {Object.entries(e.definitions).length > 0 && (
            <ul className="gdefs">
              {Object.entries(e.definitions).map(([t, x]) => (
                <li key={t}><b>{t}:</b> {x} <button className="chip danger" onClick={() => save(ws, without(e, "definitions", t))}>Delete</button></li>
              ))}
            </ul>
          )}
          {e.notes && <p className="muted">Notes: {e.notes}</p>}
        </section>
      ))}

      {edit && (
        <div className="gedit card">
          <h3>Edit "{edit.workspace}"</h3>
          <p className="muted">Saved answers can be deleted from the list; they are picked from the question options, so they are not edited as free text.</p>
          {Object.entries(edit.entry.definitions).map(([t, x]) => (
            <label key={t} className="grow">
              <span>{t}</span>
              <input maxLength={300} value={x} onChange={(ev) => setEdit({ ...edit, entry: { ...edit.entry, definitions: { ...edit.entry.definitions, [t]: ev.target.value } } })} />
            </label>
          ))}
          <label className="grow">
            <span>Notes</span>
            <textarea rows={2} maxLength={500} value={edit.entry.notes} onChange={(ev) => setEdit({ ...edit, entry: { ...edit.entry, notes: ev.target.value } })} />
          </label>
          <div className="qactions">
            <button className="primary small" onClick={() => save(edit.workspace, edit.entry)}>Save</button>
            <button className="chip" onClick={() => setEdit(null)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

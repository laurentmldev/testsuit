// The configuration in use, with where each value comes from (tsq config).
import { useResource } from "../api";

export function ConfigPage({ editor }: { editor: boolean }) {
  const config = useResource<any[]>("config");
  return (
    <div className="page">
      <div className="page-head"><h2>Configuration</h2><span className="muted">the main configuration file, environment variables and defaults, as tsqd uses them</span>
        <span className="spacer" />
        {editor && <a href="#/edit" title="Every editing session, and the repositories they can open">Editing sessions</a>}
      </div>
      {config.error && <div className="error-box">{config.error}</div>}
      <table className="grid compact">
        <thead><tr><th>Setting</th><th>Value</th><th>From</th></tr></thead>
        <tbody>
          {(config.data || []).map((c) => (
            <tr key={c.key}><td><code>{c.key}</code></td><td className="mono">{typeof c.value === "object" ? JSON.stringify(c.value) : String(c.value)}</td><td className="muted">{c.source}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

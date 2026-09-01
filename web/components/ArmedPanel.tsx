import type { ReactElement } from "react";

import { formatElapsed, formatToolArgs } from "@/lib/text";
import type { PendingItem } from "@/lib/types";

export function ArmedPanel({ item }: { item: PendingItem }): ReactElement {
  const policy = item.policy
    ? `${String(item.policy.tool_pattern ?? "—")} ${String(item.policy.action ?? "—")}`
    : "escalate (no row)";

  return (
    <article className="armed">
      <header className="armed-head">
        <p className="kicker">armed</p>
        <p className="elapsed" data-testid="elapsed">
          {formatElapsed(item.elapsed_s)}
        </p>
      </header>
      <p className="short-code">{item.short_code ?? "—"}</p>
      <dl className="meta">
        <div>
          <dt>request_id</dt>
          <dd>{item.request_id}</dd>
        </div>
        <div>
          <dt>actor</dt>
          <dd>{item.actor}</dd>
        </div>
        <div>
          <dt>tool_name</dt>
          <dd>{item.tool_name}</dd>
        </div>
        <div>
          <dt>risk_class</dt>
          <dd>{item.risk_class}</dd>
        </div>
        <div>
          <dt>relay_gated</dt>
          <dd>{item.relay_gated ? "true" : "false"}</dd>
        </div>
        <div>
          <dt>dial</dt>
          <dd>{String(item.dial)}</dd>
        </div>
        <div>
          <dt>policy</dt>
          <dd>{policy}</dd>
        </div>
      </dl>
      <section>
        <h2>justification</h2>
        <p className="untrusted" data-testid="justification">
          {item.justification}
        </p>
      </section>
      <section>
        <h2>tool_args</h2>
        <pre className="untrusted" data-testid="tool-args">
          {formatToolArgs(item.tool_args)}
        </pre>
      </section>
      <section>
        <h2>reasoning</h2>
        <p className="untrusted">{item.reasoning || "—"}</p>
      </section>
    </article>
  );
}

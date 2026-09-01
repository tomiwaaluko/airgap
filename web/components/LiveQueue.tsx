"use client";

import { useEffect, useState, type ReactElement } from "react";

import { ArmedPanel } from "@/components/ArmedPanel";
import { useSnapshot } from "@/components/SnapshotProvider";
import { formatElapsed } from "@/lib/text";

export function LiveQueue(): ReactElement {
  const snapshot = useSnapshot();
  const armed = snapshot.pending.armed;
  const [now, setNow] = useState(snapshot.at);

  useEffect(() => {
    setNow(Date.now());
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [snapshot.at]);

  const drift = Math.max(0, Math.floor((now - snapshot.at) / 1000));

  return (
    <div className="queue-page">
      {armed ? (
        <ArmedPanel item={{ ...armed, elapsed_s: armed.elapsed_s + drift }} />
      ) : (
        <article className="idle">
          <p className="kicker">queue</p>
          <p className="idle-label">IDLE</p>
          <p>No request is armed. Waiting is the product.</p>
        </article>
      )}
      <section className="waiting">
        <h2>waiting · {snapshot.pending.queue.length}</h2>
        {snapshot.pending.queue.length === 0 ? (
          <p>Empty.</p>
        ) : (
          <ul>
            {snapshot.pending.queue.map((row) => (
              <li key={row.request_id}>
                <span>{row.request_id}</span>
                <span>{row.tool_name}</span>
                <span>{formatElapsed(row.elapsed_s + drift)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

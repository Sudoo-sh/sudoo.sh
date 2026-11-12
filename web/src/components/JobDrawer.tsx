import { Check, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { useJob, useJobLog } from "@/api/queries";
import { ago, elapsed } from "@/lib/format";
import { Callout, Progress, Spinner, StatusPill } from "./ui/basics";
import { Drawer } from "./ui/overlay";

function colour(line: string, index: number) {
  if (line.startsWith("== ")) return <span key={index} className="step">{line + "\n"}</span>;
  if (line.startsWith("$ ")) return <span key={index} className="cmd">{line + "\n"}</span>;
  if (line.startsWith("[warning]")) return <span key={index} className="warn">{line + "\n"}</span>;
  if (line.startsWith("ERROR") || /Traceback|Error:/.test(line)) return <span key={index} className="err">{line + "\n"}</span>;
  return line + "\n";
}

export function Terminal({ text, live }: { text: string; live?: boolean }) {
  const ref = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [text]);
  return (
    <pre
      className="terminal"
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
    >
      {text.trimEnd().split("\n").slice(-3000).map(colour)}
      {live && <span className="cursor" />}
    </pre>
  );
}

/** A job: its steps as a timeline, notes, the reason it failed, and its technical log, all live. */
export function JobDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: job } = useJob(id);
  const { data: log } = useJobLog(id);
  const running = job?.status === "running" || job?.status === "queued";
  return (
    <Drawer
      onClose={onClose}
      title={
        <div className="stack-sm" style={{ gap: 4 }}>
          <div className="row">
            <h2>{job?.action ?? "Activity"}</h2>
            {job && <StatusPill status={job.status} />}
          </div>
          {job && (
            <span className="small muted">
              {job.instance} · by {job.requested_by} · started {ago(job.started_at)}
              {job.started_at && ` · ${elapsed(job.started_at, job.finished_at)}`}
            </span>
          )}
        </div>
      }
    >
      {!job ? (
        <Spinner />
      ) : (
        <div className="stack">
          <Progress value={job.progress_percent} status={job.status} />
          {job.error && <Callout tone="danger">{job.error}</Callout>}
          {job.notes.map((note, i) => (
            <Callout key={i} tone={note.level === "warning" ? "warning" : "info"}>
              {note.text}
            </Callout>
          ))}
          <ol className="timeline">
            {job.steps.map((step, i) => (
              <li key={i}>
                <span className={`node ${step.status}`}>
                  {step.status === "done" ? <Check /> : step.status === "failed" ? <X /> : <span className="spinner" />}
                </span>
                <span className={step.status === "running" ? "" : "muted"} style={{ fontWeight: step.status === "running" ? 600 : 450 }}>
                  {step.title}
                </span>
              </li>
            ))}
            {!job.steps.length && <li className="faint">Waiting to start…</li>}
          </ol>
          <div className="section-title">
            <h2>Technical log</h2>
            <span className="sub">for developers</span>
          </div>
          <Terminal text={log?.text ?? ""} live={running} />
        </div>
      )}
    </Drawer>
  );
}

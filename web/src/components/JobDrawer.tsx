import { Check, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { useJob, useJobLog } from "@/api/queries";
import type { Job } from "@/api/types";
import { useT } from "@/i18n";
import instanceWords from "@/i18n/instance";
import { jobTitle, stepTitle } from "@/i18n/jobs";
import { ago, elapsed } from "@/lib/format";
import { Callout, Progress, Spinner, Status } from "./ui/basics";
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

/** A job's steps as a timeline: done, failed, or the one running now. */
export function JobSteps({ job }: { job: Pick<Job, "steps"> }) {
  const t = useT(instanceWords).job;
  return (
    <ol className="timeline">
      {job.steps.map((step, i) => (
        <li key={i}>
          <span className={`node ${step.status}`}>
            {step.status === "done" ? <Check /> : step.status === "failed" ? <X /> : <span className="spinner" />}
          </span>
          <span className={step.status === "running" ? "" : "muted"}>
            {stepTitle(step.title)}
          </span>
        </li>
      ))}
      {!job.steps.length && <li className="faint">{t.waiting}</li>}
    </ol>
  );
}

/** A job: its steps as a timeline, notes, the reason it failed, and its technical log, all live. */
export function JobDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: job } = useJob(id);
  const { data: log } = useJobLog(id);
  const running = job?.status === "running" || job?.status === "queued";
  const t = useT(instanceWords).job;
  return (
    <Drawer
      onClose={onClose}
      title={
        <div className="stack-sm" style={{ gap: 4 }}>
          <div className="row">
            <h2>{job ? jobTitle(job.action) : t.activity}</h2>
            {job && <Status status={job.status} />}
          </div>
          {job && (
            <span className="small muted">
              {t.meta(job.instance, job.requested_by, ago(job.started_at))}
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
          <JobSteps job={job} />
          <h2 className="small muted">{t.technicalLog}</h2>
          <Terminal text={log?.text ?? ""} live={running} />
        </div>
      )}
    </Drawer>
  );
}

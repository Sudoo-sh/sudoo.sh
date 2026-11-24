// Import database (the instance's Backups card): upload an Odoo dump from elsewhere and put it into the instance
// (web/v1/imports.py). The file goes up in pieces; a dropped connection is retried, and choosing the same file again
// after a reload continues the earlier upload. Production asks for "production" to be typed. Also the dialog that
// asks for an optional comment before "Back up now".

import { useQueryClient } from "@tanstack/react-query";
import { DatabaseBackup, FileUp, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api, ApiError, csrfHeaders, errorText } from "@/api/client";
import { keys } from "@/api/queries";
import type { Instance, Job } from "@/api/types";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import toolWords from "@/i18n/tools";
import { bytes } from "@/lib/format";
import { Button, Callout, Field, Progress, Switch } from "./app/basics";
import { Modal, useToast } from "./app/overlay";
import { Input } from "./ui/input";

interface Upload {
  id: string;
  file_name: string;
  format: string;
  size: number;
  received: number;
  complete: boolean;
  chunk_size: number;
}

const ACCEPTED = /\.(zip|dump|sql|sql\.gz)$/i;
const TRIES = 8;
const storeKey = (instance: string) => `sudoo.import.${instance}`;

interface Remembered {
  id: string;
  name: string;
  size: number;
  modified: number;
}

function remembered(instance: string): Remembered | null {
  try {
    return JSON.parse(localStorage.getItem(storeKey(instance)) ?? "null") as Remembered | null;
  } catch {
    return null;
  }
}
function remember(instance: string, value: Remembered | null) {
  try {
    if (value) localStorage.setItem(storeKey(instance), JSON.stringify(value));
    else localStorage.removeItem(storeKey(instance));
  } catch {
    // a private window: an interrupted upload starts again from the beginning
  }
}

const wait = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("aborted", "AbortError"));
      },
      { once: true },
    );
  });

/** Send one piece; the upload as the server has it after it (also after a 409: where to continue). */
async function sendPiece(
  instance: string,
  upload: Upload,
  offset: number,
  piece: Blob,
  signal: AbortSignal,
): Promise<Upload> {
  const response = await fetch(`/api/v1/instances/${instance}/imports/${upload.id}?offset=${offset}`, {
    method: "PUT",
    headers: { "Content-Type": "application/octet-stream", Accept: "application/json", ...csrfHeaders() },
    credentials: "same-origin",
    body: piece,
    signal,
  });
  const json = await response.json().catch(() => ({}));
  if (response.status === 409) return { ...upload, received: Number(json.meta?.received ?? offset) };
  if (!response.ok) throw new ApiError(response.status, json.detail ?? response.statusText);
  return json.data as Upload;
}

export function ImportDatabaseButton({ instance, busy }: { instance: Instance; busy: boolean }) {
  const t = useT(toolWords).imports;
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="sm" variant="ghost" icon={<FileUp />} disabled={busy} onClick={() => setOpen(true)}>
        {t.button}
      </Button>
      {open && <ImportDialog instance={instance} onClose={() => setOpen(false)} />}
    </>
  );
}

function ImportDialog({ instance, onClose }: { instance: Instance; onClose: () => void }) {
  const t = useT(toolWords).imports;
  const c = useT(common).common;
  const toast = useToast();
  const client = useQueryClient();
  const production = instance.stage === "production";
  const [file, setFile] = useState<File | null>(null);
  const [neutralize, setNeutralize] = useState(!production);
  const [typed, setTyped] = useState("");
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const running = useRef<AbortController | null>(null);
  const uploadId = useRef<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  const choose = (chosen: File | null | undefined) => {
    setError(null);
    if (!chosen) return;
    if (!ACCEPTED.test(chosen.name)) {
      setError(t.unsupported);
      return;
    }
    setFile(chosen);
  };

  const start = async (chosen: File) => {
    const controller = new AbortController();
    running.current = controller;
    setError(null);
    setNote(null);
    setProgress({ done: 0, total: chosen.size });
    try {
      let upload: Upload | null = null;
      const earlier = remembered(instance.name);
      if (
        earlier &&
        earlier.name === chosen.name &&
        earlier.size === chosen.size &&
        earlier.modified === chosen.lastModified
      ) {
        upload = await api.get<Upload>(`/instances/${instance.name}/imports/${earlier.id}`).catch(() => null);
        if (upload?.received) setNote(t.resumed(bytes(upload.received)));
      }
      if (!upload) {
        upload = await api.post<Upload>(`/instances/${instance.name}/imports`, {
          file_name: chosen.name,
          size: chosen.size,
        });
        remember(instance.name, { id: upload.id, name: chosen.name, size: chosen.size, modified: chosen.lastModified });
      }
      uploadId.current = upload.id;
      let failures = 0;
      while (upload.received < chosen.size) {
        const offset: number = upload.received;
        try {
          upload = await sendPiece(
            instance.name,
            upload,
            offset,
            chosen.slice(offset, offset + upload.chunk_size),
            controller.signal,
          );
          failures = 0;
          setNote((n) => (n === t.retrying ? null : n));
        } catch (problem) {
          if (controller.signal.aborted) throw problem;
          // a refusal (4xx) is final; a dropped connection or a busy server is tried again, a little later each time
          if (problem instanceof ApiError && problem.status >= 400 && problem.status < 500) throw problem;
          if (++failures > TRIES) throw problem;
          setNote(t.retrying);
          await wait(Math.min(30_000, 1000 * 2 ** failures), controller.signal);
          upload =
            (await api.get<Upload>(`/instances/${instance.name}/imports/${upload.id}`).catch(() => null)) ?? upload;
        }
        setProgress({ done: upload.received, total: chosen.size });
      }
      setNote(t.checking);
      const job = await api.post<Job>(`/instances/${instance.name}/imports/${upload.id}/restore`, {
        neutralize,
        ...(production ? { confirm_production: true, confirm_stage: "production" } : {}),
      });
      remember(instance.name, null);
      toast("success", t.started, t.startedText);
      await Promise.all(
        [keys.instance(instance.name), keys.jobs(instance.name)].map((queryKey) =>
          client.invalidateQueries({ queryKey }),
        ),
      );
      onClose();
      return job;
    } catch (problem) {
      if (controller.signal.aborted) return;
      setProgress(null);
      setNote(null);
      setError(errorText(problem));
    } finally {
      running.current = null;
    }
  };

  const cancel = () => {
    running.current?.abort();
    if (uploadId.current) {
      api.del(`/instances/${instance.name}/imports/${uploadId.current}`).catch(() => undefined);
      remember(instance.name, null);
    }
    onClose();
  };

  const uploading = progress !== null;
  const ready = !!file && !uploading && (!production || typed === "production");
  const percent = progress && progress.total ? Math.round((100 * progress.done) / progress.total) : 0;
  return (
    <Modal
      wide
      title={t.title}
      description={t.text(instance.name)}
      onClose={uploading ? cancel : onClose}
      footer={
        <>
          <Button onClick={cancel}>{uploading ? t.cancel : c.cancel}</Button>
          <Button
            variant="danger"
            icon={<Upload />}
            disabled={!ready}
            loading={uploading}
            onClick={() => file && start(file)}
          >
            {t.upload}
          </Button>
        </>
      }
    >
      <div className="stack">
        {production && (
          <Callout tone="danger">
            <b>{t.productionWarning}</b>
          </Callout>
        )}
        <label
          className="drop-zone"
          data-dragging={dragging || undefined}
          data-disabled={uploading || undefined}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (!uploading) choose(e.dataTransfer.files[0]);
          }}
        >
          <input
            ref={picker}
            type="file"
            accept=".zip,.dump,.sql,.gz"
            disabled={uploading}
            onChange={(e) => choose(e.target.files?.[0])}
          />
          <DatabaseBackup aria-hidden="true" />
          {file ? (
            <span className="stack-xs">
              <b className="mono" dir="ltr">
                {file.name}
              </b>
              <span className="xsmall faint">{bytes(file.size)}</span>
            </span>
          ) : (
            <span className="stack-xs">
              <b>{t.drop}</b>
              <span className="xsmall faint">{t.formats}</span>
            </span>
          )}
        </label>
        <p className="small muted">{t.version(instance.odoo.version)}</p>
        <Switch
          checked={neutralize}
          onChange={setNeutralize}
          disabled={uploading}
          label={
            <span>
              {t.neutralize} <span className="xsmall faint">{t.neutralizeText}</span>
            </span>
          }
        />
        {!neutralize && <Callout tone="warning">{t.notNeutralized}</Callout>}
        {production && (
          <Field label={<>{t.typeProduction(<code key="p">production</code>)}</>}>
            <Input dir="ltr" value={typed} disabled={uploading} onChange={(e) => setTyped(e.target.value)} />
          </Field>
        )}
        {progress && (
          <div className="stack-xs" aria-live="polite">
            <Progress value={percent} />
            <span className="xsmall muted">{t.uploading(bytes(progress.done), bytes(progress.total))}</span>
          </div>
        )}
        {note && (
          <p className="xsmall muted" aria-live="polite">
            {note}
          </p>
        )}
        {error && <Callout tone="danger">{error}</Callout>}
        <p className="xsmall faint">
          {t.before} {t.passwords}
        </p>
      </div>
    </Modal>
  );
}

/** "Back up now": an optional comment, kept with the backup. */
export function BackupCommentDialog({ onClose, onRun }: { onClose: () => void; onRun: (comment: string) => void }) {
  const t = useT(toolWords).backups;
  const c = useT(common).common;
  const [comment, setComment] = useState("");
  const run = () => {
    onRun(comment.trim());
    onClose();
  };
  return (
    <Modal
      title={t.backUpTitle}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button variant="primary" icon={<DatabaseBackup />} onClick={run}>
            {t.backUp}
          </Button>
        </>
      }
    >
      <Field label={t.commentLabel} hint={t.commentHint}>
        <Input
          autoFocus
          maxLength={200}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              run();
            }
          }}
        />
      </Field>
    </Modal>
  );
}

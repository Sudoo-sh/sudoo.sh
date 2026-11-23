// A commit's short hash (seven characters, monospace), as the history, the Builds tab and an instance's header show
// it: a press copies the whole hash, and the arrow beside it opens the commit on GitHub in a new tab. The arrow is
// there only for repositories on github.com (lib/git.ts githubCommitUrl); elsewhere the hash only copies.

import { ArrowUpRight, Check, Copy, GitCommitHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useT } from "@/i18n";
import words from "@/i18n/builds";
import { githubCommitUrl, shortSha } from "@/lib/git";

export function CommitHash({ sha, repository, icon = false }: { sha: string; repository: string | null | undefined; icon?: boolean }) {
  const t = useT(words).history;
  const [copied, setCopied] = useState(false);
  const timer = useRef<number>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const url = githubCommitUrl(repository, sha);
  const short = shortSha(sha);
  const copy = async () => {
    await navigator.clipboard.writeText(sha).catch(() => undefined);
    setCopied(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1400);
  };
  return (
    <span className="commit-hash" data-linked={url ? "" : undefined}>
      <button type="button" className="ch-copy" onClick={copy} title={copied ? t.copied : t.copySha} aria-label={`${t.copySha}: ${short}`}>
        {icon && <GitCommitHorizontal className="ch-icon" aria-hidden />}
        <code dir="ltr">{short}</code>
        {copied ? <Check className="ch-state" aria-hidden /> : <Copy className="ch-state" aria-hidden />}
      </button>
      {url && (
        <a className="ch-link" href={url} target="_blank" rel="noreferrer" title={t.onGitHub} aria-label={`${t.onGitHub}: ${short}`}>
          <ArrowUpRight className="flip-rtl" aria-hidden />
        </a>
      )}
    </span>
  );
}

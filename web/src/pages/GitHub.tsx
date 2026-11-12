import { ArrowUpRight, FolderGit2, KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall, useDeployKeys } from "@/api/queries";
import { Page, PageHead } from "@/components/Shell";
import { Button, Callout, CopyField, Empty, Field, Skeleton } from "@/components/ui/basics";
import { Modal, useToast } from "@/components/ui/overlay";
import { ago } from "@/lib/format";

export function GitHubPage() {
  const { data: keysList, isPending } = useDeployKeys();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [repo, setRepo] = useState("");
  const [made, setMade] = useState<{ repo: string; public_key: string; github_url: string } | null>(null);
  const create = useCall(() => api.post<{ repo: string; public_key: string; github_url: string }>("/deploy-keys", { repo: repo.trim() }), [["deploy-keys"]]);
  const remove = useCall((rid: string) => api.del(`/deploy-keys/${rid}`), [["deploy-keys"]]);
  return (
    <Page>
      <PageHead eyebrow="Operations" title="GitHub access" actions={<Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>Add a repository</Button>}>
        Private repositories need a deploy key: a read-only key for one repository, created here and added on GitHub. Public repositories need nothing.
      </PageHead>
      <div className="card rise">
        {isPending ? <div className="card-body"><Skeleton height={120} /></div> : !keysList?.length ? (
          <Empty icon={<FolderGit2 />} title="No deploy keys">Add one for every private repository the instances use.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Repository</th><th>Key</th><th>Created</th><th /></tr></thead>
              <tbody>
                {keysList.map((k) => (
                  <tr key={k.repo}>
                    <td style={{ fontWeight: 600 }}>{k.spelling || k.repo}</td>
                    <td style={{ maxWidth: 320 }}><CopyField value={k.public_key} /></td>
                    <td className="small muted">{ago(k.created)}</td>
                    <td className="actions">
                      <a className="btn sm ghost" href={k.github_url} target="_blank" rel="noreferrer">GitHub <ArrowUpRight /></a>
                      <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label="Delete" onClick={() => confirm(`Delete the key of ${k.repo}? Remove it on GitHub too.`) && remove.mutate(k.repo, { onSuccess: () => toast("success", "Key deleted") })} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {adding && (
        <Modal title="Add a private repository" onClose={() => setAdding(false)}
          footer={<><Button onClick={() => setAdding(false)}>Cancel</Button><Button variant="primary" icon={<KeyRound />} loading={create.isPending} disabled={!repo.trim()}
            onClick={() => create.mutate(undefined, { onSuccess: (m) => { setAdding(false); setRepo(""); setMade(m); }, onError: (e) => toast("error", "No key created", errorText(e)) })}>Create key</Button></>}>
          <Field label="Repository" hint="https://github.com/owner/repository, or owner/repository">
            <input className="input" autoFocus value={repo} onChange={(e) => setRepo(e.target.value)} />
          </Field>
        </Modal>
      )}
      {made && (
        <Modal title="Add this key on GitHub" description={`In ${made.repo}: Settings, Deploy keys, Add deploy key. Leave "Allow write access" off.`} onClose={() => setMade(null)}
          footer={<><Button onClick={() => setMade(null)}>Done</Button><a className="btn primary" href={made.github_url} target="_blank" rel="noreferrer">Open GitHub <ArrowUpRight /></a></>}>
          <div className="stack">
            <CopyField value={made.public_key} block />
            <Callout>Only the public part leaves the server; the private key is stored encrypted.</Callout>
          </div>
        </Modal>
      )}
    </Page>
  );
}

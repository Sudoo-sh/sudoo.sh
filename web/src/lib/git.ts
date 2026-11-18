// Links to a repository's pages from the address git uses for it (https or ssh, GitHub, GitLab, Bitbucket, Gitea...).

/** The repository's web address: git@github.com:owner/repo.git and https://github.com/owner/repo.git both give
 *  https://github.com/owner/repo. null when the address is not one a browser can open (a local path, a file:// URL). */
export function repositoryPage(address: string | null | undefined): string | null {
  if (!address) return null;
  const text = address.trim().replace(/\.git\/?$/, "").replace(/\/+$/, "");
  const ssh = /^(?:ssh:\/\/)?(?:[\w.-]+@)?([\w.-]+\.[a-z]{2,})(?::\d+)?[:/](.+)$/i.exec(text);
  if (/^https?:\/\//i.test(text)) {
    try {
      const url = new URL(text);
      url.username = "";
      url.password = ""; // never show a token that was part of the address
      return `https://${url.host}${url.pathname}`;
    } catch {
      return null;
    }
  }
  return ssh ? `https://${ssh[1]}/${ssh[2]}` : null;
}

/** The page of one commit, on the hosts whose address for it is known. */
export function commitPage(address: string | null | undefined, sha: string): string | null {
  const page = repositoryPage(address);
  if (!page || !/^[0-9a-f]{4,40}$/i.test(sha)) return null;
  const host = new URL(page).host;
  if (host.includes("gitlab")) return `${page}/-/commit/${sha}`;
  if (host.includes("bitbucket")) return `${page}/commits/${sha}`;
  return `${page}/commit/${sha}`; // GitHub, Gitea, Forgejo, Codeberg
}

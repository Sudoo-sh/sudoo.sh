# Workflow: Domains and HTTPS

[العربية](05-domains-and-https.ar.md) · [All workflows](README.md)

**Goal:** make an instance answer on your own domain, such as `erp.client.com`, over https with a valid certificate.

**Who can do it:** everyone sees the **Domains** tab. Administrators add and remove domains.
**Before you start:** adding a domain, its DNS checks and the first certificate come from the premium "domains" package, installed when your plan includes it (the public panel source has only the stubs and the parts described below). Your administrator must have turned custom domains on for the server (the operator's CUSTOM_DOMAINS setting). Until then the tab says so and why. A server reached only through the default public links cannot carry your own domain without a proxy in front of it.

> **Adding a domain to a production instance can change its address.** The button says **Add to production** and asks you to confirm. Removing a domain stops visitors from reaching the instance by that name and deletes its certificate.

## Steps
1. On the instance's page open **Domains** and click **Add domain**.
2. Type only the name, for example `erp.client.com` (no `https://`, no path).
3. The panel shows the DNS record to create at your DNS provider: a **CNAME** for a name like `erp.client.com`, or **A** (and **AAAA**) records for a bare domain such as `client.com`. Copy each value with its copy button.
4. Create the record. Behind Cloudflare set it to **DNS only** (grey cloud).
5. Wait. The panel checks DNS by itself, every minute at first and then less often. DNS changes usually show within minutes, sometimes hours. **Check now** checks at once.
6. When the state is **Active**, open `https://<your domain>`.

## The states
| State | Meaning | What to do |
| :--- | :--- | :--- |
| Waiting for DNS | The domain does not point at this server yet. The reason says what was found. | Create or fix the record shown. |
| Getting a certificate | DNS is right and the certificate is being issued. | Wait. |
| Active | Answers over https with a valid certificate. Visits over http go to https. | Nothing. |
| Renewing | The certificate is being renewed. The current one keeps working. | Nothing. |
| Expires soon | The certificate ends within the operator's ALERT_CERTIFICATE_DAYS and has not been renewed yet. The reason says why. Administrators get an alert. | Fix the cause. |
| Failed | The certificate could not be issued (often port 80 closed, or the record points elsewhere). The panel retries later, waiting longer each time. | Fix the cause and click **Check now**. |

The renewal lead time, the longest wait between retries and the number of domains per instance are operator settings (CERTIFICATE_RENEW_DAYS, CERTIFICATE_RETRY_MAX_HOURS, DOMAINS_PER_INSTANCE). Their shipped defaults are 30 days, 24 hours and 5, but read your panel's Settings rather than assume them.

## Primary domain
The first domain you add is the **primary** one. Once active it is the instance's address: **Open Odoo**, the address in the header and the links in Odoo's e-mails and portal use it. The instance's own default address keeps working. To change it open **...** on another domain and choose **Make primary**. **Remove** in the same menu deletes the domain and its certificate. If you remove the primary domain, another domain of the instance becomes primary automatically (the first one in its list), or the default address is used again. Visitors can no longer reach the instance at the removed name.

## If the instance moves to another server
Its custom domains go with it. Their DNS still points at the old server, so each shows "moved here from <server>" with the record to create now. Change it at your DNS provider.

## Common problems
- The tab says custom domains are off: ask your administrator.
- Stuck on Waiting for DNS: compare the record with the one shown. Wait for DNS to spread.
- Failed: check that port 80 is reachable and the record points at this server.

## What was checked in source
Checked in the public panel source at 3.5.0 (instances/custom_domains.py, domain_worker.py, i18n/domains.ts): the six states and their meaning, that renewal is done by the panel itself for domains that exist (starting CERTIFICATE_RENEW_DAYS before expiry, or sooner for short-lived certificates), that **Expires soon** uses ALERT_CERTIFICATE_DAYS, that removing a domain deletes its certificate and key and makes another domain primary, and the move-with-instance behaviour. **Not checked** (the premium package is not available): adding a domain, the DNS record shown, the DNS check timing, **Check now**, **Make primary** and the exact button names. This page keeps those steps as written in the User Manual only.


> No screenshots yet: the add-a-domain screens are in a premium package that was not available.

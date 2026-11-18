# Sudoo.sh Licence Agreement

Version 1.0, effective 2026-11-18.
Current version: <https://sudoo.sh/legal/license>. This version, permanently: <https://sudoo.sh/legal/license/v1.0>.

Required Notice: Copyright (c) 2026 Yousef M. Y. Al Sabbah, trading as Sudoo.sh.

---

> **Summary in plain words (not part of the agreement, not binding)**
>
> - This agreement is the licence for the Sudoo.sh panel, its source code and its connected services. You accept it
>   when you create an account, set up a panel or run the installer.
> - When you sign up you tell us how you will use Sudoo.sh: **personal** (no business at all), **for your own
>   company**, or **as a service provider** running Odoo for other companies. If you choose a company option, you sign
>   up **in your company's name**: the company is the licensee, and you confirm you may bind it.
> - **One account per company.** A company may hold only one Sudoo.sh account. Its employees and contractors join it
>   as team members. A second account for the same company is a breach.
> - Every use type follows the same plans, counted by **production branches**: Free 1, Pro 5, Business 25, and more
>   one at a time. Development and staging are unlimited. Personal use is free, on the Free plan only. Charities,
>   schools and public bodies sign up as companies.
> - **If you run, host, manage or maintain Odoo for someone else, it counts on your plan**, whatever name, account,
>   server or card is used. Splitting work across accounts, opening accounts in your clients' names or hiding
>   production to avoid paying is a breach: every account involved can be closed and you must pay for all unlicensed
>   use at list price.
> - What you tell us at sign-up must be true and kept up to date. We may check it, and we may check the usage figures
>   your panel reports.
> - You pay in advance (monthly, 12 or 24 months). Upgrades apply at once with a credit for unused time; downgrades
>   apply at the end of the period; no partial refunds.
> - The source code is public so you can review it, and you may use it only as this agreement allows.
> - Your Odoo databases and files stay on your servers. If your account is suspended, your live instances keep
>   serving. The software comes as it is, our liability is limited, and the Apache-2.0 plugin SDK, the MIT-0 plugin
>   template and the OPL-1 Sudoo.sh Odoo apps are **not** covered by this agreement.
>
> Only the full text below is binding.

---

## Contents

1. Parties
2. Definitions
3. Acceptance, authority and versions
4. Declared use and the licence granted
5. Plans and entitlements
6. Anti-circumvention
7. True information, verification and audit
8. Fees, billing periods and changes of plan
9. Restrictions
10. Excluded components, open-source software and contributions
11. Trademarks
12. Data, privacy and confidentiality
13. Your security responsibilities
14. Suspension
15. Term and termination
16. Warranty disclaimer
17. Limitation of liability
18. Indemnity
19. Export control and sanctions
20. Governing law and disputes
21. General terms
22. Contact and notices

Annex A. Arabic summary (non-binding)

---

## 1. Parties

1.1 This Sudoo.sh Licence Agreement (the **Agreement**) is between:

- **Yousef M. Y. Al Sabbah, trading as Sudoo.sh**, of Palestine (the **Licensor**, **we**, **us**). Our postal
  address is provided on request through support@sudoo.sh; and
- the individual or legal entity that accepts it (the **Customer**, **you**). If you accept on behalf of a company or
  other organisation, "you" means that organisation, and section 3.3 applies.

1.2 We may transfer this Agreement to a company formed to carry on the Sudoo.sh business, as described in
section 21.4.

## 2. Definitions

In this Agreement, the following words have these meanings.

2.1 **Software** means the Sudoo.sh panel and every other program the Licensor makes available for use with it,
including the gateway, server agent, installer, command-line tool, browser extension, official plugins and their
updates, documentation and licence components, in source or binary form, from any source, including the Licensor's
public source repositories. It does not include the Excluded Components (section 10).

2.2 **Panel** means the part of the Software that a Customer installs on its own server to create and run Odoo
instances.

2.3 **Hub** means the services the Licensor operates for the Software, including hub.sudoo.sh, the sign-in and
account sites, the marketplace, the licence and lease service and the billing service.

2.4 **Installation** means one copy of the Panel (or of any other server component of the Software) installed on
one server, identified by its installation key and the server fingerprint the Software computes.

2.5 **Server** means one physical or virtual machine, or one container host, on which an Installation or any part of
it runs.

2.6 **Account** means an account on the Hub, with its users, Installations, subscriptions and declared information.

2.7 **Plan** means the subscription level of an Account (for example Free, Pro or Business) and any add-ons, as
described on the Licensor's pricing page at <https://sudoo.sh/pricing> at the time of purchase or renewal (the
**Pricing Page**).

2.8 **Entitlements** means the limits and features of a Plan: the number of Production Branches, Servers, projects,
API requests and team members, and the features enabled.

2.9 **Production Branch** means any Odoo instance or database that is used for real business operations, real
users or real data, or that is reachable by end users as a live system, whether or not it is labelled
"production" in the Software. A branch the Software marks as Production is always a Production Branch. An instance
labelled development, staging, demo or test becomes a Production Branch as soon as it is used in this way.

2.10 **Non-Production Use** means development, testing, staging, training and demonstration instances that are not
Production Branches.

2.11 **Personal Use** means use by an individual for personal study, research, experiments, hobby projects or
private purposes, with no commercial purpose and not for or on behalf of any business, employer, Client or other
organisation.

2.12 **Internal Use** means use by a company or other organisation solely for the internal operations of itself and
its Affiliates, where every Odoo instance serves the Customer or its Affiliates and no Third Party.

2.13 **Service Provider** means a Customer that installs, operates, hosts, manages, maintains, administers,
develops on or supports Odoo for one or more Clients, whether paid or not.

2.14 **Client** means any person or organisation, other than the Customer and its Affiliates, for whom the Customer
does any of the things listed in section 2.13.

2.15 **Third Party** means any person or organisation other than the Customer and its Affiliates. Clients are Third
Parties.

2.16 **Affiliate** means an entity that Controls, is Controlled by, or is under common Control with the Customer,
for as long as that Control lasts.

2.17 **Control** means direct or indirect ownership of more than 50% of the voting interests of an entity, or the
power to direct its management and policies by vote, contract or otherwise.

2.18 **Declared Use** means the use type (Personal Use, Internal Use or Service Provider) and the information the
Customer gives at sign-up and later updates under section 7.

2.19 **Usage Data** means the technical figures the Software reports to the Hub, as listed in section 7.6.

2.20 **Excluded Components** has the meaning in section 10.1.

2.21 **List Price** means the undiscounted price for the relevant Plan, add-on and billing period on the Pricing Page
at the time the use took place, without launch offers, promotions or volume discounts.

2.22 **Company Identifiers** means an organisation's verified domain names, company registration numbers and tax or
VAT identification numbers.

2.23 **Required Notice** means the copyright notice at the top of this Agreement and any copyright or licence notice
included in the Software.

## 3. Acceptance, authority and versions

3.1 **How you accept.** You accept this Agreement by ticking the acceptance box (or giving the equivalent
confirmation) when you create an Account, when you complete the Panel's first-run setup, or when you confirm the
installer's licence prompt, or by installing or using the Software in any other way. If you do not accept it, do
not install or use the Software.

3.2 **Acceptance at each step.** Each acceptance step applies the same Agreement. Accepting at one step does not
release you from accepting again where the Software asks you to.

3.3 **Signing up for a company.** A person who chooses Internal Use or Service Provider at sign-up creates the
Account and accepts this Agreement in the name of the company or other organisation they name, not in their own
name. That organisation is the Customer and the licensee, and the person confirms that they are authorised to bind
it. If they are not authorised, the organisation gets no licence, and that person is personally bound by this
Agreement and personally liable for compliance with it and for all fees due for the use made. An employee or
contractor who installs the Software for an organisation accepts on that organisation's behalf.

3.4 **Age and capacity.** You must be at least 18 years old (or the age of legal majority where you live) and able to
enter into a binding contract.

3.5 **Versions.** Each version of this Agreement has a version number and an effective date. The current version is
published at <https://sudoo.sh/legal/license>, and each version stays available at its own permanent address (this
version at <https://sudoo.sh/legal/license/v1.0>). The version you accepted applies until a new version applies to
you under section 3.6.

3.6 **Changes.** We may change this Agreement. We will give at least 30 days' notice of a change by e-mail to the
Account's address and by a notice in the Hub and the Panel, with a summary of what changed.

- (a) A change that does not reduce your rights (for example a correction or clarification) applies at the end of
  the notice period.
- (b) A change that reduces your rights or increases your obligations applies to you when you accept it, or, if
  you do not accept it, at the start of your next billing period after the notice period. Until then the earlier
  version continues to apply.
- (c) If you do not accept a change under (b), you may cancel your Plan before it applies to you, and it ends at the
  end of the current billing period.
- (d) We may require you to accept the new version before you sign in, add Installations or change your Plan.
- (e) A change required by law or to stop a security risk may apply sooner, with as much notice as is reasonable.

3.7 **Records.** We keep a record of each acceptance: the Agreement version and a digest of its text, the date and
time (UTC), the IP address, the browser or client used, the user and Account, the Installation (for Panel and
installer acceptance), the step where it was given and the Declared Use at that time. These records are evidence of
acceptance. You may ask for a copy of your records.

## 4. Declared use and the licence granted

4.1 **Declared Use.** At sign-up you must declare one use type: Personal Use, Internal Use or Service Provider. Your
licence covers only the use type you declared and that we have accepted, within the Entitlements of your Plan.

4.2 **Personal Use licence.** If you declared Personal Use, we grant you a personal, non-exclusive, non-transferable,
non-sublicensable, worldwide licence to install and use the Software for Personal Use only, on the Free Plan only.
A Personal Use Account cannot buy a paid Plan or add-ons. Personal Use excludes any use for a business, employer,
Client or other commercial purpose, including evaluation for a business.

4.3 **Organisations that are not businesses.** Charities, schools, universities, public bodies and other non-profit
organisations are not Personal Use. They sign up as Internal Use or Service Provider like any other organisation,
under section 7, and follow the same Plans.

4.4 **Internal Use licence.** If you declared Internal Use, we grant you a non-exclusive, non-transferable,
non-sublicensable, worldwide licence to install and use the Software for the internal operations of you and your
Affiliates, within the Entitlements of your Plan. Your employees and individual contractors may use it on your
behalf. You are responsible for your Affiliates' and personnel's compliance. Internal Use does not include running
Odoo for any Third Party.

4.5 **Service Provider licence.** If you declared Service Provider, we grant you a non-exclusive, non-transferable,
non-sublicensable, worldwide licence to install and use the Software, within the Entitlements of your Plan, to run
Odoo for your own internal operations and for your Clients, and to give your Clients access to the Odoo instances
you run for them. Your Clients get no licence to the Software from you; they may use the Odoo instances you run for
them, and they may not use the Software themselves unless they hold their own licence.

4.6 **Change of use.** If your actual use changes (for example a company starts serving Clients), you must update
your Declared Use and Plan before the new use starts. Use outside your Declared Use is unlicensed use.

4.7 **Evaluation.** An organisation may evaluate the Software for Non-Production Use under an Internal Use or Service
Provider Account on the Free Plan. It may not evaluate under a Personal Use Account.

4.8 **Source code.** This Agreement is the licence for the Software's source code, including the source code we
publish in our public repositories. We make that source available so that you can review it, and you may use it only
for the uses this Agreement licenses to you. You may read and change it only to use the Software as this Agreement
allows, within section 9. You may not distribute, publish or offer the Software or changed versions of it to others,
except that you may share changes with us under section 10.4 (for example by opening a pull request in our
repository). Publishing the source does not grant any licence to use it outside this Agreement.

4.9 **Earlier public licence.** Before this version, some of our source code was published under the PolyForm
Noncommercial License 1.0.0. From the effective date of this version, our public repositories are licensed only under
this Agreement. This does not withdraw rights that the earlier licence already gave you in a copy you obtained under
it, to the extent the law does not allow them to be withdrawn; those rights extend only to that copy and to the uses
that licence allows, and never to the Hub or to later versions of the Software.

4.10 **Reservation.** All rights not expressly granted are reserved. The Software is licensed, not sold.

## 5. Plans and entitlements

5.1 **One ladder for every use type.** The same Plans apply to Internal Use and Service Provider Accounts, and the
Free Plan to Personal Use Accounts. Plans are measured by the number of Production Branches. At the effective date
of this version they are:

- (a) **Free:** 1 Production Branch;
- (b) **Pro:** 5 Production Branches;
- (c) **Business:** 25 Production Branches; and
- (d) extra Production Branches, bought one at a time as add-ons to a paid Plan.

Non-Production Use is unlimited on every Plan (section 5.4). The Pricing Page states the current Plans, prices and
other Entitlements, and changes to them apply under section 8.9.

5.2 **Entitlements.** Your Plan's Entitlements are those on the Pricing Page for your Plan when you subscribed or last
renewed, plus any add-ons you bought. They apply across your whole Account and all its Installations together.

5.3 **Production Branches.** Each Production Branch needs a valid lease issued by the Hub to its Installation. Every
Production Branch counts toward your Plan, wherever it runs and whatever it is called (section 2.9).

5.4 **Non-Production Use.** Development, staging, demo and test instances are not limited by number, subject to
the fair-use limits on the Pricing Page and the capacity of your own Servers. Using them as Production Branches to
avoid the limits breaches section 6.

5.5 **Servers.** The number of Servers on which Installations of one Account may run is limited by the Plan. One
Installation is bound to one Server.

5.6 **Technical enforcement.** The Software checks Entitlements with the Hub through signed licences and leases,
heartbeats and integrity checks. A Production Branch may keep running for the offline grace period stated in the
documentation when the Hub cannot be reached. These checks are part of the licence and section 9.1(e) applies to
them.

5.7 **Exceeding limits.** If you use more than your Entitlements, you must upgrade your Plan or buy add-ons at once.
We may charge the excess from when it began at List Price.

5.8 **One Account per organisation.** Each legal entity may hold only one Account at any time.

- (a) Two Accounts belong to the same legal entity if any of their Company Identifiers match.
- (b) The entity's employees and contractors use its Account as team members invited to it, and never open
  separate Accounts for its use.
- (c) Each Affiliate is a separate legal entity and holds its own Account if it signs up; Affiliates may ask us to
  link their Accounts. Odoo instances run for an Affiliate under your Account count on your Plan.
- (d) Opening or keeping a second Account for the same entity, under any name, domain or person, is a breach of
  section 6 (section 6.3(j)). We may merge, suspend or close duplicate Accounts.

## 6. Anti-circumvention

6.1 **Attribution rule.** Every Installation, Server and Production Branch that is installed, operated, hosted,
managed, maintained, administered, monitored or supported, in whole or in part, by or on behalf of you or your
Affiliates for a Third Party:

- (a) counts toward **your** Plan and Entitlements; and
- (b) must be licensed under **your** Service Provider Account,

regardless of whose name, Account, e-mail address, domain, company registration, Server, cloud account, GitHub
organisation or payment method is used, and regardless of who owns the Server or the data.

6.2 **Client's own licence.** Section 6.1 does not apply to an Installation that a Client licenses under its own
Account for its own Internal Use, pays for itself, and operates itself, where you only give occasional
consultancy or development services and do not install, operate, host, manage, maintain or administer that
Installation. If you do any of those, section 6.1 applies.

6.3 **Prohibited practices.** You must not, directly or through others:

- (a) split usage across several Accounts, Installations or Plans to stay within lower limits or avoid fees;
- (b) open or use Accounts in the names of Clients, employees, relatives, Affiliates or other nominees for
  Installations you install, operate, manage, maintain or administer;
- (c) run a separate Installation, Account or Free Plan for each Client while managing them yourself;
- (d) share, lend, rent, sell or resell an Account, Installation, licence key, lease or Plan, or let others use them
  outside your licence;
- (e) declare a use type, a number of Clients or other information that is false or no longer true;
- (f) label a Production Branch as development, staging or demo, or hide or disguise production use;
- (g) tamper with, bypass, disable, emulate, replay or falsify licence checks, leases, heartbeats, fingerprints,
  integrity checks, Usage Data or entitlement counting, or run a fake or modified Hub;
- (h) copy an Installation's identity to another Server, or reset or clone it to obtain new free Entitlements;
- (i) help, encourage or allow anyone else to do any of these; or
- (j) open or keep more than one Account for the same legal entity (section 5.8).

6.4 **Consequences.** A breach of this section 6 is a material breach that cannot be cured. In addition to any other
right or remedy we have:

- (a) we may suspend or terminate, with immediate effect, every licence, Account and Installation involved,
  including those of your Affiliates and of any nominee or duplicate Account used in the breach, or merge duplicate
  Accounts into one;
- (b) you must pay fees for all unlicensed use at List Price, for the correct Plan and add-ons, from the date the
  unlicensed use started (or, if that date cannot be shown, from the date the earliest Account or Installation
  involved was created) until it stops or is properly licensed;
- (c) you must pay our reasonable costs of investigating and enforcing, including audit and legal costs; and
- (d) we may refuse to open new Accounts for you, your Affiliates and the people who acted for you in the breach.

6.5 **Cooperation.** If we reasonably believe that section 6 may have been breached, you must, within 10 business
days of our written request, tell us in writing which Installations, Servers, Accounts and Clients are involved,
and give the records reasonably needed to check this. Failing to cooperate is itself a breach of this Agreement.

6.6 **Signals.** We may use signals such as shared payment methods, Server fingerprints, GitHub organisations, IP
address ranges, Company Identifiers and Usage Data to detect possible breaches. A signal alone does not establish a
breach; we will give you the chance to explain before we terminate under section 6.4, unless that would let the
breach continue or create a legal or security risk.

## 7. True information, verification and audit

7.1 **True information.** All information you give at sign-up and later (identity, company details, registration
number, tax or VAT number, website, role, number of Clients, Declared Use) must be true, complete and current. You
must update it within 30 days of any change, and before any change of Declared Use under section 4.6.

7.2 **Consent to verification.** You agree that we may verify that information, including by:

- (a) confirming your e-mail address and checking its domain;
- (b) asking you to place a DNS record or file on your company domain;
- (c) checking tax and VAT numbers and company registrations against public or official registers;
- (d) asking for documents such as a certificate of incorporation or a tax registration; and
- (e) asking your staff contact to confirm your identity and role.

7.3 **Approval.** We may require verification to be completed before an Internal Use or Service Provider Account may
run Production Branches, and we may refuse an Account that cannot be verified or whose Company Identifiers match
another Account (section 5.8).

7.4 **Re-verification.** We may ask you to re-verify at reasonable intervals (normally no more than once every 12
months) and whenever we have a reasonable concern that the information is no longer accurate.

7.5 **False information.** Giving false or misleading information, or not keeping it current, is a material breach.
If it led to lower fees, section 6.4(b) applies to the difference.

7.6 **Usage Data.** The Software reports to the Hub the following Usage Data, and you agree that we may use it to
operate the Hub, enforce Entitlements and check compliance:

- (a) the installation key and a one-way digest of the Server fingerprint;
- (b) the Software version and integrity check results;
- (c) the number of projects, Production Branches, other instances and Servers, and of running instances;
- (d) installed plugins and their licence standing;
- (e) coarse resource figures (CPU, memory and disk percentages) and heartbeat times; and
- (f) API request counts against quotas.

Usage Data does not include the contents of your Odoo databases, files, source code or your Clients' data. Our
privacy policy at <https://sudoo.sh/legal/privacy> explains how it is processed.

7.7 **Records and self-certification.** On our written request (no more than once in any 12 months, unless we have a
reasonable concern of non-compliance), you must within 10 business days give a written certificate, signed by an
authorised person, listing your Installations, Servers, Production Branches and Clients served, and confirming
compliance.

7.8 **Audit.** With at least 15 business days' written notice (no more than once in any 12 months, unless an earlier
check found non-compliance or we have a reasonable concern), we or an independent auditor bound by
confidentiality may check your use of the Software against this Agreement, during normal business hours and in a
way that does not unreasonably disrupt your business. Audits will be done remotely where possible, using Usage
Data, records and screen-sharing. We bear the cost of the audit unless it shows that you used more than 5% beyond
your Entitlements or underpaid by more than 5%, in which case you bear its reasonable cost and pay the difference
under section 6.4(b).

## 8. Fees, billing periods and changes of plan

8.1 **Fees.** You pay the fees for your Plan and add-ons as shown on the Pricing Page or in your order. Prices,
Entitlements and offers are set by the Licensor and may differ by region and currency.

8.2 **Billing periods.** Plans are billed in advance for the billing period you choose: monthly, 12 months or 24
months (or other periods we offer). Subscriptions renew automatically for the same period unless you cancel before
the renewal date.

8.3 **Payment.** Payment is taken through our payment provider by card or another method we accept. You authorise
us and the provider to charge the method on file for each renewal, upgrade and add-on.

8.4 **Upgrades.** An upgrade (a higher Plan, more add-ons or a longer billing period) takes effect at once. The
unused part of your current period is credited against the price of the new one, and the new period starts on the
day of the upgrade.

8.5 **Downgrades and cancellation.** A downgrade or cancellation takes effect at the end of the current billing
period. You must bring your use within the lower Entitlements by then; Production Branches above them will not
receive new leases.

8.6 **No partial refunds.** Fees are not refundable, in whole or in part, for unused time, unused Entitlements or
early cancellation, except as stated in section 8.4, where we terminate without cause, or where the law requires a
refund.

8.7 **Taxes.** Fees exclude taxes. You pay all sales, use, value-added, goods and services and similar taxes, except
taxes on our income. If a tax must be withheld, you pay the extra amount so that we receive the full fee. Business
Customers must give a valid tax number where reverse-charge rules apply.

8.8 **Late payment.** If a payment fails, we will tell you and retry it. If it is still unpaid 14 days after it was
due, we may suspend paid Entitlements under section 14. Unpaid amounts may carry interest at 1% per month or the
highest rate the law allows, whichever is lower, plus reasonable collection costs.

8.9 **Price changes.** We may change prices and Entitlements. A price increase applies to your subscription only from
your next renewal after at least 30 days' notice. A reduction of Entitlements within a paid period does not apply to
you until your next renewal.

8.10 **Launch offers and promotions.** Launch offers, discounts and promotions apply only as described in their own
terms (for example the eligible sign-up window, the discount and how long it lasts). They are not combined unless
their terms say so, they end if the Account is terminated for breach, and they do not reduce List Price for
section 6.4(b).

8.11 **Disputed charges.** Tell us of any billing dispute within 60 days of the charge. Opening a chargeback without
first contacting us may lead to suspension of the licences paid by that charge while it is open.

## 9. Restrictions

9.1 You must not, and must not allow anyone else to:

- (a) use the Software outside your Declared Use, your Plan or this Agreement;
- (b) provide the Panel, or a substantial part of its functions, to Third Parties as a hosted or managed service
  that lets them create or manage their own Odoo instances (a competing "Panel as a service"), or use the Software to
  build a product that competes with it; running Odoo for Clients as a Service Provider is allowed;
- (c) sublicense, sell, rent, lease, lend, assign, publish or distribute the Software, except as section 4.8 allows;
- (d) remove, change or hide licence notices, copyright notices, trademarks or the Required Notice;
- (e) bypass, disable or interfere with technical limits, licence checks, leases, heartbeats or integrity checks
  (section 6.3(g));
- (f) reverse engineer, decompile or disassemble any part of the Software provided only in binary form, except to
  the extent the law allows it despite this restriction;
- (g) use the Software or the Hub to break the law, infringe others' rights, send spam or malware, or attack any
  system;
- (h) probe, scan or load-test the Hub, except under our published security disclosure policy; or
- (i) access the Hub by automated means other than the interfaces we document.

## 10. Excluded components, open-source software and contributions

10.1 **Excluded Components.** This Agreement does not apply to the following, which are governed only by their own
licences:

- (a) the **Sudoo.sh plugin SDK** (`sudoo-plugin-sdk`), licensed under the Apache License 2.0;
- (b) the **Sudoo.sh plugin template** (`sudoo-plugin-template`), licensed under the MIT No Attribution License
  (MIT-0);
- (c) the **Sudoo.sh Odoo apps** (`sudoo-odoo`, for example `sudoo_base`), licensed under the Odoo Proprietary
  License v1.0 (OPL-1);
- (d) **Odoo** itself (Odoo Community under the LGPL-3; Odoo Enterprise under Odoo S.A.'s own terms, which you must
  obtain separately), PostgreSQL, Docker images and other software the Panel downloads or runs; and
- (e) third-party open-source components included in the Software, each under its own licence, listed in the
  Software's notices.

Nothing in this Agreement limits the rights those licences give you, and they do not extend to the rest of the
Software.

10.2 **Third-party plugins.** Plugins published on the marketplace by other developers are licensed by those
developers under their own terms. We are not a party to those terms.

10.3 **Odoo Enterprise.** The Software contains no Odoo Enterprise code. You are solely responsible for having the
rights to any Odoo Enterprise add-ons you use with it.

10.4 **Contributions.** Contributions to the Licensor's repositories are governed by the contributing guide of the
repository (CONTRIBUTING.md). Under it, each contributor licenses the contribution to the Licensor so that the
Licensor may include it in the Software and license it under this Agreement and any other terms. This Agreement does
not change those terms.

10.5 **Feedback.** If you send suggestions or feedback, we may use them freely and without obligation to you.

## 11. Trademarks

11.1 "Sudoo", "Sudoo.sh", the Sudoo.sh logo and the marketplace badges are trademarks of the Licensor. This
Agreement grants no right to use them, except to describe accurately that you use Sudoo.sh, as allowed by the
trademark policy (TRADEMARKS.md, published at <https://github.com/Sudoo-sh/sudoo.sh/blob/main/TRADEMARKS.md>).

11.2 "Odoo" and "Odoo.sh" are trademarks of Odoo S.A. Sudoo.sh is not affiliated with or endorsed by Odoo S.A.

## 12. Data, privacy and confidentiality

12.1 **Your Odoo data.** The Panel runs on your Servers. Your Odoo databases, files, backups, source code and your
Clients' data stay on your Servers (or storage you choose) and are not sent to the Hub. You are the controller of
that data and, for Client data, you and your Client decide how it is processed; we do not process it.

12.2 **Hub data.** We process Account information, Declared Use and verification information, acceptance records,
billing records, support tickets and Usage Data to provide the Hub, enforce this Agreement, prevent fraud and meet
legal obligations, as described in our privacy policy at <https://sudoo.sh/legal/privacy>. Payment card details are
handled by our payment provider, not by us.

12.3 **Support access.** If you give us access to a Server, instance or data to help you, we use it only for that
purpose and treat what we see as your Confidential Information.

12.4 **Confidentiality.** Each party will keep the other's non-public information that is marked confidential or is
clearly confidential (**Confidential Information**) confidential, use it only for this Agreement, and protect it with
reasonable care. This does not apply to information that is public through no fault of the receiving party, was
already known to it, was received lawfully from someone else, or was developed independently. A party may disclose
Confidential Information where the law requires, after giving notice where allowed. Audit and verification
information we receive is our confidential record of your compliance.

## 13. Your security responsibilities

13.1 You are responsible for:

- (a) the Servers, operating systems, networks and cloud accounts on which you run the Software;
- (b) keeping the Software updated, applying security updates promptly, and configuring it securely, including
  changing default Odoo credentials, restricting access and using secure exposure settings;
- (c) the accounts, passwords, second factors, API tokens and keys of your users, and everything done with them;
- (d) taking and testing backups of your data; and
- (e) your legal obligations to your Clients and to the people whose data you process, including data-protection
  agreements with your Clients.

13.2 Tell us without delay at support@sudoo.sh if you become aware of unauthorised use of your Account. Report a
security problem in the Software privately, as described in our security policy (SECURITY.md in our public
repository).

## 14. Suspension

14.1 We may suspend all or part of your Account, Installations or Entitlements, with notice where reasonably
possible:

- (a) if payment is overdue under section 8.8;
- (b) if we reasonably believe you have breached sections 5.8, 6, 7 or 9;
- (c) if your use creates a security risk, harms the Hub or other customers, or exposes us to legal liability; or
- (d) if the law or an authority requires it.

14.2 **Effect.** During suspension your running Odoo instances, including Production Branches, keep serving their
users. What stops is management: the Hub stops issuing new licences and leases, and the Panel may block creating,
changing, updating or deploying instances, installing plugins and using paid features. We will not delete your data
on your Servers.

14.3 We lift a suspension when its cause is resolved. Suspension does not stop fees from accruing for the period.

## 15. Term and termination

15.1 **Term.** This Agreement starts when you first accept it and lasts until terminated. Each paid Plan lasts for
its billing period and renews under section 8.2.

15.2 **By you.** You may end this Agreement at any time by cancelling your Plan and closing your Account. Paid Plans
end at the end of the current billing period (section 8.5).

15.3 **By us for breach.** We may terminate this Agreement and your licences:

- (a) at once, for a breach of sections 5.8, 6 or 7.5, or of section 9.1(b), (e) or (g);
- (b) if you fail to cure any other material breach within 30 days of written notice; or
- (c) if you become insolvent or cease business.

15.4 **By us without cause.** We may terminate a Free Plan with 30 days' notice, and stop offering a paid Plan at the
end of your current billing period, with at least 90 days' notice.

15.5 **Effect.** When this Agreement or a licence ends:

- (a) your right to use the Software under it ends; you must stop using the Software and uninstall the Panel within
  30 days, during which you may export your Odoo data and move your Production Branches elsewhere;
- (b) all fees due up to termination, and any amount due under section 6.4, become payable at once;
- (c) if we terminated without cause under section 15.4, we refund prepaid fees for the unused part of the paid
  period; and
- (d) sections 6.4, 6.5, 7.7, 7.8 (for 24 months after termination), 8 (for amounts due), 10, 12.4, 16, 17, 18,
  20 and 21 continue.

## 16. Warranty disclaimer

16.1 **As far as the law allows, the Software and the Hub are provided "as is" and "as available", without any
warranty or condition of any kind, express or implied, including merchantability, fitness for a particular
purpose, title, non-infringement, uninterrupted or error-free operation, or that data will not be lost.**

16.2 We do not warrant that the Software is suitable for any particular use, including production hosting, until
and only to the extent that our documentation describes it as such.

16.3 Some laws do not allow these exclusions; there, they apply as far as permitted.

## 17. Limitation of liability

17.1 **Exclusion.** As far as the law allows, neither party is liable for indirect, incidental, special,
consequential or punitive damages, or for loss of profits, revenue, business, goodwill or data, however caused,
even if advised of the possibility.

17.2 **Cap.** As far as the law allows, each party's total liability arising out of or relating to this Agreement is
limited to the greater of (a) the fees you paid to us under this Agreement in the 12 months before the event that
gave rise to the claim and (b) US$100.

17.3 **Exceptions.** Sections 17.1 and 17.2 do not limit:

- (a) your obligation to pay fees and amounts due under sections 5.7, 6.4 and 7;
- (b) your liability for breach of sections 4, 5.8, 6, 9 or 11, or for infringing our intellectual property;
- (c) your obligations under section 18; or
- (d) liability that cannot be limited by law, such as for death or personal injury caused by negligence, or for
  fraud.

## 18. Indemnity

18.1 You will defend, indemnify and hold harmless the Licensor and its personnel against all claims, losses,
damages, fines and reasonable costs (including legal fees) arising from:

- (a) claims by your Clients, end users or anyone else relating to the Odoo instances, services or data you provide
  or process using the Software;
- (b) your breach of this Agreement or of the law; or
- (c) your data, code, plugins or Odoo add-ons, including Odoo Enterprise add-ons you use without the right to.

18.2 We will tell you promptly of a claim, let you control its defence (you may not settle it in a way that admits
fault for us or binds us without our consent), and give reasonable help at your cost.

## 19. Export control and sanctions

19.1 You must comply with all export control and sanctions laws that apply to the Software and to you. You must not
use, export or give access to the Software in or to a country, region, person or entity subject to comprehensive
sanctions or listed on an applicable restricted-party list, or for a prohibited end use.

19.2 You confirm that you are not such a person or entity and are not owned or controlled by one. We may suspend or
terminate under sections 14 and 15 if this is or becomes untrue.

## 20. Governing law and disputes

20.1 **Governing law.** This Agreement, and any dispute or claim arising out of or in connection with it or its
subject matter or formation (including non-contractual disputes or claims), is governed by the laws of England and
Wales, without regard to their conflict-of-laws rules. The United Nations Convention on Contracts for the
International Sale of Goods does not apply.

20.2 **First step.** Before starting proceedings, the parties will try in good faith for 30 days to settle a dispute
by written notice and discussion.

20.3 **Arbitration.** Any dispute arising out of or in connection with this Agreement, including any question about
its existence, validity or termination, that is not settled under section 20.2 will be referred to and finally
resolved by arbitration under the LCIA Rules, which are incorporated by reference into this section. The tribunal
will consist of one arbitrator. The seat of the arbitration will be London, England. The language of the arbitration
will be English. Hearings may be held remotely by video conference.

20.4 **Court relief.** Despite section 20.3, either party may at any time ask any competent court for urgent
injunctive or other interim relief, including to protect its intellectual property or Confidential Information, and
we may bring a claim to collect unpaid fees and other amounts due under this Agreement in any competent court.

20.5 **Consumers.** If you use the Software for Personal Use and the mandatory law of the country where you live
gives you rights as a consumer that cannot be excluded by agreement (for example a right to bring proceedings in your
local courts, or rights about refunds and automatic renewal), nothing in this Agreement removes those rights, and
sections 20.1 and 20.3 apply only as far as that law allows.

## 21. General terms

21.1 **Entire agreement.** This Agreement, the Pricing Page, your orders, the privacy policy and the documents it
refers to are the whole agreement on its subject and replace earlier discussions. Your purchase-order terms do not
apply. If a signed written agreement between you and us covers the Software, it prevails where it conflicts.

21.2 **Order of precedence.** In a conflict: a signed written agreement, then this Agreement, then the Pricing Page,
then the documentation.

21.3 **Independent parties.** The parties are independent contractors. Nothing creates a partnership, agency or
employment.

21.4 **Assignment.** You may not assign or transfer this Agreement or any licence without our written consent;
any attempt to do so is void. We may assign or transfer this Agreement to an Affiliate or to a successor of the
business, including a company formed to carry on the Sudoo.sh business, with notice to you.

21.5 **Severability.** If any part of this Agreement is unenforceable, it is limited to the minimum extent needed and
the rest stays in force.

21.6 **No waiver.** Not enforcing a right is not a waiver of it.

21.7 **Force majeure.** Neither party is liable for delay or failure caused by events beyond its reasonable control,
other than payment obligations.

21.8 **Language.** This Agreement is written in English. Translations and summaries, including Annex A, are for
convenience only; the English text prevails.

21.9 **Electronic acceptance.** The parties agree that acceptance by clicking, ticking or typing a confirmation is a
valid signature, and that electronic records kept under section 3.7 are admissible as evidence.

## 22. Contact and notices

22.1 Notices to us: by e-mail to support@sudoo.sh. Our postal address is provided on request through the same
address. Notices to you: the e-mail address of your Account's owner, and notices in the Hub or the Panel. E-mail
notices are received the next business day after sending.

22.2 Questions about this Agreement or a separate signed agreement: support@sudoo.sh.

---

## Annex A. ملخص باللغة العربية (غير ملزم)

> هذا الملخص للتوضيح فقط وليس جزءاً من الاتفاقية ولا يُلزم أحداً. **النص الإنجليزي هو المعتمد ويسود عند أي اختلاف.**

**ما هذه الاتفاقية؟** هي ترخيص استخدام لوحة Sudoo.sh وشيفرتها المصدرية والخدمات المرتبطة بها. تقبلها عند إنشاء
الحساب، أو عند إعداد اللوحة لأول مرة، أو عند تشغيل برنامج التثبيت. المرخِّص هو يوسف م. ي. الصباح، ويعمل باسم Sudoo.sh
(فلسطين)، وعنوان المراسلة: support@sudoo.sh.

**نوع الاستخدام المُعلَن.** عند التسجيل تختار نوعاً واحداً:
- **استخدام شخصي:** لأغراض غير تجارية فقط، على الخطة المجانية فقط، ولا يجوز لأي عمل أو جهة عمل أو عميل.
- **استخدام داخلي لشركة:** لتشغيل أعمال الشركة والشركات التابعة لها فقط، دون خدمة أي طرف آخر.
- **مزوّد خدمة:** تشغيل أو استضافة أو إدارة أو صيانة Odoo لشركات أخرى (العملاء).

الجمعيات الخيرية والمدارس والجهات الحكومية تسجّل كشركات وتخضع للتحقق نفسه.

**التسجيل باسم الشركة.** من يختار "شركتي" أو "مزوّد خدمة" يسجّل ويقبل الاتفاقية **باسم شركته**، ويؤكد أنه مخوّل
بإلزامها، والشركة (لا الشخص) هي المرخَّص لها. إن لم يكن مخوّلاً فهو مسؤول شخصياً عن الالتزام بالاتفاقية وعن الرسوم.

**حساب واحد لكل شركة.** لكل كيان قانوني حساب واحد فقط في أي وقت. تُعدّ الحسابات للشركة نفسها إذا تطابق أيٌّ من:
النطاق الموثَّق، أو رقم السجل التجاري، أو الرقم الضريبي. يستخدم الموظفون والمتعاقدون حساب الشركة كأعضاء فريق مدعوّين،
ولا يفتحون حسابات منفصلة. لكل شركة تابعة (كيان قانوني مستقل) حسابها الخاص، ويمكنها طلب ربط الحسابات. فتح حساب ثانٍ
للشركة نفسها أو الاحتفاظ به، بأي اسم أو نطاق أو شخص، مخالفة تُعامل كتحايل، ويجوز لـ Sudoo.sh دمج الحسابات المكررة أو
إيقافها أو إغلاقها.

**الخطط والحدود.** سلّم واحد لكل أنواع الاستخدام، يُحسب بعدد **فروع الإنتاج**: المجانية 1، Pro خمسة، Business
خمسة وعشرون، وفروع إضافية واحداً واحداً. فرع الإنتاج هو أي نسخة Odoo تُستخدم فعلياً لعمل حقيقي أو بيانات حقيقية،
مهما كان اسمها. التطوير والتجريب (staging) بلا حدود عددية. الأسعار الحالية في صفحة الأسعار.

**منع التحايل (بند صارم).** أي تثبيت تقوم بتثبيته أو تشغيله أو إدارته أو صيانته لطرف آخر يُحتسب على خطتك ويجب
ترخيصه تحت حسابك كمزوّد خدمة، **مهما كان الاسم أو الحساب أو النطاق أو الخادم أو وسيلة الدفع المستخدمة**. يُمنع:
تقسيم الاستخدام على عدة حسابات، فتح حسابات بأسماء العملاء أو أشخاص صوريين، إنشاء تثبيت منفصل لكل عميل تديره
أنت، مشاركة الحسابات أو إعادة بيعها، التصريح الكاذب بنوع الاستخدام، أو العبث بفحوص الترخيص والعدّ. **العاقبة:** إيقاف
وإنهاء جميع التراخيص والحسابات المعنية (بما فيها الشركات التابعة والحسابات الصورية والمكررة)، ودفع رسوم كل الاستخدام
غير المرخّص بسعر القائمة منذ بدايته، مع التكاليف المعقولة. ويجب عليك التعاون والإفصاح عند الطلب خلال 10 أيام عمل.

**صحة المعلومات والتحقق.** يجب أن تكون بياناتك صحيحة ومحدّثة. توافق على التحقق منها (إثبات ملكية النطاق، رقم
الضريبة والسجل التجاري، المستندات)، وعلى إعادة التحقق دورياً، وعلى تدقيق الاستخدام بإشعار مسبق قدره 15 يوم عمل، مرة
واحدة في السنة كحد أقصى في الأحوال العادية. ترسل اللوحة أرقاماً تقنية فقط (عدد الفروع والخوادم والإصدار وغيرها)، ولا
ترسل بيانات Odoo الخاصة بك أو بعملائك.

**الشيفرة المصدرية.** الشيفرة منشورة علناً لتتمكن من مراجعتها، ويجوز استخدامها فقط للاستخدامات المرخّصة بهذه
الاتفاقية، ولا يجوز توزيعها أو نشر نسخ معدّلة منها.

**الرسوم.** الدفع مقدماً: شهرياً أو 12 أو 24 شهراً، مع تجديد تلقائي. الترقية فورية مع رصيد للمدة غير المستخدمة،
والتخفيض في نهاية الفترة، ولا استرداد جزئي. الضرائب على العميل. تغيير الأسعار بإشعار 30 يوماً ويسري عند التجديد.
مهلة السداد 14 يوماً بعد الاستحقاق.

**القيود.** لا يجوز تقديم اللوحة نفسها كخدمة منافسة، ولا إزالة الإشعارات، ولا تجاوز القيود التقنية، ولا الترخيص
من الباطن أو التوزيع، مع الالتزام بقوانين التصدير والعقوبات.

**ما لا تشمله الاتفاقية.** حزمة تطوير الإضافات (Apache-2.0)، وقالب الإضافات (MIT-0)، وتطبيقات Sudoo.sh لـ Odoo
(OPL-1)، وOdoo نفسه، والمكوّنات مفتوحة المصدر: كلٌّ منها تحكمه رخصته.

**الإيقاف.** عند إيقاف الحساب تبقى نسخ Odoo العاملة تخدم مستخدميها، ويتوقف فقط الإدارة والتحديثات وإصدار تراخيص
جديدة.

**البيانات والأمان.** بيانات Odoo تبقى على خوادمك وأنت مسؤول عن أمانها ونسخها الاحتياطي. البرنامج مقدَّم "كما هو"
دون ضمان، والمسؤولية محدودة بالأكبر من: ما دفعته خلال آخر 12 شهراً، أو 100 دولار أمريكي. تتحمل أنت مطالبات عملائك
المتعلقة بخدماتك.

**الإنهاء.** يمكنك الإلغاء في أي وقت. ويمكن إيقافك أو إنهاء الاتفاقية عند عدم الدفع أو الإخلال أو الخطر القانوني.
عند الإنهاء تتوقف عن الاستخدام وتزيل اللوحة خلال 30 يوماً، وتُستحق المبالغ غير المدفوعة.

**القانون الحاكم والنزاعات:** قوانين إنجلترا وويلز. تُحل النزاعات بالتحكيم وفق قواعد محكمة لندن للتحكيم الدولي
(LCIA)، بمحكّم واحد، ومقرّه لندن، وباللغة الإنجليزية، ويجوز عقد الجلسات عن بُعد. يجوز لأي طرف اللجوء إلى المحاكم
المختصة لطلب إجراءات عاجلة، ولـ Sudoo.sh تحصيل الرسوم غير المدفوعة أمام أي محكمة مختصة. ويحتفظ المستهلك في
الاستخدام الشخصي بالحقوق التي يمنحها له قانون بلده الإلزامي.

**التعديلات.** يجوز تعديل الاتفاقية بإشعار مسبق 30 يوماً كما في البند 3.

---

Sudoo.sh may update this agreement as described in section 3.

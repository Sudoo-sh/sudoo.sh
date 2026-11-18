// The demo's e-mail templates: the panel's built-in texts and sample values (mail.json, made from
// src/sudoo/mail/catalog.py; tests/test_account_security.py checks they match) and a small copy of the plain format
// of src/sudoo/mail/textformat.py, so the page's preview and checks behave like the real ones.

import type { MailPreview, MailTemplate } from "@/api/types";
import seed from "./mail.json";

type Block = { html: string; text: string };
type Value = string | number | Block;
type Text = { subject: string; title: string; body: string };

interface Seeded {
  key: string;
  name: string;
  description: string;
  recipients: string;
  kicker: string;
  preheader: string;
  default: Text;
  placeholders: MailTemplate["placeholders"];
  required: string[];
  samples: Record<string, Value>;
}

const MESSAGES = seed.messages as unknown as Seeded[];
const changed = new Map<string, Text & { changed_at: string; changed_by: string }>();

const PLACEHOLDER = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;
const ONE_PLACEHOLDER = /^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$/;
const LINK = /\[([^[\]\n]+)\]\((\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}|[^()\s]+)\)/g;
const LITERAL_TARGET = /^(https?:\/\/[^\s<>"']+|mailto:[^\s<>"']+)$/;

const escape = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
const isBlock = (value: unknown): value is Block => typeof value === "object" && value !== null && "html" in value;
const plain = (value: Value | undefined) => (value === undefined ? "" : isBlock(value) ? value.text : String(value));
const empty = (value: Value | undefined) => plain(value).trim() === "";

export const find = (key: string) => MESSAGES.find((m) => m.key === key);

export function resource(message: Seeded): MailTemplate {
  const saved = changed.get(message.key);
  return {
    key: message.key,
    name: message.name,
    description: message.description,
    recipients: message.recipients,
    subject: saved?.subject ?? message.default.subject,
    title: saved?.title ?? message.default.title,
    body: saved?.body ?? message.default.body,
    default: message.default,
    customized: Boolean(saved),
    changed_at: saved?.changed_at ?? null,
    changed_by: saved?.changed_by ?? null,
    placeholders: message.placeholders,
    required: message.required,
  };
}

export const all = () => MESSAGES.map(resource);

export function problems(message: Seeded, text: Text): string[] {
  const found: string[] = [];
  const kinds = new Map(message.placeholders.map((p) => [p.name, p.kind]));
  const used = new Set<string>();
  const unknown = new Set<string>();
  for (const [label, value, limit] of [["subject", text.subject, 200], ["title", text.title, 120], ["text", text.body, 10000]] as const) {
    if (!value.trim()) found.push(`The ${label} is empty.`);
    if (value.length > limit) found.push(`The ${label} is too long (at most ${limit} characters).`);
    const names = [...value.matchAll(PLACEHOLDER)].map((m) => m[1]!);
    names.forEach((n) => (used.add(n), kinds.has(n) || unknown.add(n)));
    const rest = value.replace(PLACEHOLDER, "");
    if (rest.includes("{{") || rest.includes("}}")) found.push(`The ${label} has a placeholder that is not written like {{ name }}.`);
    if (label !== "text") {
      if (value.includes("\n")) found.push(`The ${label} must be one line.`);
      const block = names.find((n) => kinds.get(n) === "block");
      if (block) found.push(`{{ ${block} }} can only be used in the text, alone on its line.`);
    }
  }
  if (unknown.size) found.push(`Unknown placeholder${unknown.size > 1 ? "s" : ""}: ${[...unknown].sort().map((n) => `{{ ${n} }}`).join(", ")}. Use the ones listed.`);
  for (const line of text.body.split("\n")) {
    for (const m of line.matchAll(PLACEHOLDER)) {
      if (kinds.get(m[1]!) === "block" && !ONE_PLACEHOLDER.test(line.trim())) found.push(`{{ ${m[1]} }} must stand alone on its line.`);
    }
    for (const m of line.matchAll(LINK)) {
      const one = m[2]!.match(ONE_PLACEHOLDER);
      if (one) {
        if (kinds.has(one[1]!) && kinds.get(one[1]!) !== "link") found.push(`{{ ${one[1]} }} is not an address: a link needs a link placeholder.`);
      } else if (!LITERAL_TARGET.test(m[2]!)) found.push(`The link to ${m[2]!.slice(0, 60)} is not allowed: use https://, http://, mailto: or a link placeholder.`);
    }
  }
  if (message.required.length && !message.required.some((r) => used.has(r))) {
    found.push(`This e-mail must contain ${message.required.map((r) => `{{ ${r} }}`).join(" or ")}.`);
  }
  return [...new Set(found)];
}

const fill = (text: string, values: Record<string, Value>) => text.replace(PLACEHOLDER, (_, n: string) => plain(values[n])).replace(/\s+/g, " ").trim();

function shape(line: string, values: Record<string, Value>, accent: string): [string, string] {
  const target = (raw: string) => {
    const one = raw.match(ONE_PLACEHOLDER);
    const url = one ? plain(values[one[1]!]) : raw;
    return LITERAL_TARGET.test(url) ? url : "";
  };
  let html = "";
  let text = "";
  let position = 0;
  const inline = (part: string): [string, string] => {
    const h = escape(part).replace(/\*\*(?=\S)(.+?)(?<=\S)\*\*/g, "<strong>$1</strong>");
    return [h.replace(PLACEHOLDER, (_, n: string) => escape(plain(values[n]))), part.replace(/\*\*(?=\S)(.+?)(?<=\S)\*\*/g, "$1").replace(PLACEHOLDER, (_, n: string) => plain(values[n]))];
  };
  for (const m of line.matchAll(LINK)) {
    const [bh, bt] = inline(line.slice(position, m.index));
    const [lh, lt] = inline(m[1]!);
    const url = target(m[2]!);
    html += bh + (url ? `<a href="${escape(url)}" style="color:${accent};">${lh}</a>` : lh);
    text += bt + (url ? `${lt} (${url})` : lt);
    position = m.index! + m[0].length;
  }
  const [rh, rt] = inline(line.slice(position));
  return [html + rh, text + rt];
}

function render(body: string, values: Record<string, Value>, accent: string): [string, string] {
  const html: string[] = [];
  const text: string[] = [];
  let kind = "";
  let lines: [string, string][] = [];
  const close = () => {
    if (kind === "p") html.push(`<p style="margin:0 0 16px 0;">${lines.map((l) => l[0]).join("<br>")}</p>`);
    if (kind === "ul") html.push(`<ul style="margin:0 0 16px 0; padding-left:20px;">${lines.map((l) => `<li style="margin:0 0 4px 0;">${l[0]}</li>`).join("")}</ul>`);
    if (kind === "note") html.push(`<table role="presentation" width="100%" style="margin:0 0 16px 0; font-size:13px; background-color:#FFF8E6; border-left:3px solid #E6A700; border-radius:4px;"><tr><td style="padding:10px 14px; color:#5C4400;">${lines.map((l) => l[0]).join("<br>")}</td></tr></table>`);
    if (kind) text.push(lines.map((l) => (kind === "ul" ? `- ${l[1]}` : l[1])).join("\n"));
    kind = "";
    lines = [];
  };
  const collect = (wanted: string, line: string) => {
    if (kind !== wanted) close();
    kind = wanted;
    lines.push(shape(line, values, accent));
  };
  for (const raw of body.split("\n")) {
    const line = raw.trim();
    if (!line) { close(); continue; }
    const names = [...line.matchAll(PLACEHOLDER)].map((m) => m[1]!);
    if (names.length && names.every((n) => empty(values[n]))) continue;
    const one = line.match(ONE_PLACEHOLDER);
    const value = one ? values[one[1]!] : undefined;
    if (isBlock(value)) { close(); html.push(value.html); text.push(value.text); continue; }
    const button = line.match(new RegExp(`^${LINK.source}$`));
    if (button) {
      close();
      const [, label] = shape(button[1]!, values, accent);
      const labelHtml = shape(button[1]!, values, accent)[0];
      const one = button[2]!.match(ONE_PLACEHOLDER);
      const url = one ? plain(values[one[1]!]) : button[2]!;
      if (LITERAL_TARGET.test(url)) {
        html.push(`<p style="margin:0 0 16px 0; text-align:center;"><a href="${escape(url)}" style="display:inline-block; background-color:${accent}; border:1px solid ${accent}; border-radius:5px; padding:8px 18px; font-size:13px; color:#FFFFFF; text-decoration:none;">${labelHtml}</a></p>`);
        text.push(`${label}: ${url}`);
      } else {
        html.push(`<p style="margin:0 0 16px 0;">${labelHtml}</p>`);
        text.push(label);
      }
      continue;
    }
    if (line.startsWith("- ")) collect("ul", line.slice(2));
    else if (line.startsWith(">")) collect("note", line.slice(1).trim());
    else collect("p", line);
  }
  close();
  return [html.join(""), text.filter((t) => t.trim()).join("\n\n")];
}

export function preview(message: Seeded, text: Text, accent: string): MailPreview {
  const found = problems(message, text);
  if (found.length) return { subject: null, html: null, text: null, problems: found };
  const values = message.samples;
  const [bodyHtml, bodyText] = render(text.body, values, accent);
  const subject = fill(text.subject, values);
  const html = seed.layout
    .replaceAll("#714B67", accent)
    .replace(/@@SUBJECT@@/g, escape(subject))
    .replace("@@KICKER@@", escape(fill(message.kicker, values)))
    .replace("@@TITLE@@", escape(fill(text.title, values)))
    .replace("@@PREHEADER@@", escape(fill(message.preheader, values)))
    .replace("@@BODY@@", () => bodyHtml);
  return { subject, html, text: `${bodyText}\n\n--\nSudoo.sh - Your Odoo instances, on your own server.\n`, problems: [] };
}

export function save(message: Seeded, text: Text, by: string) {
  const same = text.subject === message.default.subject && text.title === message.default.title && text.body === message.default.body;
  if (same) changed.delete(message.key);
  else changed.set(message.key, { ...text, changed_at: new Date().toISOString().replace(/\.\d+Z$/, "Z"), changed_by: by });
}

export const reset = (key: string) => changed.delete(key);

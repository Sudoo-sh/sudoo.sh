// The demo's mail catcher: a few e-mails each staging and development instance "sent" (quotations, invoices, a
// password reset, a portal invitation), as the real catcher keeps them (src/sudoo/mailcatcher/), for the Mail tab.

import type { CaughtMail, CaughtMailDetail } from "@/api/types";
import { hash, MINUTE, HOUR, OPENED } from "./util";

interface Sample {
  subject: (company: string, n: number) => string;
  to: string;
  text: (company: string, n: number) => string;
  attachment?: (n: number) => string;
}

const SAMPLES: Sample[] = [
  {
    subject: (company, n) => `${company} Quotation (Ref S${String(n).padStart(5, "0")})`,
    to: "Deco Addict <deco.addict82@example.com>",
    text: (company, n) => `Hello,\n\nYour quotation S${String(n).padStart(5, "0")} amounting to $ 1,740.00 is ready for review.\n\nDo not hesitate to contact us if you have any questions.\n\n--\n${company}`,
    attachment: (n) => `Quotation - S${String(n).padStart(5, "0")}.pdf`,
  },
  {
    subject: (company, n) => `${company} Invoice (Ref INV/2026/${String(n).padStart(5, "0")})`,
    to: "Azure Interior <azure.Interior24@example.com>",
    text: (company, n) => `Dear Azure Interior,\n\nHere is your invoice INV/2026/${String(n).padStart(5, "0")} amounting to $ 4,610.00 from ${company}. Please remit payment at your earliest convenience.\n\nPlease use the following communication for your payment: INV/2026/${String(n).padStart(5, "0")}.\n\nThank you,\n${company}`,
    attachment: (n) => `INV_2026_${String(n).padStart(5, "0")}.pdf`,
  },
  {
    subject: () => "Password reset",
    to: "Marc Demo <mark.brown23@example.com>",
    text: (company) => `Hello Marc Demo,\n\nA password reset was requested for the account linked to this e-mail.\nYou may change your password by following this link, which will remain valid during 24 hours:\n\n    Change password\n\nIf you do not expect this, you can safely ignore this e-mail.\n\nThanks,\n${company}`,
  },
  {
    subject: (company) => `Your account at ${company}`,
    to: "Gemini Furniture <gemini.furniture39@example.com>",
    text: (company) => `Dear Gemini Furniture,\n\nWelcome to ${company}'s portal!\n\nAn account has been created for you with the following login: gemini.furniture39@example.com\n\nClick on the button below to pick a password and activate your account.\n\n    Activate Account\n\n--\n${company}`,
  },
  {
    subject: (company, n) => `${company} Delivery Order (Ref WH/OUT/${String(n).padStart(5, "0")})`,
    to: "Ready Mat <ready.mat28@example.com>",
    text: (company, n) => `Hello Ready Mat,\n\nWe are glad to inform you that your order has been shipped. Your tracking reference is WH/OUT/${String(n).padStart(5, "0")}.\n\nThank you,\n${company}`,
  },
];

const escape = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function html(subject: string, text: string): string {
  const paragraphs = text.split("\n\n").map((block) => `<p>${escape(block).replace(/\n/g, "<br>")}</p>`).join("");
  return `<!doctype html><html><body><h2>${escape(subject)}</h2>${paragraphs}</body></html>`;
}

const boxes = new Map<string, CaughtMailDetail[]>();

const company = (name: string) =>
  name.split("-").slice(0, 2).map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ").replace(/^Odoo \d+$/, "My Company");

function seed(name: string): CaughtMailDetail[] {
  const n = hash(name);
  const count = 2 + (n % 4);
  const from = `${company(name)} <noreply@${name.split("-")[0]}.example.com>`;
  return Array.from({ length: count }, (_, i) => {
    const sample = SAMPLES[(n + i) % SAMPLES.length]!;
    const ref = 12 + ((n >>> (i + 1)) % 80);
    const subject = sample.subject(company(name), ref);
    const text = sample.text(company(name), ref);
    const received = (OPENED - (i * 3 + 1) * HOUR - (n % 50) * MINUTE) / 1000;
    const recipient = sample.to.match(/<([^>]+)>/)?.[1] ?? sample.to;
    const attachment = sample.attachment?.(ref);
    const id = `${Math.round(received * 1000)}-${(n + i).toString(16).padStart(8, "0").slice(-8)}`;
    const size = 2400 + text.length * 3 + (attachment ? 38_000 : 0);
    return {
      id,
      received_at: received,
      subject,
      from,
      to: [recipient],
      cc: [],
      mail_from: from.match(/<([^>]+)>/)![1]!,
      rcpt_to: [recipient],
      date: new Date(received * 1000).toUTCString().replace("GMT", "+0000"),
      size,
      has_html: true,
      attachments: attachment ? 1 : 0,
      snippet: text.replace(/\s+/g, " ").slice(0, 240),
      headers: [
        ["Message-Id", `<${id}.odoo@${name}>`],
        ["Date", new Date(received * 1000).toUTCString().replace("GMT", "+0000")],
        ["From", from],
        ["Reply-To", from],
        ["To", sample.to],
        ["Subject", subject],
        ["MIME-Version", "1.0"],
        ["Content-Type", 'multipart/mixed; boundary="===============demo=="'],
      ],
      text,
      attachment_list: attachment ? [{ index: 4, filename: attachment, content_type: "application/pdf", size: 38_000 }] : [],
      html: html(subject, text),
    };
  });
}

function box(name: string): CaughtMailDetail[] {
  if (!boxes.has(name)) boxes.set(name, seed(name));
  return boxes.get(name)!;
}

const summary = ({ headers: _h, text: _t, attachment_list: _a, html: _html, ...rest }: CaughtMailDetail): CaughtMail => rest;

export function list(name: string, query: string, limit: number) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const all = box(name).filter((m) => {
    const haystack = [m.subject, m.from, m.to.join(" "), m.rcpt_to.join(" "), m.snippet].join(" ").toLowerCase();
    return words.every((word) => haystack.includes(word));
  });
  const bytes = box(name).reduce((sum, m) => sum + m.size, 0);
  return { items: all.slice(0, limit).map(summary), total: all.length, usage: { messages: box(name).length, bytes } };
}

export const detail = (name: string, id: string) => box(name).find((m) => m.id === id) ?? null;

export function remove(name: string, id: string): boolean {
  const found = box(name);
  const index = found.findIndex((m) => m.id === id);
  if (index < 0) return false;
  found.splice(index, 1);
  return true;
}

export function removeAll(name: string): number {
  const count = box(name).length;
  boxes.set(name, []);
  return count;
}

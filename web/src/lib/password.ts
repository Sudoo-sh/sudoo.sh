// The password rules, the same as the server's (accounts.password_problem, NIST SP 800-63B): length over forced
// character classes, and no passwords that are guessed first. The server checks again; this only answers sooner.

import { words } from "@/i18n";
import auth from "@/i18n/auth";

const COMMON = [
  "password",
  "passw0rd",
  "123456",
  "qwerty",
  "azerty",
  "letmein",
  "welcome",
  "admin",
  "administrator",
  "iloveyou",
  "monkey",
  "dragon",
  "football",
  "baseball",
  "sunshine",
  "princess",
  "trustno1",
  "abc123",
  "odoo",
  "changeme",
  "secret",
  "master",
  "login",
  "sudoo",
  "starwars",
  "whatever",
  "superman",
  "batman",
  "shadow",
  "michael",
  "jordan",
  "hello",
  "freedom",
  "summer",
  "winter",
  "spring",
  "autumn",
  "computer",
  "internet",
  "default",
  "temp",
  "test",
];
const SEQUENCES = [
  "abcdefghijklmnopqrstuvwxyz",
  "0123456789",
  "qwertyuiop",
  "asdfghjkl",
  "zxcvbnm",
  "1qaz2wsx3edc",
  "qazwsxedc",
];
export const MIN_LENGTH = 12;
const t = () => words(auth).password;

export interface Person {
  username?: string;
  email?: string;
  names?: string[];
}

function hasSequence(lowered: string): boolean {
  return SEQUENCES.some((sequence) =>
    [sequence, [...sequence].reverse().join("")].some((text) => {
      for (let i = 0; i + 5 <= text.length; i++) if (lowered.includes(text.slice(i, i + 5))) return true;
      return false;
    }),
  );
}

/** Why the password is not accepted, or null. */
export function passwordProblem(password: string, person: Person = {}): string | null {
  const lowered = password.toLowerCase();
  if (password.length < MIN_LENGTH) return t().tooShort(MIN_LENGTH, password.length);
  if (password.length > 128) return t().tooLong;
  const personal = [person.username, person.email?.split("@")[0], ...(person.names ?? [])].filter(
    (p): p is string => !!p && p.length >= 3,
  );
  if (personal.some((part) => lowered.includes(part.toLowerCase()))) return t().personal;
  if (COMMON.some((word) => lowered.includes(word))) return t().common;
  if (hasSequence(lowered)) return t().sequence;
  if (/(.)\1\1\1/.test(password)) return t().repeats;
  if (new Set(password).size < 6) return t().simple;
  return null;
}

/** 0 (not accepted) to 4 (very strong), from the length and the kinds of characters. */
export function strength(password: string, person: Person = {}): 0 | 1 | 2 | 3 | 4 {
  if (passwordProblem(password, person)) return 0;
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(password)).length;
  const points = (password.length >= 16 ? 1 : 0) + (password.length >= 20 ? 1 : 0) + (kinds >= 3 ? 1 : 0);
  return Math.min(4, 1 + points) as 1 | 2 | 3 | 4;
}

/** A random password of 20 characters from an alphabet without look-alikes (0/O, 1/l/I). */
export function generatePassword(): string {
  const alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_.!";
  for (;;) {
    const bytes = crypto.getRandomValues(new Uint8Array(20));
    const made = Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
    if (!passwordProblem(made)) return made;
  }
}

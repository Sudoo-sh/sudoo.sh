/**
 * Counting words. English has one form for 1 and one for the rest; Arabic has six (Intl.PluralRules "ar": zero, one,
 * two, few for 3-10, many for 11-99, other for 100 and above). A form is a template where "#" is the number, written
 * with Western digits; a form without "#" names the count in words ("بيئتان", two instances). The same rules as the
 * account site (app.sudoo.sh) and the sign-in site.
 */

export type Plural = { one: string; other: string; zero?: string; two?: string; few?: string; many?: string };

const RULES = { en: new Intl.PluralRules("en"), ar: new Intl.PluralRules("ar") } as const;
const NUMBER = new Intl.NumberFormat("en");

/** "3 instances", "بيئتان", "11 بيئة" */
export function countIn(language: keyof typeof RULES, n: number, forms: Plural): string {
  const category = RULES[language].select(n) as keyof Plural;
  const template = forms[category] ?? forms.other;
  return template.replace("#", NUMBER.format(n));
}

/** An address, a code, a branch or a name inside an Arabic sentence: kept left to right, apart from the text around
 *  it (Unicode isolates, the plain-text form of <bdi dir="ltr">). */
export const ltr = (text: string | number) => `⁦${text}⁩`;

/** A name inside a sentence, isolated in its own direction (first strong letter decides), as <bdi> does. */
export const iso = (text: string | number) => `⁨${text}⁩`;

/**
 * Pronunciation overrides: how a word SOUNDS.
 *
 * Runs last, after normalization has decided what a token *means*. See
 * `shared/pronunciation.json` and spec section 11 for that division.
 *
 * This exists because the permissive dictionary mispronounces proper nouns and
 * coinages, which is the failure a reader is judged on: a listener forgives a
 * missing comma and does not forgive their own company's name. The dictionary
 * cannot be fixed by more data for words nobody has heard of, so the answer is
 * to let a person fix one in two seconds and never hear it again.
 *
 * Two sources, both applied: the shipped list, and whatever the user has added.
 * User rules win, so overriding a shipped mistake is possible.
 */

/** @typedef {{match: string, say: string, regex?: boolean, matchCase?: boolean}} Rule */

/** @type {Rule[]} */ let BUILTIN = [];
/** @type {Rule[]} */ let USER = [];
/** Compiled, longest literal first. Rebuilt when either source changes. */
let COMPILED = [];

const isWordChar = (c) => c !== undefined && /[\p{L}\p{N}]/u.test(c);

function compile() {
  // A user rule *replaces* the shipped rule for the same word rather than
  // merely running before it. Ordering alone is not enough: a user rule turning
  // "GIF" into "gif" leaves text the shipped rule then matches and turns into
  // "jiff", undoing the override. The shipped one has to go.
  const overridden = new Set(USER.map((r) => r.match.toLowerCase()));
  const shipped = BUILTIN.filter((r) => !overridden.has(r.match.toLowerCase()));

  COMPILED = [...USER, ...shipped]
    .map((r, i) => {
      // Source is tagged here rather than inferred by identity later: the
      // mapped object is a new reference, so an `includes` test against the
      // original array is always false and the ordering silently does nothing.
      const fromUser = i < USER.length;
      if (!r.regex) return { ...r, re: null, fromUser };
      try {
        return { ...r, re: new RegExp(r.match, r.matchCase ? 'g' : 'gi'), fromUser };
      } catch {
        // A user typed a bad pattern. Dropping it is better than throwing on
        // every sentence thereafter.
        return null;
      }
    })
    .filter(Boolean)
    // User rules first, then longest literal, so a longer match is never
    // shadowed by a shorter one contained inside it.
    .sort((a, b) => (b.fromUser - a.fromUser) || (b.match.length - a.match.length));
}

/** @param {{builtin?: Rule[]}} data parsed shared/pronunciation.json */
export function loadBuiltin(data) {
  BUILTIN = Array.isArray(data?.builtin) ? data.builtin : [];
  compile();
  return BUILTIN.length;
}

/** @param {Rule[]} rules the user's own, from extension storage */
export function loadUser(rules) {
  USER = Array.isArray(rules) ? rules : [];
  compile();
  return USER.length;
}

export function rules() {
  return { builtin: [...BUILTIN], user: [...USER] };
}

/**
 * Apply the overrides.
 *
 * Literal rules match on word boundaries, using the same rule as say-as
 * expansion: rejected when a letter or digit sits immediately either side. That
 * is what stops "UI" firing inside "GUI" and "env" inside "environment".
 *
 * @param {string} text
 * @returns {{text: string, changed: boolean}}
 */
export function pronounce(text) {
  if (!text || !COMPILED.length) return { text, changed: false };

  let out = text;
  let changed = false;

  for (const rule of COMPILED) {
    if (rule.re) {
      rule.re.lastIndex = 0;
      const next = out.replace(rule.re, rule.say);
      if (next !== out) { out = next; changed = true; }
      continue;
    }

    // Literal, boundary-anchored, left to right so an earlier replacement
    // cannot be rematched by the same rule.
    let result = '';
    let i = 0;
    const needle = rule.matchCase ? rule.match : rule.match.toLowerCase();
    const hay = rule.matchCase ? out : out.toLowerCase();

    while (i < out.length) {
      const at = hay.indexOf(needle, i);
      if (at === -1) { result += out.slice(i); break; }
      const end = at + needle.length;
      if (isWordChar(out[at - 1]) || isWordChar(out[end])) {
        result += out.slice(i, at + 1);
        i = at + 1;
        continue;
      }
      result += out.slice(i, at) + rule.say;
      i = end;
      changed = true;
    }
    out = result;
  }

  return { text: out, changed };
}

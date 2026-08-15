/* Glossary popups: grammar terms in rendered reports get a dotted underline; a click
   shows a plain-English definition. Static data, matched client-side — no server calls.
   Definitions are written for learners: short sentences, common words only. */

const GLOSSARY = [
  {name: "noun", def: "A word that names a thing, person, or idea.", ex: "report, mistake, user"},
  {name: "verb", def: "A word for an action or a state.", ex: "write, need, be"},
  {name: "adjective", def: "A word that describes a noun.", ex: "a simple fix"},
  {name: "adverb", def: "A word that describes a verb or an adjective. It often ends in -ly.", ex: "the app works incorrectly"},
  {name: "pronoun", def: "A short word used instead of a noun.", ex: "it, they, you"},
  {name: "preposition", def: "A small word that connects a noun to the rest of the sentence.", ex: "in, on, after, about"},
  {name: "article", def: "The small words a, an, and the before a noun.", ex: "a report = any one; the report = a specific one"},
  {name: "auxiliary", forms: ["auxiliary verb", "auxiliary", "helper verb"], def: "A helper verb: do, does, did, be, or have. English uses it to make questions, negatives, and tenses.", ex: "Why did you split it?"},
  {name: "infinitive", def: "The basic form of a verb with to.", ex: "we need to improve it"},
  {name: "-ing form", forms: ["-ing form", "gerund"], def: "The verb with -ing, used like a noun. Use it after a preposition and after verbs like start, stop, keep.", ex: "what about making a page"},
  {name: "third-person singular", forms: ["third-person singular", "third person singular"], def: "The verb form for he, she, it, or a user. It takes -s.", ex: "a user repeats the mistake"},
  {name: "tense", def: "The verb form that shows time: past, present, or future.", ex: "wrote, writes, will write"},
  {name: "past participle", def: "The third form of a verb (make → made). Used in perfect tenses and in the passive.", ex: "made, done, written"},
  {name: "passive", def: "The form where the subject does not do the action — it receives it.", ex: "errors are made, not errors make"},
  {name: "subject", def: "The person or thing that does the action. In English it comes before the verb.", ex: "a user repeats the mistake"},
  {name: "object", def: "The person or thing that the action happens to.", ex: "explain the word to me"},
  {name: "word order", def: "The fixed order of English: subject, then verb, then object. In a question the auxiliary moves before the subject.", ex: "why do we need it?"},
  {name: "modify", forms: ["modify", "modifies", "modified", "modifying"], def: "To describe or change the meaning of another word.", ex: "an adverb modifies a verb"},
  {name: "singular / plural", forms: ["singular", "plural"], def: "One thing, or more than one.", ex: "mistake → mistakes"},
  {name: "countable noun", forms: ["countable noun", "countable"], def: "A noun you can count. It takes a/an and has a plural.", ex: "a mistake, two mistakes"},
  {name: "uncountable noun", forms: ["uncountable noun", "uncountable"], def: "A noun with no plural and no a/an.", ex: "work, advice, information"},
  {name: "possessive", def: "The form that shows who owns something.", ex: "your idea, the app's settings"},
  {name: "comparative / superlative", forms: ["comparative", "superlative"], def: "The forms for comparing: -er / the -est, or more / the most.", ex: "easier / the easiest; more simply / most simply"},
  {name: "calque", def: "A word-for-word translation from your language. It sounds wrong in English.", ex: "\"the work with prompts\" for \"prompt handling\""},
  {name: "false friend", def: "A word in your language that looks like an English word but means something different.", ex: "тумблер is a toggle, not a tumbler"},
  {name: "fixed phrase", forms: ["fixed phrase", "set phrase"], def: "Words that always go together in one exact form.", ex: "in detail, take up space"},
  {name: "collocation", def: "Words that naturally go together in English.", ex: "you make a mistake, you don't \"do\" one"},
  {name: "phrasal verb", def: "A verb plus a small word, with its own meaning.", ex: "take up, turn off, find out"},
  {name: "contraction", def: "A short form written with an apostrophe (').", ex: "don't, it's, we'll"},
  {name: "register", def: "How formal or informal a word is.", ex: "kids is casual, children is neutral"},
  {name: "wordiness", def: "Using more words than needed.", ex: "\"let's think one more time about\" → \"let's revisit\""},
];

/* Every matchable surface (forms plus naive plurals) → its entry. */
const TERMS = new Map();
for (const entry of GLOSSARY) {
  for (const form of entry.forms ?? [entry.name]) {
    TERMS.set(form, entry);
    if (form.endsWith("y")) TERMS.set(form.slice(0, -1) + "ies", entry);
    else TERMS.set(form + "s", entry);
  }
}
/* Longest-first so "auxiliary verb" beats "auxiliary" at the same position; the
   lookarounds are word boundaries that also treat "-" as a word char, so "verb"
   never matches inside "adverb" and "-ing form" can start with a hyphen. */
const TERM_RE = new RegExp(
  "(?<![\\w-])(?:" +
    [...TERMS.keys()].sort((a, b) => b.length - a.length).map(f => f.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") +
    ")(?![\\w-])",
  "gi");

/* Wrap glossary terms found in root's text in <span class="term">. Quoted user
   fragments sit in code/pre and are skipped — only the lesson's own prose is marked. */
function annotateTerms(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest("code, pre, a, .term") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.nodeValue;
    let frag = null, last = 0;
    for (const m of text.matchAll(TERM_RE)) {
      frag ??= document.createDocumentFragment();
      frag.append(text.slice(last, m.index));
      const span = document.createElement("span");
      span.className = "term";
      span.tabIndex = 0;
      span.dataset.term = TERMS.get(m[0].toLowerCase()).name;
      span.textContent = m[0];
      frag.append(span);
      last = m.index + m[0].length;
    }
    if (frag) { frag.append(text.slice(last)); node.replaceWith(frag); }
  }
}

const termPop = document.createElement("div");
termPop.className = "term-pop";
termPop.hidden = true;
document.body.append(termPop);
let termPopFor = null;  // the .term span the popup is open for

function hideTermPop() { termPop.hidden = true; termPopFor = null; }

function toggleTermPop(span) {
  if (termPopFor === span) { hideTermPop(); return; }
  termPopFor = span;
  // Read the rect first: a term clicked inside the popup's own definition is detached
  // by replaceChildren below, and a detached span measures at 0,0.
  const r = span.getBoundingClientRect();
  const entry = GLOSSARY.find(e => e.name === span.dataset.term);
  termPop.replaceChildren();
  for (const [cls, text] of [["term-name", entry.name], ["term-def", entry.def], ["term-example", entry.ex]]) {
    const s = document.createElement("span");
    s.className = cls;
    s.textContent = text;
    if (cls === "term-def") annotateTerms(s);  // terms used inside a definition are clickable too
    termPop.append(s);
  }
  termPop.hidden = false;  // unhide before measuring: offsetWidth is 0 while hidden
  termPop.style.top = `${r.bottom + scrollY + 6}px`;
  termPop.style.left = `${Math.max(0, Math.min(r.left + scrollX, scrollX + document.documentElement.clientWidth - termPop.offsetWidth - 12))}px`;
}

document.addEventListener("click", e => {
  const span = e.target instanceof Element && e.target.closest(".term");
  if (span) toggleTermPop(span);
  else if (!termPop.contains(e.target)) hideTermPop();
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") hideTermPop();
  else if ((e.key === "Enter" || e.key === " ") && e.target instanceof Element && e.target.classList.contains("term")) {
    e.preventDefault();  // Space must not scroll the page
    toggleTermPop(e.target);
  }
});

for (const md of document.querySelectorAll(".markdown")) annotateTerms(md);

"""
Matching rules (no browser here, so they can be tested on their own).

- Which question on the form a data.json entry belongs to  -> find_card()
- Which option to pick for an answer                        -> pick_option()
- A dry run over the whole form for the "Check form" button -> check_structure()

The form is in French: accents, case and punctuation are ignored, French small words are
ignored when comparing wordings, and ordinals are understood ("1ère", "1ere", "première",
"premier", "1er", "1" all mean 1). English wordings work too.
"""

import os
import re
import unicodedata
from difflib import SequenceMatcher

FIELD_TYPES = ("text", "choice", "multi", "dropdown", "file", "tick")

# Ways to find the question of a field, from the most to the least certain.
# All fields are tried with one way before the next way is used, so a sure match is never
# taken away by a weaker one (e.g. "Prénom" cannot grab the "Nom et prénom" question when
# "Nom complet" lists "Nom et prénom" as an alias).
TIERS = ("exact", "partial", "answer", "input", "similar")
TIER_TEXT = {
    "exact": "",
    "partial": "found by part of its title",
    "answer": "found by its answer (the only question with this option)",
    "input": "found by the kind of box (e-mail / phone)",
    "similar": "found by similar wording",
}
HOW_TEXT = {"exact": "", "same": "same meaning", "partial": "partial name", "number": "same number"}

SIMILAR_MIN = 0.75     # similarity needed to accept a question found by similar wording
SIMILAR_MARGIN = 0.12  # ... and how much better than the next best it must be

STOPWORDS = {
    # French
    "le", "la", "les", "l", "de", "du", "des", "d", "un", "une", "et", "ou", "a", "au", "aux", "en",
    "dans", "sur", "pour", "par", "avec", "votre", "vos", "ton", "ta", "tes", "son", "sa", "ses",
    "mon", "ma", "mes", "quel", "quelle", "quels", "quelles", "est", "sont", "ce", "cette", "ces",
    "qui", "que", "quoi", "svp", "s", "vous", "nous", "je", "tu", "il", "elle", "y",
    # English
    "the", "of", "your", "what", "is", "an", "and", "or", "to", "for", "in", "on", "please",
}
ORDINAL_WORDS = {
    "premier": 1, "premiere": 1, "first": 1,
    "deuxieme": 2, "second": 2, "seconde": 2, "deux": 2,
    "troisieme": 3, "third": 3, "trois": 3,
    "quatrieme": 4, "fourth": 4, "quatre": 4,
    "cinquieme": 5, "fifth": 5, "cinq": 5,
    "sixieme": 6, "sixth": 6, "six": 6,
    "septieme": 7, "seventh": 7, "sept": 7,
    "huitieme": 8, "eighth": 8, "huit": 8,
    "neuvieme": 9, "ninth": 9, "neuf": 9,
    "dixieme": 10, "tenth": 10, "dix": 10,
}
# 1, 1er, 1ere, 1re, 1e, 2eme, 2ieme, 2em, 2nd, 3rd, 1st, 4th ...
_NUMBER_WORD = re.compile(r"^(\d{1,2})(?:er|ere|re|e|eme|ieme|em|nd|rd|st|th)?$")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^\+?[\d\s.\-()]{8,20}$")
EMAIL_WORDS = {"mail", "email", "courriel"}
PHONE_WORDS = {"telephone", "tel", "portable", "gsm", "mobile", "phone", "whatsapp"}
PLACEHOLDER_OPTIONS = {"choisir", "selectionner", "select", "choose"}


# ---------------------------------------------------------------- text helpers
def _plain(text):
    text = unicodedata.normalize("NFKD", str(text or "")).lower()
    return "".join(c for c in text if not unicodedata.combining(c))


def norm(text):
    """Lower-case, no accents, no spaces/punctuation: 'Italy ( August )' -> 'italyaugust'."""
    return re.sub(r"[\W_]+", "", _plain(text))


def words(text):
    """'1ère année Médecine' -> ['1ere', 'annee', 'medecine']"""
    return [w for w in re.split(r"[\W_]+", _plain(text)) if w]


def clean_title(text):
    text = re.sub(r"obligatoire|required", " ", str(text), flags=re.I)
    return text.split("\n")[0].strip().rstrip("*").strip()


def number_of_word(word):
    match = _NUMBER_WORD.match(word)
    if match:
        return int(match.group(1))
    return ORDINAL_WORDS.get(word)


def ordinal_of(text):
    """The first number/ordinal in a text: '5ème année' -> 5, 'première' -> 1, 'Aucun' -> None."""
    for word in words(text):
        number = number_of_word(word)
        if number is not None:
            return number
    return None


def canonical(text):
    """The meaningful words, with numbers written one way:
    'Quel est votre pays de 1er choix ?' and 'Pays de premier choix' -> '#1 choix pays' style."""
    out = []
    for word in words(text):
        number = number_of_word(word)
        if number is not None:
            out.append(f"#{number}")
        elif word not in STOPWORDS:
            out.append(word)
    return " ".join(out)


def similarity(a, b):
    """0..1: how close two wordings are (small words and number spellings ignored)."""
    ca, cb = canonical(a), canonical(b)
    if not ca or not cb:
        return 0.0
    ta, tb = set(ca.split()), set(cb.split())
    dice = 2 * len(ta & tb) / (len(ta) + len(tb))
    return max(dice, SequenceMatcher(None, ca, cb).ratio())


def as_list(value):
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


# ---------------------------------------------------------------- profile and templates
def placeholders(value):
    return {name for v in as_list(value) for name in _PLACEHOLDER.findall(v)} if value is not None else set()


def render(value, profile):
    """'{first_name} {last_name}' -> 'Youssef ...' using the "profile" section of data.json."""
    if isinstance(value, list):
        return [render(v, profile) for v in value]
    if not isinstance(value, str):
        return value
    return _PLACEHOLDER.sub(lambda m: str(profile[m.group(1)]) if m.group(1) in profile else m.group(0), value)


def prepare_fields(data):
    """The fields of data.json with {placeholders} replaced by the profile values."""
    profile = data.get("profile") or {}
    out = []
    for field in data.get("fields") or []:
        copy = dict(field)
        if "value" in copy:
            copy["value"] = render(copy["value"], profile)
        out.append(copy)
    return out


# ---------------------------------------------------------------- options
def alias_group(value, aliases):
    """All names that mean the same option, e.g. 'UK' -> ['UK', 'United Kingdom', ...]."""
    w = norm(value)
    for key, names in (aliases or {}).items():
        group = [key] + list(names)
        if any(norm(name) == w for name in group):
            return group
    return [value]


def pick_index(labels, wanted, group=None):
    """Return (index, ambiguous_labels): exact match on any name in the group, then (only for
    names of 4+ letters) 'name is inside the label'. Short values like 'UK' never match inside
    other words."""
    names = [n for n in (group or [wanted]) if norm(n)]
    if not names:
        return None, []
    for name in names:
        for i, label in enumerate(labels):
            if norm(label) == norm(name):
                return i, []
    for name in names:
        if len(norm(name)) < 4:
            continue
        partial = [i for i, label in enumerate(labels) if norm(name) in norm(label)]
        if len(partial) == 1:
            return partial[0], []
        if len(partial) > 1:
            return None, [labels[i] for i in partial]
    return None, []


def pick_option(labels, values, aliases=None):
    """Choose the option for an answer.
    `values` is one value or a list of acceptable values (the first that matches wins).
    Returns (index, how, value_used, ambiguous_labels); how is one of
      exact   - same text (ignoring case/accents/punctuation), or an alias of it
      same    - same meaning ('1ère année' = 'première année', small words ignored)
      partial - the value is inside the option ('Marrakech' -> 'Marrakech - AEMM'), 4+ letters
      number  - same number ('5' or '5eme annee' -> '5ème année Médecine Générale')
    Several options that mean the same thing (e.g. the form lists '1ère année' twice) count
    as one. Anything else ambiguous is not guessed."""
    labels = [str(label) for label in labels]
    ambiguous = []
    for value in as_list(values):
        names = [n for n in alias_group(value, aliases) if norm(n)]
        for name in names:
            for i, label in enumerate(labels):
                if norm(label) == norm(name):
                    return i, "exact", value, []
        for name in names:
            wanted = canonical(name)
            hits = [i for i, label in enumerate(labels) if wanted and canonical(label) == wanted]
            if hits:
                return hits[0], "same", value, []
        for name in names:
            if len(norm(name)) < 4:
                continue
            hits = [i for i, label in enumerate(labels) if norm(name) in norm(label)]
            if len(hits) == 1:
                return hits[0], "partial", value, []
            if len(hits) > 1:
                ambiguous = [labels[i] for i in hits]
        for name in names:
            number = ordinal_of(name)
            if number is None:
                continue
            extra = {w for w in canonical(name).split() if not w.startswith("#")}
            hits = [i for i, label in enumerate(labels)
                    if ordinal_of(label) == number and extra <= set(canonical(label).split())]
            if hits and len({canonical(labels[i]) for i in hits}) == 1:
                return hits[0], "number", value, []
            if len(hits) > 1:
                ambiguous = [labels[i] for i in hits]
    return None, None, None, ambiguous


def choice_note(label, how, used):
    """Log text for an option that was not an exact match, so you can check it."""
    if not how or how == "exact":
        return ""
    return f'chose "{label}" for "{used}" ({HOW_TEXT.get(how, how)})'


# ---------------------------------------------------------------- questions
def field_type(field):
    return str(field.get("type", "text")).strip().lower()


def field_names(field):
    """The question text of a field plus its "aliases"."""
    names = [str(field.get("question", ""))] + [str(a) for a in (field.get("aliases") or [])]
    return [n for n in names if norm(n)]


def compatible(ftype, kind):
    """Can a field of this type answer a question of this kind?
    Lenient on purpose (a question that changed from radio buttons to a dropdown still works),
    but a tick box only goes to a checkbox, a document only to an upload question, and text
    never goes to checkboxes (so an e-mail address cannot land on the 'E-mail' tick box)."""
    if ftype == "tick":
        return kind == "multi"
    if ftype == "file":
        return kind == "file"
    if kind == "file":
        return False
    if ftype == "text":
        return kind in ("text", "choice", "dropdown")
    return True


def _value_kind(value):
    first = as_list(value)[0].strip() if value not in (None, "", []) else ""
    if _EMAIL.match(first):
        return "email"
    if _PHONE.match(first) and sum(c.isdigit() for c in first) >= 8:
        return "phone"
    return None


def in_section(field, card):
    """A field with "section": "SCORE" only answers questions on a page whose section title
    contains SCORE (forms that ask the same questions once per programme)."""
    section = norm(field.get("section") or "")
    return not section or section in norm(card.get("section", ""))


def find_card(cards, field, tier, used=(), aliases=None):
    """Find the question (card) for a field using one way of matching (see TIERS).
    `cards` are dicts with title, kind, options, input, section. Cards in `used` are already
    answered. Returns (index, ambiguous_titles, detail)."""
    ftype = field_type(field)
    free = [i for i, c in enumerate(cards)
            if i not in used and compatible(ftype, c.get("kind", "text")) and in_section(field, c)]
    names = field_names(field)
    title = {i: str(cards[i].get("title", "")) for i in free}

    def result(hits, detail=""):
        if len(hits) == 1:
            return hits[0], [], detail
        if len(hits) > 1:
            return None, [title[i] for i in hits], ""
        return None, [], ""

    if tier == "exact":
        hits = [i for i in free if any(
            norm(title[i]) == norm(n) or (canonical(n) and canonical(title[i]) == canonical(n)) for n in names)]
        keywords = [str(k) for k in (field.get("keywords") or []) if norm(k)]
        if not hits and keywords:
            hits = [i for i in free if all(norm(k) in norm(title[i]) for k in keywords)]
        return result(hits)
    if tier == "partial":
        hits = [i for i in free if any(len(norm(n)) >= 4 and norm(n) in norm(title[i]) for n in names)]
        return result(hits)
    if tier == "answer":
        if ftype not in ("choice", "dropdown", "multi") or field.get("value") in (None, "", []):
            return None, [], ""
        first = as_list(field["value"])[0] if ftype == "multi" else field["value"]
        hits = [i for i in free if cards[i].get("kind") in ("choice", "dropdown", "multi") and cards[i].get("options")
                and pick_option(cards[i]["options"], first, aliases)[1] in ("exact", "same")]
        return (hits[0], [], "") if len(hits) == 1 else (None, [], "")
    if tier == "input":
        kind = _value_kind(field.get("value")) if ftype == "text" else None
        if kind is None:
            return None, [], ""
        wanted_words, input_type = (EMAIL_WORDS, "email") if kind == "email" else (PHONE_WORDS, "tel")
        hits = [i for i in free if cards[i].get("kind") == "text" and (
            cards[i].get("input") == input_type or set(words(title[i])) & wanted_words)]
        return (hits[0], [], "") if len(hits) == 1 else (None, [], "")
    if tier == "similar":
        scored = sorted(((max(similarity(title[i], n) for n in names), i) for i in free), reverse=True)
        if scored and scored[0][0] >= SIMILAR_MIN and (
                len(scored) == 1 or scored[0][0] - scored[1][0] >= SIMILAR_MARGIN):
            return scored[0][1], [], f"similarity {scored[0][0]:.2f}"
        return None, [], ""
    raise ValueError(tier)


def title_claims(cards, fields, aliases=None):
    """Indexes of the cards that some field matches by its title (exact or partial)."""
    claimed = set()
    for field in fields:
        for tier in ("exact", "partial"):
            index, _, _ = find_card(cards, field, tier, (), aliases)
            if index is not None:
                claimed.add(index)
                break
    return claimed


# ---------------------------------------------------------------- reading Google's form data
# Google puts the whole form (every page) in the page as FB_PUBLIC_LOAD_DATA_.
# item = [id, title, description, type, [[entry_id, options, required, ...]], ...]
FB_KINDS = {0: "text", 1: "text", 2: "choice", 3: "dropdown", 4: "multi", 5: "choice",
            9: "text", 10: "text", 13: "file"}
FB_PAGE_BREAK = 8


def parse_fb(fb):
    """Pages of question cards from Google's FB_PUBLIC_LOAD_DATA_ (questions of every page,
    without having to fill and click through the form)."""
    try:
        items = fb[1][1] or []
    except (TypeError, IndexError):
        return []
    pages = [[]]
    section = ""
    for item in items:
        if not isinstance(item, list) or len(item) < 4:
            continue
        if item[3] == FB_PAGE_BREAK:
            pages.append([])
            section = clean_title(item[1] or "")  # the title of the new page, e.g. "Choix de Pays (SCOPE)"
            continue
        kind = FB_KINDS.get(item[3])
        if kind is None:  # titles, images, videos, grids
            continue
        options, required = [], False
        try:
            entry = item[4][0]
            options = [str(o[0]) for o in (entry[1] or []) if o and o[0] not in (None, "")]
            required = bool(entry[2])
        except (TypeError, IndexError):
            pass
        pages[-1].append({"title": clean_title(item[1] or ""), "kind": kind, "options": options,
                          "input": "", "required": required, "section": section})
    return pages


# ---------------------------------------------------------------- the dry run ("Check form")
def describe_answer(field, card, aliases=None):
    """What would be put in this question. Returns (how, text); how is None when the answer is
    not one of the question's options (the question may then exist on another page)."""
    ftype, kind = field_type(field), card.get("kind", "text")
    value = field.get("value")
    if ftype == "file":
        path = str(value or "").strip()
        return "exact", (f"document {os.path.basename(path)}" if path else
                         "no file chosen: you will upload it yourself")
    if ftype == "tick":
        return "exact", "the box will be ticked"
    options = card.get("options") or []
    if kind == "text" or not options:
        return "exact", f'"{as_list(value)[0]}"'
    groups = [[v] for v in as_list(value)] if ftype == "multi" else [value]
    chosen, worst = [], "exact"
    for alternatives in groups:
        index, how, used, ambiguous = pick_option(options, alternatives, aliases)
        if index is None:
            shown = ", ".join((ambiguous or options)[:12])
            what = "several options match" if ambiguous else "not one of the options"
            return None, f'"{as_list(alternatives)[0]}" is {what}: {shown}'
        note = choice_note(options[index], how, used)
        chosen.append(f'"{options[index]}"' + (f" ({note})" if note else ""))
        if how != "exact":
            worst = how
    return worst, " + ".join(chosen)


def snippet_for(card):
    """A ready-to-paste data.json line for a question that is not in data.json yet."""
    import json
    kind = card.get("kind", "text")
    snippet = {"question": card.get("title", ""), "type": kind if kind in FIELD_TYPES else "text", "value": ""}
    if kind == "multi":
        snippet["value"] = []
    return json.dumps(snippet, ensure_ascii=False)


def check_structure(pages, fields, aliases=None):
    """Dry run of the filling over every page. Nothing is clicked.
    Returns a list of (status, text):
      OK        found, answer is exact
      CHECK     found, but by a less certain way or the option was not an exact match: look at it
      PROBLEM   found, but the answer cannot be used (not an option, several match)
      NOT FOUND no question for this entry
      SKIPPED   optional entry, not on this form
      MISSING   required question that data.json does not answer (the form would be blocked)
      NEW       optional question that data.json does not answer"""
    entries = []
    pending = list(fields)
    misses = {}
    answered_titles = set()
    unanswered = []
    for page_no, cards in enumerate(pages, start=1):
        used = set()
        deferred = []
        for tier in TIERS:
            next_round = []
            for field in pending:
                question = str(field.get("question", ""))
                index, ambiguous, detail = find_card(cards, field, tier, used, aliases)
                if index is None:
                    if ambiguous and tier in ("exact", "partial"):
                        entries.append(("PROBLEM", f'"{question}": several questions match: '
                                        + " | ".join(ambiguous[:4]) + ". Make the question text more precise."))
                        continue
                    next_round.append(field)
                    continue
                card = cards[index]
                answered_titles.add(norm(card["title"]))
                how, text = describe_answer(field, card, aliases)
                if how is None:  # the answer may belong to the same question on another page
                    misses[(question, field.get("section") or "")] = text
                    deferred.append(field)
                    continue
                used.add(index)
                misses.pop((question, field.get("section") or ""), None)
                found = TIER_TEXT[tier] + (f", {detail}" if detail else "")
                status = "OK" if tier == "exact" and how in ("exact", "same") else "CHECK"
                entries.append((status, f'"{question}" -> "{card["title"]}" (page {page_no})'
                                + (f" [{found}]" if found else "") + f": {text}"))
            pending = next_round
        pending = pending + deferred
        unanswered += [(page_no, card) for i, card in enumerate(cards) if i not in used]

    sections = {norm(card.get("section", "")) for cards in pages for card in cards}
    for field in pending:
        question = str(field.get("question", ""))
        section = norm(field.get("section") or "")
        key = (question, field.get("section") or "")
        if key in misses:
            entries.append(("PROBLEM", f'"{question}": {misses[key]}'))
        elif section and not any(section in s for s in sections):
            entries.append(("SKIPPED", f'"{question}": the form has no "{field.get("section")}" section'))
        elif field.get("optional"):
            entries.append(("SKIPPED", f'"{question}": optional, not on this form'))
        else:
            entries.append(("NOT FOUND", f'"{question}": no question on the form matches it '
                            "(add the form's wording to its \"aliases\")"))
    for page_no, card in unanswered:
        if norm(card["title"]) in answered_titles:
            continue  # the same question on another branch, e.g. the SCORE page when you chose SCOPE
        if card.get("required"):
            entries.append(("MISSING", f'required question not in data.json: "{card["title"]}" (page {page_no}). '
                            f"Add: {snippet_for(card)}"))
        else:
            entries.append(("NEW", f'optional question not in data.json: "{card["title"]}" (page {page_no})'))
    return entries


# ---------------------------------------------------------------- checking data.json itself
def validate(data, url=None, file_exists=os.path.isfile, resolve=lambda p: p):
    """Return a list of human-readable problems (empty list = file is fine)."""
    if not isinstance(data, dict):
        return ["The file must contain { ... } at the top level."]
    problems = []
    if not str(url or data.get("form_url", "")).strip().startswith("http"):
        problems.append('The form link is missing (paste it in the window or set "form_url"; it starts with https://).')
    aliases = data.get("option_aliases", {})
    if not isinstance(aliases, dict) or not all(
            isinstance(v, list) and all(isinstance(x, str) for x in v) for v in aliases.values()):
        problems.append('"option_aliases" must look like {"CZ": ["Czechia"], "UK": ["United Kingdom"]}.')
    profile = data.get("profile", {})
    if not isinstance(profile, dict) or not all(isinstance(v, (str, int, float)) for v in profile.values()):
        problems.append('"profile" must look like {"first_name": "...", "last_name": "..."}.')
        profile = {}
    fields = data.get("fields")
    if not isinstance(fields, list) or not fields:
        problems.append('"fields" is missing or empty.')
        return problems
    for i, field in enumerate(fields, 1):
        if not isinstance(field, dict):
            problems.append(f"Field {i}: it must be inside {{ }}.")
            continue
        question = str(field.get("question", "")).strip()
        label = f'"{question}"' if question else f"Field {i}"
        ftype = field_type(field)
        if not question:
            problems.append(f"Field {i}: 'question' is empty.")
        if ftype not in FIELD_TYPES:
            problems.append(f'{label}: unknown type "{ftype}". Use text, choice, multi, dropdown, file or tick.')
            continue
        for key in ("aliases", "keywords"):
            if key in field and not (isinstance(field[key], list) and all(isinstance(x, str) for x in field[key])):
                problems.append(f'{label}: "{key}" must be a list of texts, for example ["...", "..."].')
        if "optional" in field and not isinstance(field["optional"], bool):
            problems.append(f'{label}: "optional" must be true or false.')
        if "section" in field and not isinstance(field["section"], str):
            problems.append(f'{label}: "section" must be a text, for example "SCOPE".')
        unknown = placeholders(field.get("value")) - set(profile)
        if unknown:
            problems.append(f'{label}: {{{", ".join(sorted(unknown))}}} is not in "profile".')
        if ftype == "tick":
            continue
        if ftype == "file":
            path = str(field.get("value") or "").strip()
            # an empty path is allowed: that document is left for you to upload by hand
            if path and not file_exists(resolve(path)):
                problems.append(f"{label}: the file does not exist: {path}")
            continue
        value = render(field.get("value"), profile)
        if value in (None, "", []) or (isinstance(value, list) and not any(str(v).strip() for v in value)) \
                or (isinstance(value, str) and not value.strip()):
            problems.append(f'{label}: "value" is empty.')
        if ftype == "multi" and not isinstance(field.get("value"), list):
            problems.append(f'{label}: type "multi" needs a list, for example ["A", "B"].')
    return problems

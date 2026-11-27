"""
Form Auto-Filler
----------------
A simple window that fills a Google Form for you using the answers in data.json.
Start it with start.bat on Windows or start.command on a Mac (run setup.bat / setup.command once first).

How a question is found and how an option is chosen is decided in matching.py.
"""

import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext

from matching import (FIELD_TYPES, PLACEHOLDER_OPTIONS, TIER_TEXT, TIERS, as_list, check_structure,  # noqa: F401
                      choice_note, clean_title, field_type, find_card, norm, parse_fb, pick_index,
                      is_confirmation, pick_option, prepare_fields, snippet_for, title_claims)
from matching import validate as _validate

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.join(BASE_DIR, "data.json")
LOG_FILE = os.path.join(BASE_DIR, "last_run_log.txt")
BROWSER_DIR = os.environ.get("AUTOFILL_PROFILE_DIR", os.path.join(BASE_DIR, "browser_data"))
CDP_PORT = int(os.environ.get("AUTOFILL_CDP_PORT", "9333"))

IS_MAC = sys.platform == "darwin"
if IS_MAC:
    _MAC_APPS = ["/Applications", os.path.expanduser("~/Applications")]
    BROWSERS = [  # (name, path) in order of preference
        (name, os.path.join(folder, f"{app}.app", "Contents", "MacOS", app))
        for name, app in (("Brave", "Brave Browser"), ("Google Chrome", "Google Chrome"),
                          ("Microsoft Edge", "Microsoft Edge"))
        for folder in _MAC_APPS
    ]
    UI_FONT, MONO_FONT, FONT_GROW = "Helvetica Neue", "Menlo", 2  # Mac fonts look smaller at the same size
else:
    PF = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    PF86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    LAPPDATA = os.environ.get("LOCALAPPDATA", "")
    BROWSERS = [  # (name, path) in order of preference
        ("Brave", os.path.join(PF, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")),
        ("Brave", os.path.join(PF86, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")),
        ("Brave", os.path.join(LAPPDATA, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")),
        ("Google Chrome", os.path.join(PF, "Google", "Chrome", "Application", "chrome.exe")),
        ("Google Chrome", os.path.join(PF86, "Google", "Chrome", "Application", "chrome.exe")),
        ("Microsoft Edge", os.path.join(PF86, "Microsoft", "Edge", "Application", "msedge.exe")),
        ("Microsoft Edge", os.path.join(PF, "Microsoft", "Edge", "Application", "msedge.exe")),
    ]
    UI_FONT, MONO_FONT, FONT_GROW = "Segoe UI", "Consolas", 0
NEXT_WORDS = ["suivant", "next", "continuer", "continue"]
SUBMIT_WORDS = ["envoyer", "submit", "soumettre"]
MAX_PAGES = 15
# Google's file picker: seconds to wait after its 'Parcourir' button appears before using it.
# Clicking straight away lets the upload start, then the picker resets (seen on a real form).
# The first picker of a run is slower to get ready (tested: 1.5 s was too short for it, fine for
# the next ones). If it resets anyway, the upload is retried once with PICKER_RETRY_SETTLE_S.
PICKER_FIRST_SETTLE_S = 2.5
PICKER_SETTLE_S = 1.5
PICKER_RETRY_SETTLE_S = 4
# Each question card is tagged with data-af-q (see TAG_JS), so the script does not depend on
# Google's role names (the form changed from role="listitem" to plain containers).
LISTITEM = '[data-af-q]:visible'  # only the questions of the page being shown
TAG_JS = """() => {
    const CONTROL = '[role="radio"],[role="checkbox"],[role="listbox"],input,textarea,select';
    document.querySelectorAll('[data-af-q]').forEach(e => e.removeAttribute('data-af-q'));
    let n = 0;
    document.querySelectorAll('[role="heading"]').forEach(h => {
        let e = h;
        while (e && e !== document.body) {
            if (e.querySelectorAll('[role="heading"]').length > 1) return; // a container of several questions
            const fileButton = Array.from(e.querySelectorAll('[role="button"], button'))
                .some(b => /fichier|file/i.test(b.textContent));
            if (e.querySelector(CONTROL) || fileButton) {
                if (!e.hasAttribute('data-af-q')) e.setAttribute('data-af-q', String(n++));
                return;
            }
            e = e.parentElement;
        }
    });
    return n;
}"""

# JavaScript helpers used to read the page
SNAPSHOT_JS = """els => els.map(e => {
    const h = e.querySelector('[role="heading"]');
    const title = ((h ? h.innerText : e.innerText) || '').trim();
    let kind = 'text';
    if (e.querySelector('[role="radio"]')) kind = 'choice';
    else if (e.querySelector('[role="checkbox"]')) kind = 'multi';
    else if (e.querySelector('[role="listbox"]')) kind = 'dropdown';
    else if (IS_FILE(e)) kind = 'file';
    const options = Array.from(e.querySelectorAll('[role="radio"],[role="checkbox"],[role="option"]'))
        .map(o => (o.getAttribute('data-value') || o.getAttribute('aria-label') || o.textContent || '').trim())
        .filter(Boolean);
    const box = e.querySelector('input:not([type="hidden"]):not([type="file"]), textarea');
    const input = box ? (box.getAttribute('type') || box.tagName.toLowerCase()) : '';
    const required = !!(h && h.innerText.indexOf('*') >= 0);
    return {title, kind, options, input, required};
})"""
TEXT_JS = """els => els.map(e => e.textContent.replace(/\\s+/g, ' '))"""
TITLES_JS = """els => els.map(e => {
    const h = e.querySelector('[role="heading"]');
    return ((h ? h.innerText : e.innerText) || '').trim();
})"""
KIND_JS = """e => {
    if (e.querySelector('[role="radio"]')) return 'choice';
    if (e.querySelector('[role="checkbox"]')) return 'multi';
    if (e.querySelector('[role="listbox"]')) return 'dropdown';
    if (IS_FILE(e)) return 'file';
    return 'text';
}"""
# An upload question has a file input, or a button such as "Ajouter un fichier".
FILE_TEST = ("(!!e.querySelector('input[type=\"file\"]') || Array.from(e.querySelectorAll('[role=\"button\"], button'))"
             ".some(b => /fichier|file/i.test(b.textContent)))")
SNAPSHOT_JS = SNAPSHOT_JS.replace("IS_FILE(e)", FILE_TEST)
KIND_JS = KIND_JS.replace("IS_FILE(e)", FILE_TEST)
FINAL_JS = """els => els.map(e => {
    const h = e.querySelector('[role="heading"]');
    const title = ((h ? h.innerText : e.innerText) || '').trim();
    const box = e.querySelector('input:not([type="file"]), textarea');
    const checked = Array.from(e.querySelectorAll(
        '[role="radio"][aria-checked="true"], [role="checkbox"][aria-checked="true"]'))
        .map(o => o.getAttribute('data-value') || o.getAttribute('aria-label') || '');
    const lb = e.querySelector('[role="listbox"]');
    let value = '';
    if (checked.length) value = checked.join(' | ');
    else if (lb) {
        const sel = lb.querySelector('[role="option"][aria-selected="true"]');
        value = sel ? (sel.getAttribute('data-value') || sel.textContent || '').trim() : lb.innerText.trim();
    }
    else if (box) value = box.value;
    return {title, value};
})"""
# The option a dropdown has selected (Google marks it with aria-selected), or null if unknown.
SELECTED_JS = """b => { const s = b.querySelector('[role="option"][aria-selected="true"]');
    return s ? (s.getAttribute('data-value') || s.textContent || '').trim() : null; }"""
SETTLE_QUIET_MS = 800   # the answers on a page must stay unchanged this long before filling it
SETTLE_MAX_MS = 4000    # (Google restores a saved draft just after a page appears)
LABEL_JS = """els => els.map(e =>
    e.getAttribute('data-value') || e.getAttribute('aria-label') || e.innerText || '')"""
BUTTON_JS = """els => els.map(e => (e.innerText || e.getAttribute('aria-label') || '').trim())"""
# only lists INSIDE the question (the whole form is itself a list, so a plain
# '[role=list] [role=button]' selector would also match the 'Ajouter un fichier' button)
ATTACHED_JS = """e => Array.from(e.querySelectorAll('[role="list"]'))
    .flatMap(l => Array.from(l.querySelectorAll('[role="button"]')))
    .map(b => (b.innerText || '').trim()).filter(Boolean)"""
MISSING_JS = """els => els.filter(e => /Cette question est obligatoire|This is a required question/i.test(e.textContent))
    .map(e => { const h = e.querySelector('[role="heading"]');
                return ((h ? h.innerText : e.innerText) || '').split('\\n')[0].trim(); })"""
FB_JS = "() => (typeof FB_PUBLIC_LOAD_DATA_ !== 'undefined') ? FB_PUBLIC_LOAD_DATA_ : null"
# The title(s) of the page being shown, e.g. "Choix de Pays (SCOPE)": headings that are not a
# question and not the form's own title.
SECTION_JS = """() => Array.from(document.querySelectorAll('[role="heading"]'))
    .filter(h => !h.closest('[data-af-q]') && h.offsetParent !== null)
    .map(h => (h.innerText || '').trim())
    .filter(t => t && t !== document.title.trim())
    .join(' | ')"""
TICK_HOLD_MS = 1500     # a tick box must stay ticked this long before it counts as done
# "Wait for the form to open": the form link is loaded again every WAIT_MIN_S..WAIT_MAX_S seconds
# (random, so the checks do not look like a robot to Google) until its questions appear.
WAIT_MIN_S = 3.0
WAIT_MAX_S = 5.0
WAIT_LOAD_S = 4.0           # after loading the link, how long to wait for questions to show up
WAIT_HEARTBEAT_S = 600      # while waiting, a "still closed" line in the log every 10 minutes
CLOSED_RE = re.compile(r"n.est plus possible de r.pondre|n.accepte plus de r|no longer accepting|"
                       r"not accepting responses|ne peut plus .tre rempli", re.I)
NEXT_WAIT_S = 20  # how long to wait for the next page after 'Suivant' (branching pages are slower)


# ---------------------------------------------------------------- helpers
def short(err):
    return str(err).strip().splitlines()[0][:220] if str(err).strip() else type(err).__name__


class OptionMissing(RuntimeError):
    """The question is on this page but the answer is not one of its options.
    The value may belong to another page (e.g. another programme), so it is retried on later pages."""


def resolve_path(path):
    """Absolute path of a document; relative paths are taken from the autofill folder."""
    p = os.path.expanduser(str(path).strip().strip('"'))
    return p if os.path.isabs(p) else os.path.join(BASE_DIR, p)


def validate(data, url=None):
    """Problems in data.json (empty list = fine). `url` is the link typed in the window."""
    return _validate(data, url, os.path.isfile, resolve_path)


SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"'})


def load_data(path):
    with open(path, encoding="utf-8-sig") as handle:
        text = handle.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # TextEdit (Mac) and Word turn " into curly quotes while typing, which breaks the file.
        # Straight quotes are what was meant, so read it again with those.
        fixed = text.translate(SMART_QUOTES)
        if fixed == text:
            raise
        return json.loads(fixed)


# ---------------------------------------------------------------- browser work
class Filler:
    """Runs in a background thread. Talks to the GUI only through emit()."""

    def __init__(self, data, url, submit, emit, stop, keep_open=True, wait_open=False):
        self.data = data
        self.url = url
        self.submit = submit
        self.emit = emit
        self.stop = stop
        self.keep_open = keep_open  # False = close the browser when done (used by the tests)
        self.wait_open = wait_open  # True = the form may still be closed: wait until it opens
        self.pause_ms = int(data.get("pause_ms", 300))
        self.key_delay_ms = int(data.get("typing_delay_ms", 40))
        self.aliases = data.get("option_aliases", {}) or {}
        # tick required confirmation boxes that are not in data.json (see is_confirmation)
        self.auto_tick = data.get("auto_tick_confirmations", True) is not False
        self.results = []         # one entry per outcome: OK, ERROR, NOT FOUND, NEW, BLOCKED, MANUAL, SKIPPED
        self.problem_lines = []   # shown in the popup at the end
        self.unmatched = {}       # questions on the form that are not in data.json
        self.error_titles = []    # titles of questions that were found but could not be filled
        self.option_miss = {}     # (question, section) -> why its value was not on the pages visited
        self.sections_seen = []   # section titles of the pages visited (normalised)
        self.pickers_used = 0     # Google file pickers opened so far (the first one is slower)
        self.final_answers = {}   # question -> what is on the form now (collected from every page)
        self.blocked = False      # the form would not go past a page (a required question is empty)
        self.submitted = False
        self.proc = None          # the browser process we started
        self.all_fields = []
        self.ticked_titles = []   # tick boxes answered on the page being filled

    # --- reporting
    def log(self, text):
        self.emit("log", text)

    def status(self, text):
        self.emit("status", text)

    def record(self, status, question, detail=""):
        self.results.append(status)
        line = f"[{status}] {question}"
        if detail:
            line += f" - {detail}"
        self.log(line)
        if status in ("ERROR", "NOT FOUND", "BLOCKED"):
            self.problem_lines.append(f"{status}: {question}" + (f" ({detail})" if detail else ""))

    # --- main flow
    def run(self):
        from playwright.sync_api import sync_playwright

        self.all_fields = prepare_fields(self.data)  # {first_name} etc. replaced from "profile"
        remaining = list(self.all_fields)

        with sync_playwright() as p:
            context = self._open_browser(p)
            # always a new tab: if the browser was already open (e.g. a form you are reviewing),
            # that tab is left untouched
            page = context.new_page()
            self.status("Opening the form...")
            self.log(f"Opening: {self.url}")
            if self.wait_open:
                if not self._wait_until_open(page):
                    self.log("Stopped by you while waiting for the form to open. Nothing was filled.")
                    self.status("Stopped. Nothing was filled.")
                    if not self.keep_open:
                        self._close_browser()
                    return
            else:
                page.goto(self.url, wait_until="domcontentloaded", timeout=60000)

            if not self._wait_for_form(page):
                raise RuntimeError(
                    "The form did not load. Check the form link, or if the browser asks you to sign in, "
                    "sign in in the browser window and wait."
                )
            self._settle(page)

            # Walk every page of the form (so new questions are seen everywhere), then stop.
            for page_no in range(1, MAX_PAGES + 1):
                if self.stop.is_set():
                    break
                section = page.evaluate(SECTION_JS)
                self.sections_seen.append(norm(section))
                self.log(f"--- Page {page_no}{f' ({section})' if section else ''}: "
                         f"{len(remaining)} answer(s) still to place ---")
                remaining, used = self._fill_page(page, remaining)
                self._scan_page(page, used)

                moved = self._go_next(page)
                if moved == "last":
                    break
                if moved != "moved":
                    self.blocked = True
                    detail = ("unanswered required question(s): " + "; ".join(moved)) if moved else \
                        f"the form did not go to the next page within {NEXT_WAIT_S} s"
                    self.record("BLOCKED", f"page {page_no}", detail)
                    break

            for field in remaining:
                question = str(field.get("question", ""))
                section = norm(field.get("section") or "")
                key = (question, field.get("section") or "")
                if key in self.option_miss:
                    self.record("NOT FOUND", question, self.option_miss[key] +
                                " (this answer is not among the options of any page visited)")
                elif field.get("optional"):
                    self.record("SKIPPED", question, "optional, not on this form")
                elif section and not self.blocked and not any(section in s for s in self.sections_seen):
                    self.record("SKIPPED", question, f'the form did not show a "{field.get("section")}" '
                                                     "section (another choice was made)")
                elif self.blocked:
                    self.record("NOT FOUND", question, "not reached: the form is stuck on an earlier page")
                else:
                    self.record("NOT FOUND", question, "no question on the form matches it "
                                "(add the form's wording to its \"aliases\")")
            for title in self.error_titles:
                self._highlight(page, title)

            problems = [r for r in self.results if r in ("ERROR", "NOT FOUND", "NEW", "BLOCKED")]
            if self.stop.is_set():
                self.log("Stopped by you. Nothing was submitted.")
            elif self.submit and not problems:
                self.status("Sending the form...")
                if self._click_button(page, SUBMIT_WORDS):
                    page.wait_for_timeout(3000)
                    body = page.inner_text("body").lower()
                    if any(w in body for w in ("enregistr", "recorded", "submitted", "soumis")):
                        self.submitted = True
                        self.log("FORM SENT - confirmation shown.")
                    else:
                        self.log("Submit clicked, but no confirmation was seen. Check the browser window.")
                else:
                    self.log("Could not find the Submit button. Send the form yourself.")
            elif self.submit and problems:
                self.log("NOT submitted: some answers need your attention (see the popup). "
                         "Fix them in the browser or in data.json, then run again.")
            else:
                self.log("NOT submitted (the 'submit' option is off). Check the form, then press Submit yourself.")

            self._summary()
            if problems:
                self.emit("popup", "The form is filled, but these need your attention:\n\n"
                          + "\n".join("- " + line for line in self.problem_lines[:25])
                          + ("\n\nNew questions were added to the form. Copy them into data.json "
                             "(see the log for ready-to-paste lines)." if self.unmatched else ""))
            if self.keep_open:
                self.status("Done. Review the form in the browser. Close the browser window when you are finished.")
                self._wait_until_browser_closed(page)
            else:
                self._close_browser()

    def _summary(self):
        names = ("OK", "ERROR", "NOT FOUND", "NEW", "BLOCKED", "MANUAL", "SKIPPED")
        count = {s: self.results.count(s) for s in names}
        self.log(
            f"SUMMARY: {count['OK']} filled, {count['ERROR']} error(s), {count['NOT FOUND']} not found, "
            f"{count['NEW']} new question(s) on the form, {count['BLOCKED']} blocked, "
            f"{count['MANUAL']} file(s) to upload by hand, {count['SKIPPED']} optional skipped."
        )

    # --- one page
    def _cards(self, page):
        """The questions shown on this page: title, kind, options, kind of box, required, and the
        title of the page's section (used by fields with "section")."""
        self._tag(page)
        cards = page.locator(LISTITEM).evaluate_all(SNAPSHOT_JS)
        section = page.evaluate(SECTION_JS)
        for card in cards:
            card["title"] = clean_title(card["title"])
            card["options"] = [o for o in card["options"] if norm(o) not in PLACEHOLDER_OPTIONS]
            card["section"] = section
        return cards

    def _settle(self, page):
        """Wait until the answers shown on the page stop changing. Google keeps a draft of the
        form and puts its answers back just after a page appears; clicking during that moment
        gets undone (seen on a real form: a ticked box and a radio button came back)."""
        last, quiet, waited = None, 0, 0
        while waited < SETTLE_MAX_MS:
            try:
                self._tag(page)
                now = json.dumps(page.locator(LISTITEM).evaluate_all(FINAL_JS), ensure_ascii=False)
            except Exception:
                now = None
            if now is not None and now == last:
                quiet += 200
                if quiet >= SETTLE_QUIET_MS:
                    return
            else:
                last, quiet = now, 0
            page.wait_for_timeout(200)
            waited += 200

    def _go_next(self, page):
        """Click 'Suivant' and wait until the next page is really there.
        Returns "last" (no Suivant: last page), "moved", or a list of the unanswered required
        questions when the form refuses to move on. Pages that depend on an answer (e.g. SCOPE or
        SCORE) can take a few seconds, so this waits for the change instead of a fixed time."""
        signature = self._signature(page)
        if not self._click_button(page, NEXT_WORDS):
            return "last"
        deadline = time.time() + NEXT_WAIT_S
        while time.time() < deadline:
            page.wait_for_timeout(300)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
                if self._signature(page) != signature:
                    self._wait_for_form(page, timeout=5000)  # the last page may have no question at all
                    self._settle(page)
                    return "moved"
                missing = page.locator(LISTITEM).evaluate_all(MISSING_JS)
                if missing:
                    return missing
            except Exception:
                continue  # the page is being replaced right now: look again
        return []

    def _fill_page(self, page, remaining):
        """Answer what can be answered on this page.
        Fields are matched in rounds, from the surest way to the least sure (see TIERS), and a
        question answered in one round cannot be taken by a later, weaker match.
        Returns (fields still to place on later pages, indexes of the questions answered)."""
        pending, deferred, used = list(remaining), [], set()
        self.ticked_titles = []  # tick boxes answered on this page, checked again at the end
        for tier in TIERS:
            if not pending or self.stop.is_set():
                break
            cards = self._cards(page)
            next_round = []
            for field in pending:
                if self.stop.is_set():
                    next_round.append(field)
                    continue
                question = str(field.get("question", ""))
                index, ambiguous, detail = find_card(cards, field, tier, used, self.aliases)
                if index is None:
                    if ambiguous and tier in ("exact", "partial"):
                        self.record("ERROR", question, "several questions match. Make the question text more "
                                    "precise. Found: " + " | ".join(ambiguous[:4]))
                        continue
                    next_round.append(field)
                    continue
                outcome = self._answer(page, field, index, cards[index], tier, detail)
                if outcome == "notfound":
                    deferred.append(field)  # its answer is not an option here: try it on later pages
                else:
                    used.add(index)
                page.wait_for_timeout(self.pause_ms)
            pending = next_round
        used |= self._auto_tick(page, used)
        self._recheck_ticks(page, self.ticked_titles)
        return pending + deferred, used

    def _container(self, page, index, title):
        """The question card, checked by its title (in case the page re-drew itself)."""
        self._tag(page)
        cards = page.locator(LISTITEM)
        titles = [clean_title(t) for t in cards.evaluate_all(TITLES_JS)]
        if index < len(titles) and titles[index] == title:
            return cards.nth(index)
        if title in titles:
            return cards.nth(titles.index(title))
        raise RuntimeError(f'the question "{title}" is no longer on the page')

    def _answer(self, page, field, index, card, tier, detail):
        """Put the answer of `field` into the question `card`. Returns ok / error / notfound."""
        question = str(field.get("question", "")).strip()
        ftype = field_type(field)
        value = field.get("value")
        click_before = bool(field.get("click_before", False))
        self.status(f"Filling: {question}")
        found = TIER_TEXT.get(tier, "") + (f", {detail}" if detail else "")
        if found:
            self.log(f'      "{question}" {found}: "{card["title"]}"')
        try:
            container = self._container(page, index, card["title"])
        except Exception as err:
            self.record("ERROR", question, short(err))
            return "error"
        # The answer type is decided by what the form really shows, not by the "type" in the file,
        # so a question that changed from radio buttons to a dropdown (or similar) still works.
        kind = container.evaluate(KIND_JS)
        if ftype == "file" or kind == "file":
            return self._handle_file(page, container, question, ftype, value, card["title"])
        last_error = None
        for _attempt in range(2):
            try:
                container.scroll_into_view_if_needed()
                notes = []
                if ftype == "tick":
                    notes = [self._do_tick(page, container)]
                    self.ticked_titles.append(card["title"])
                elif kind == "text":
                    self._do_text(container, as_list(value)[0], click_before)
                elif kind in ("choice", "multi"):
                    notes = self._do_choice(container, value, ftype == "multi", click_before)
                elif kind == "dropdown":
                    notes = [self._do_dropdown(page, container, value, click_before)]
                self.option_miss.pop((question, field.get("section") or ""), None)
                self.record("OK", question, "; ".join(n for n in notes if n))
                return "ok"
            except OptionMissing as err:
                self.option_miss[(question, field.get("section") or "")] = short(err)
                return "notfound"
            except Exception as err:
                last_error = err
                page.wait_for_timeout(700)
        self.record("ERROR", question, short(last_error))
        self.error_titles.append(card["title"])
        return "error"

    def _scan_page(self, page, used):
        """Record what is on this page: answers so far, and questions data.json does not cover."""
        self._tag(page)
        for item in page.locator(LISTITEM).evaluate_all(FINAL_JS):
            if item["title"]:
                self.final_answers[clean_title(item["title"])] = item["value"]
        cards = self._cards(page)
        claimed = set(used) | title_claims(cards, self.all_fields, self.aliases)
        for index, card in enumerate(cards):
            title = card["title"]
            if not title or index in claimed or title in self.unmatched:
                continue
            self.unmatched[title] = card
            self.results.append("NEW")
            self.problem_lines.append(f"NEW on the form, not in data.json: {title}")
            self.log(f'[NEW] "{title}" is on the form but not in data.json.')
            if card["options"]:
                self.log(f"      options: {', '.join(card['options'][:20])}")
            self.log("      add this to data.json (fill in the value): " + snippet_for(card))

    def _highlight(self, page, title):
        try:
            index, _ = self._find_question(page, title)
            if index is not None:
                page.locator(LISTITEM).nth(index).evaluate(
                    "el => { el.style.outline = '3px solid #e53935'; el.style.outlineOffset = '6px'; "
                    "el.scrollIntoView({block: 'center'}); }")
        except Exception:
            pass

    # --- browser
    def _open_browser(self, p):
        """Start Brave (or Chrome/Edge) as a normal browser with a debug port, then connect to it.
        Playwright never launches the browser itself, so Google does not see automation and
        the sign-in works like normal."""
        headless = os.environ.get("AUTOFILL_HEADLESS") == "1"
        os.makedirs(BROWSER_DIR, exist_ok=True)
        found = next(((name, path) for name, path in BROWSERS if os.path.isfile(path)), None)
        if not found:
            raise RuntimeError("No Brave, Google Chrome or Microsoft Edge found on this computer. "
                               "Install Brave, then press START again.")
        name, exe = found
        version_url = f"http://127.0.0.1:{CDP_PORT}/json/version"
        if not headless:
            self._close_hidden_browser(p, version_url)
        args = [exe, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={BROWSER_DIR}",
                "--no-first-run", "--no-default-browser-check"]
        if headless:
            args.append("--headless=new")
        self.log(f"Opening {name}. If Google asks you to sign in, sign in in that window.")
        self.proc = subprocess.Popen(args)

        deadline = time.time() + 30
        while True:
            try:
                urllib.request.urlopen(version_url, timeout=2).read()
                break
            except Exception:
                if time.time() > deadline:
                    raise RuntimeError(
                        f"{name} did not start in time. Close every {name} window (check the taskbar, "
                        "including hidden ones), then press START again.")
                time.sleep(0.5)
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        return browser.contexts[0]

    def _close_hidden_browser(self, p, version_url):
        """If an INVISIBLE (headless) browser is still running on our port and profile (e.g. left
        over by a test run that was killed), START would connect to it and fill the form where
        nobody can see it. Close it, so a normal window is opened instead."""
        try:
            info = json.loads(urllib.request.urlopen(version_url, timeout=2).read())
        except Exception:
            return  # nothing running on the port: the normal case
        if "headless" not in (info.get("User-Agent", "") + info.get("Browser", "")).lower():
            return  # a visible browser from an earlier run: it is reused, as before
        self.log("An invisible browser from an earlier run was still open. Closing it first.")
        try:
            # browser.close() would only disconnect; Browser.close really ends the browser
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            browser.new_browser_cdp_session().send("Browser.close")
        except Exception:
            pass
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                urllib.request.urlopen(version_url, timeout=1).read()
            except Exception:
                time.sleep(1)  # let it release the profile before a new browser uses it
                return
            time.sleep(0.5)
        raise RuntimeError("An invisible browser from an earlier run is still open and could not be closed. "
                           "Restart the computer, then press START again.")

    def _close_browser(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _tag(self, page):
        """Mark the question cards on the page (see TAG_JS). Returns how many were found.
        Google sometimes reloads the page when moving between sections, which briefly
        destroys the page script; then wait and try again."""
        for attempt in range(6):
            try:
                return page.evaluate(TAG_JS)
            except Exception as err:
                retry_ok = "navigation" in str(err).lower() or "context" in str(err).lower()
                if attempt == 5 or not retry_ok:
                    raise
                page.wait_for_timeout(700)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
        return 0

    def _wait_for_form(self, page, timeout=120000):
        """Wait for questions to appear. Gives the user time to sign in if Google asks."""
        if "accounts.google" in page.url:
            self.status("Please sign in to Google in the browser window...")
            self.log("Google asks you to sign in. Sign in in the browser window; the app will continue.")
        waited = 0
        while waited < timeout:
            self._tag(page)
            if page.locator(LISTITEM).count() > 0:
                page.wait_for_timeout(400)
                self._tag(page)
                return True
            page.wait_for_timeout(300)
            waited += 300
        return False

    def _form_state(self, page):
        """What the loaded link shows: "open" (questions are there), "closed" (Google's 'no longer
        accepting responses' page), "signin" (Google asks to sign in) or "unknown" (anything else:
        an error page, no network...). Only "open" starts the filling, so a page we do not
        recognise can never start it by mistake."""
        deadline = time.time() + WAIT_LOAD_S
        while True:
            if "accounts.google" in page.url:
                return "signin"
            try:
                if self._tag(page) > 0 and page.locator(LISTITEM).count() > 0:
                    return "open"
                if "closedform" in page.url or CLOSED_RE.search(page.inner_text("body")):
                    return "closed"
            except Exception:
                pass  # the page is still loading
            if time.time() > deadline:
                return "unknown"
            page.wait_for_timeout(250)

    def _wait_until_open(self, page):
        """Load the form link again and again (every WAIT_MIN_S..WAIT_MAX_S s) until its questions
        appear. The original link is loaded each time, not page.reload(): a closed form may have
        moved to a '/closedform' address, and reloading that would never show the opened form.
        Returns True when the form is open, False if STOP was pressed."""
        started = time.time()
        last_state, last_heartbeat, checks = None, time.time(), 0
        while not self.stop.is_set():
            why = ""
            if "accounts.google" in page.url:
                state = "signin"  # do not reload the sign-in page: the person is signing in
            else:
                try:
                    page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
                    state = self._form_state(page)
                except Exception as err:
                    state, why = "unknown", short(err)
            checks += 1
            if state == "open":
                waited = time.time() - started
                self.log(f"THE FORM IS OPEN (after {checks} check(s), {waited / 60:.1f} min of waiting). Filling now.")
                return True
            if state != last_state:
                self.log({
                    "closed": "The form is closed (not accepting responses). Checking every "
                              f"{WAIT_MIN_S:g}-{WAIT_MAX_S:g} s; it will be filled as soon as it opens.",
                    "signin": "Google asks you to sign in. Sign in in the browser window; waiting continues.",
                    "unknown": "The form link does not show the form yet" + (f" ({why})" if why else "")
                               + ". Still checking.",
                }[state])
                last_state = state
            if time.time() - last_heartbeat >= WAIT_HEARTBEAT_S:
                self.log(f"Still waiting: {checks} check(s) so far, form {state}.")
                last_heartbeat = time.time()
            pause = random.uniform(WAIT_MIN_S, WAIT_MAX_S)
            self.status(f"Waiting for the form to open - {state} at {datetime.now():%H:%M:%S} "
                        f"(check {checks}), next check in {pause:.0f} s. Press STOP to cancel.")
            end = time.time() + pause
            while time.time() < end and not self.stop.is_set():
                time.sleep(0.2)
        return False

    def _signature(self, page):
        # Titles, options and the full text of each question (dropdown options are hidden
        # until opened, but they are in the text). Two pages can share question titles.
        self._tag(page)
        snapshot = page.locator(LISTITEM).evaluate_all(SNAPSHOT_JS)
        texts = page.locator(LISTITEM).evaluate_all(TEXT_JS)
        return json.dumps([snapshot, texts], sort_keys=True, ensure_ascii=False)

    def _find_question(self, page, question):
        self._tag(page)
        titles = page.locator(LISTITEM).evaluate_all(TITLES_JS)
        return pick_index([clean_title(t) for t in titles], question)

    # --- documents
    def _attached(self, container):
        """Names of the files already attached to a question (Google lists them as buttons in a list)."""
        return container.evaluate(ATTACHED_JS)

    @staticmethod
    def _same_file(shown, name):
        """Is the attached file `shown` our file `name`? Google may add " - First Last" after the
        name, so accept that, but 'upload_check2.pdf' must not count as 'upload_check.pdf'."""
        shown, name = shown.strip().lower(), name.strip().lower()
        if shown == name:
            return True
        stem = os.path.splitext(name)[0]
        rest = shown[len(stem):] if stem and shown.startswith(stem) else None
        return rest is not None and (rest == "" or rest.startswith(".") or rest.startswith(" -"))

    def _do_upload(self, page, container, path):
        """Attach a document to a file question.
        - same file already attached (Google keeps drafts): nothing to do
        - another file attached: remove it first (the question accepts 1 file)
        - then: into the question's file input if it has one, otherwise through the
          'Ajouter un fichier' button and Google's picker.
        The picker is always closed afterwards so it cannot cover the rest of the form."""
        if not os.path.isfile(path):
            raise RuntimeError(f"file not found: {path}")
        name = os.path.basename(path)
        attached = self._attached(container)
        if any(self._same_file(a, name) for a in attached):
            return f"{name} (was already attached)"
        if attached:
            self._remove_attached(page, container)
        try:
            inputs = container.locator('input[type="file"]')
            used_picker = inputs.count() == 0
            if not used_picker:
                inputs.first.set_input_files(path)
            else:
                add = container.locator('[role="button"]:visible, button:visible').filter(
                    has_text=re.compile(r"ajouter|add|importer|upload", re.I))
                if add.count() == 0:
                    raise RuntimeError("no visible 'Ajouter un fichier' button on this question")
                add.first.click()  # opens Google's "Insérer un fichier" picker
                if not self._set_picker_file(page, path):
                    raise RuntimeError("the file picker did not accept the file")
            retried = False
            for tick in range(240):  # up to 2 minutes for the upload
                if any(self._same_file(a, name) for a in self._attached(container)):
                    return name
                # the picker went back to 'Parcourir' without attaching: it was used too early.
                # Nothing is attached, so trying once more (more slowly) cannot add a duplicate.
                if used_picker and not retried and tick >= 6 and self._picker_reset(page):
                    retried = True
                    self.log(f"      the file picker reset; trying {name} again more slowly")
                    if not self._set_picker_file(page, path, settle=PICKER_RETRY_SETTLE_S):
                        raise RuntimeError("the file picker did not accept the file")
                page.wait_for_timeout(500)
            raise RuntimeError(f"the upload of {name} was not confirmed on the form")
        finally:
            self._close_picker(page)

    def _remove_attached(self, page, container):
        """Remove files already attached to the question (e.g. from an earlier attempt)."""
        for _ in range(5):
            if not self._attached(container):
                return
            buttons = container.locator('[role="button"]:visible, button:visible')
            labels = buttons.evaluate_all("els => els.map(e => e.getAttribute('aria-label') || '')")
            remove = [i for i, label in enumerate(labels) if re.search(r"supprimer|remove|delete|retirer", label, re.I)]
            if not remove:
                raise RuntimeError("another file is attached and could not be removed: " +
                                   ", ".join(self._attached(container)))
            buttons.nth(remove[0]).click()
            page.wait_for_timeout(800)
        if self._attached(container):
            raise RuntimeError("could not remove the file already attached")

    def _close_picker(self, page):
        """Close Google's picker if it is still open (it can stay open after an upload)."""
        picker = page.locator('iframe[src*="picker"]')
        for _ in range(3):
            try:
                if picker.count() == 0 or not picker.first.is_visible():
                    return
            except Exception:
                return
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            for frame in list(page.frames):
                if "picker" not in frame.url:
                    continue
                try:
                    # e.g. "Fermer le sélecteur Insérer un fichier"
                    close = frame.locator('[aria-label^="Fermer"], [aria-label^="Close"]')
                    if close.count() > 0 and close.first.is_visible():
                        close.first.click()
                except Exception:
                    pass
            page.wait_for_timeout(500)

    def _picker_reset(self, page):
        """True if an open picker shows its 'Parcourir' button again (it went back to the start)."""
        for frame in list(page.frames):  # the form itself has no 'Parcourir' button
            try:
                browse = frame.get_by_role("button", name=re.compile(r"^(Parcourir|Browse)$", re.I))
                if browse.count() > 0 and browse.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _set_picker_file(self, page, path, settle=None):
        """Give the file to Google's picker ('Insérer un fichier', inside a frame) the way a person
        does: press its 'Parcourir' button and answer the file dialog. Tested on a real form: this
        attaches the file and the picker closes by itself. Putting the file straight into the
        picker's hidden input starts the upload but then the picker resets, so that is only a
        last resort when no 'Parcourir' button shows up."""
        if settle is None:
            settle = PICKER_SETTLE_S if self.pickers_used else PICKER_FIRST_SETTLE_S
        self.pickers_used += 1
        deadline = time.time() + 40
        last_resort_after = time.time() + 25
        seen_since = None  # the picker needs a few seconds after 'Parcourir' appears to be ready
        while time.time() < deadline:
            for frame in list(page.frames):
                try:
                    browse = frame.get_by_role("button", name=re.compile(r"^(Parcourir|Browse)$", re.I))
                    if browse.count() > 0 and browse.first.is_visible():
                        seen_since = seen_since or time.time()
                        if time.time() - seen_since < settle:
                            break  # visible, but give the picker time to finish loading
                        with page.expect_file_chooser(timeout=8000) as chooser:
                            browse.first.click()
                        chooser.value.set_files(path)
                        self._confirm_picker(page)
                        return True
                except Exception:
                    continue
            if time.time() > last_resort_after:
                for frame in list(page.frames):
                    if frame == page.main_frame:
                        continue
                    try:
                        inputs = frame.locator('input[type="file"]')
                        if inputs.count() > 0:
                            inputs.first.set_input_files(path)
                            self._confirm_picker(page)
                            return True
                    except Exception:
                        continue
            page.wait_for_timeout(500)
        return False

    def _confirm_picker(self, page):
        """If the picker shows a confirm button after the file is chosen, press it."""
        page.wait_for_timeout(800)
        for frame in list(page.frames):
            try:
                button = frame.get_by_role("button", name=re.compile(
                    r"^(Sélectionner|Insérer|Select|Insert|Terminé|Done)$", re.I))
                if button.count() > 0 and button.first.is_visible():
                    button.first.click()
                    return
            except Exception:
                continue

    def _handle_file(self, page, container, question, ftype, value, title):
        """Upload a document. Never retried: a second try could upload the same file twice."""
        if ftype != "file":
            self.record("MANUAL", question, "upload this file yourself on the form")
            return "ok"
        path = str(value or "").strip()
        if not path:
            self.record("MANUAL", question, "no file chosen in data.json - upload it yourself")
            return "ok"
        try:
            container.scroll_into_view_if_needed()
            name = self._do_upload(page, container, resolve_path(path))
            self.record("OK", question, f"uploaded {name}")
            return "ok"
        except Exception as err:
            self.record("ERROR", question, short(err))
            self.error_titles.append(title)
            return "error"

    # --- the kinds of answers
    def _do_text(self, container, value, click_before):
        box = container.locator(
            'input[type="text"], input[type="email"], input[type="tel"], input[type="number"], '
            'input[type="url"], input[type="date"], input:not([type]), textarea'
        ).first
        box.wait_for(state="visible", timeout=8000)
        if click_before:
            # like a person: click into the box, clear it, type
            box.click()
            box.fill("")
            box.press_sequentially(value, delay=self.key_delay_ms)
        else:
            # direct: put the text in the box without clicking first
            box.fill(value)
        if box.input_value().strip() != value.strip():
            raise RuntimeError("the text did not stay in the box")

    def _tick_box(self, box):
        """Tick a checkbox that is not ticked. Raises if it will not tick."""
        box.evaluate("el => el.click()")
        if box.get_attribute("aria-checked") != "true":
            box.click(force=True)
        if box.get_attribute("aria-checked") != "true":
            raise RuntimeError("could not tick the box")

    def _do_tick(self, page, container):
        """Tick EVERY box of the question ('Enregistrer mon adresse e-mail', or 'Je certifie sur
        l'honneur' with its two boxes) and make sure they STAY ticked. Seeing a box ticked once is
        not enough: it may only show a default, and Google can put back a saved draft where you had
        unticked it a moment later. So the boxes are watched for TICK_HOLD_MS and ticked again
        whenever one gets unticked. Returns a note for the log."""
        boxes = container.locator('[role="checkbox"]')
        boxes.first.wait_for(state="attached", timeout=8000)
        count = boxes.count()
        ticked = 0
        for i in range(count):
            if boxes.nth(i).get_attribute("aria-checked") != "true":
                self._tick_box(boxes.nth(i))
                ticked += 1
        if count == 1:
            note = "ticked" if ticked else "was already ticked"
        else:
            note = f"{count} boxes ticked" + (f" ({count - ticked} already were)" if ticked < count else "")
        retick = 0
        steady = 0
        while steady < TICK_HOLD_MS:
            page.wait_for_timeout(200)
            undone = [i for i in range(count) if boxes.nth(i).get_attribute("aria-checked") != "true"]
            if not undone:
                steady += 200
                continue
            retick += 1
            if retick > 3:
                raise RuntimeError("a box keeps getting unticked")
            for i in undone:
                self._tick_box(boxes.nth(i))
            steady = 0
        if retick:
            note += f"; a box got unticked again and was re-ticked ({retick}x)"
        return note

    def _recheck_ticks(self, page, titles):
        """Before leaving a page: the boxes ticked on it must still be ticked."""
        for title in titles:
            try:
                boxes = self._container(page, 0, title).locator('[role="checkbox"]')
                undone = [i for i in range(boxes.count()) if boxes.nth(i).get_attribute("aria-checked") != "true"]
                for i in undone:
                    self._tick_box(boxes.nth(i))
                if undone:
                    self.log(f'      "{title}": a box had been unticked again - ticked it again')
            except Exception as err:
                self.record("ERROR", title, f"could not re-check the tick box: {short(err)}")
                self.error_titles.append(title)

    def _auto_tick(self, page, used):
        """Required confirmation boxes that data.json does not mention (see is_confirmation in
        matching.py): tick them, or the form cannot be sent. Returns the indexes answered."""
        done = set()
        if not self.auto_tick:
            return done
        cards = self._cards(page)
        claimed = set(used) | title_claims(cards, self.all_fields, self.aliases)
        for index, card in enumerate(cards):
            if index in claimed or not is_confirmation(card) or self.stop.is_set():
                continue
            title = card["title"]
            try:
                container = self._container(page, index, title)
                container.scroll_into_view_if_needed()
                note = self._do_tick(page, container)
                self.record("OK", title, f"not in data.json, but a required confirmation: {note} automatically")
                self.ticked_titles.append(title)
                done.add(index)
            except Exception as err:
                self.record("ERROR", title, f"required confirmation, could not tick it: {short(err)}")
                self.error_titles.append(title)
        return done

    def _do_choice(self, container, value, select_all, click_before):
        """Radio buttons or checkboxes. For a "multi" field every value of the list is ticked;
        otherwise the list holds acceptable answers and the first one that matches is used.
        Returns notes about options that were not an exact match."""
        options = container.locator('[role="radio"], [role="checkbox"]')
        options.first.wait_for(state="attached", timeout=8000)
        labels = options.evaluate_all(LABEL_JS)
        groups = [[v] for v in as_list(value)] if select_all else [value]
        targets = []
        for alternatives in groups:  # find everything first: nothing is clicked if one is missing
            index, how, used, ambiguous = pick_option(labels, alternatives, self.aliases)
            if index is None:
                hint = f" (several match: {', '.join(ambiguous[:4])})" if ambiguous else ""
                raise OptionMissing(f'option "{as_list(alternatives)[0]}" not found{hint}. '
                                    f'Options: {", ".join(labels[:15])}')
            targets.append((index, how, used))
        notes = []
        for index, how, used in targets:
            option = options.nth(index)
            if option.get_attribute("aria-checked") != "true":
                if click_before:
                    option.scroll_into_view_if_needed()
                    option.click()
                else:
                    option.evaluate("el => el.click()")
                if option.get_attribute("aria-checked") != "true":
                    option.click(force=True)
                if option.get_attribute("aria-checked") != "true":
                    raise RuntimeError(f'could not tick "{labels[index]}"')
            notes.append(choice_note(labels[index], how, used))
        if select_all:
            # checkboxes left ticked by a saved draft but not in your list are unticked
            wanted = {index for index, _, _ in targets}
            for i in range(len(labels)):
                option = options.nth(i)
                if i not in wanted and option.get_attribute("role") == "checkbox" \
                        and option.get_attribute("aria-checked") == "true":
                    option.click()
                    notes.append(f'unticked "{labels[i]}"')
        return notes

    def _do_dropdown(self, page, container, value, click_before):
        box = container.locator('[role="listbox"]').first
        box.wait_for(state="visible", timeout=8000)
        box.click()  # a dropdown always has to be opened by a click
        page.wait_for_timeout(300)  # the menu opens with an animation: let it finish
        if box.get_attribute("aria-expanded") == "false":  # the click did not open it: try once more
            box.click()
            page.wait_for_timeout(300)
        # only the options of THIS dropdown (each dropdown keeps its own list inside itself)
        options = box.locator('[role="option"]:visible')
        options.first.wait_for(state="visible", timeout=8000)
        labels = options.evaluate_all(LABEL_JS)
        real = [i for i, label in enumerate(labels) if norm(label) not in PLACEHOLDER_OPTIONS]
        index, how, used, ambiguous = pick_option([labels[i] for i in real], value, self.aliases)
        if index is None:
            self._close_dropdown(page, box)
            hint = f" (several match: {', '.join(ambiguous[:4])})" if ambiguous else ""
            raise OptionMissing(f'option "{as_list(value)[0]}" not found in the dropdown{hint}. '
                                f'Options: {", ".join(labels[i] for i in real[:15])}')
        index = real[index]
        option = options.nth(index)
        if click_before:
            option.click()
        else:
            option.evaluate("el => el.click()")
        page.wait_for_timeout(150)
        # check the option the dropdown really has selected (its text contains every option)
        selected = box.evaluate(SELECTED_JS)
        if selected is not None:
            if norm(selected) != norm(labels[index]):
                raise RuntimeError(f'the dropdown shows "{selected}" instead of "{labels[index]}"')
        elif norm(labels[index]) not in norm(box.inner_text()):
            raise RuntimeError("the dropdown did not change")
        return choice_note(labels[index], how, used)

    def _close_dropdown(self, page, box):
        """Make sure an open drop-down menu is closed, so it does not cover the next question."""
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        if box.get_attribute("aria-expanded") == "true":
            box.click()
            page.wait_for_timeout(100)

    def _click_button(self, page, words):
        buttons = page.locator('div[role="button"]:visible, button:visible')
        labels = buttons.evaluate_all(BUTTON_JS)
        wanted = [norm(w) for w in words]
        for i, label in enumerate(labels):
            if norm(label) in wanted:
                buttons.nth(i).click()
                return True
        return False

    def _wait_until_browser_closed(self, page):
        """Keep running until the user closes the tab or the browser window."""
        while True:
            try:
                if page.is_closed():
                    return
                page.wait_for_timeout(1000)
            except Exception:
                return


class FormChecker(Filler):
    """'Check form': read every page of the form WITHOUT filling or clicking anything, and report
    which data.json entry goes where, which option would be chosen, and what is missing.
    Google puts the whole form in the page (FB_PUBLIC_LOAD_DATA_), so no page has to be filled."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entries = []

    def run(self):
        from playwright.sync_api import sync_playwright

        fields = prepare_fields(self.data)
        with sync_playwright() as p:
            context = self._open_browser(p)
            page = context.new_page()
            try:
                self.status("Reading the form (nothing is filled)...")
                self.log(f"Checking: {self.url}")
                page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                if not self._wait_for_form(page):
                    raise RuntimeError("The form did not load. Check the link, or sign in in the browser window.")
                pages, note = self._read_structure(page)
                if note:
                    self.log(note)
                total = sum(len(cards) for cards in pages)
                self.log(f"The form has {total} question(s) on {len(pages)} page(s).")
                self.entries = check_structure(pages, fields, self.aliases, self.auto_tick)
                for status, text in self.entries:
                    self.log(f"[{status}] {text}")
                self._report()
            finally:
                if self.keep_open:
                    try:
                        page.close()
                    except Exception:
                        pass
                else:
                    self._close_browser()

    def _read_structure(self, page):
        cards_here = self._cards(page)
        fb = page.evaluate(FB_JS)
        pages = parse_fb(fb) if fb else []
        if not pages or not any(pages):
            return [cards_here], "Could not read the whole form: only the first page was checked."
        # questions Google draws itself (e.g. the 'E-mail' tick box) are not in its form data
        known = {norm(c["title"]) for cards in pages for c in cards}
        extra = [c for c in cards_here if norm(c["title"]) not in known]
        pages[0] = extra + pages[0]
        return pages, ""

    def _report(self):
        statuses = [s for s, _ in self.entries]
        count = {s: statuses.count(s) for s in ("OK", "CHECK", "PROBLEM", "NOT FOUND", "MISSING", "NEW", "SKIPPED")}
        self.log(f"CHECK SUMMARY: {count['OK']} ok, {count['CHECK']} to look at, {count['PROBLEM']} problem(s), "
                 f"{count['NOT FOUND']} not found, {count['MISSING']} required question(s) missing, "
                 f"{count['NEW']} optional new, {count['SKIPPED']} optional skipped.")
        serious = [(s, t) for s, t in self.entries if s in ("PROBLEM", "NOT FOUND", "MISSING")]
        look = [(s, t) for s, t in self.entries if s == "CHECK"]
        if not serious and not look:
            message = "Everything in data.json matches the form. You can press START."
        else:
            parts = []
            if serious:
                parts.append("Fix these in data.json before START:\n" +
                             "\n".join(f"- {s}: {t[:160]}" for s, t in serious[:15]))
            if look:
                parts.append("Look at these (they were matched, but not word for word):\n" +
                             "\n".join(f"- {t[:160]}" for _, t in look[:15]))
            message = "\n\n".join(parts) + "\n\nThe full list is in the log."
        self.emit("info", message)
        self.status("Check finished. Nothing was filled or sent.")


# ---------------------------------------------------------------- window
def big_button(parent, text, color, size, padx, command, **options):
    """A big coloured button. On a Mac the system draws buttons itself and ignores the background
    colour (white text would then be invisible), so there it keeps the normal colours."""
    colors = {} if IS_MAC else {"bg": color, "fg": "white"}
    return tk.Button(parent, text=text, font=(UI_FONT, size + FONT_GROW, "bold"), padx=padx, pady=6,
                     command=command, **colors, **options)


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.worker = None
        self.stop = threading.Event()
        self.data_path = tk.StringVar(value=DEFAULT_DATA)
        self.url_var = tk.StringVar()
        self.submit_var = tk.BooleanVar(value=False)
        self.wait_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready. Press CHECK FORM to see how your answers fit the form, "
                                             "then START FILLING.")

        root.title("Form Auto-Filler")
        root.geometry("820x700")
        root.minsize(700, 560)
        root.option_add("*Font", (UI_FONT, 11 + FONT_GROW))

        frame = tk.Frame(root, padx=16, pady=12)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="1. Your answers file", font=(UI_FONT, 12 + FONT_GROW, "bold")).grid(row=0, column=0, sticky="w")
        tk.Entry(frame, textvariable=self.data_path).grid(row=1, column=0, sticky="ew", pady=(2, 0))
        row1 = tk.Frame(frame)
        row1.grid(row=2, column=0, sticky="w", pady=(4, 12))
        tk.Button(row1, text="Choose file...", command=self.choose_file).pack(side="left")
        tk.Button(row1, text="Edit answers", command=self.edit_file).pack(side="left", padx=6)
        tk.Button(row1, text="Check file", command=self.check_file).pack(side="left")
        tk.Button(row1, text="Documents to upload...", command=self.open_documents).pack(side="left", padx=6)

        tk.Label(frame, text="2. Form link", font=(UI_FONT, 12 + FONT_GROW, "bold")).grid(row=3, column=0, sticky="w")
        tk.Entry(frame, textvariable=self.url_var).grid(row=4, column=0, sticky="ew", pady=(2, 12))

        options = tk.Frame(frame)
        options.grid(row=5, column=0, sticky="w", pady=(0, 12))
        tk.Checkbutton(
            options,
            text="Send the form automatically at the end  (leave OFF to check it yourself first)",
            variable=self.submit_var,
        ).pack(anchor="w")
        tk.Checkbutton(
            options,
            text=f"Wait for the form to open, then fill it  (if the form is still closed: "
                 f"it is checked every {WAIT_MIN_S:g}-{WAIT_MAX_S:g} s)",
            variable=self.wait_var,
        ).pack(anchor="w")

        row2 = tk.Frame(frame)
        row2.grid(row=6, column=0, sticky="w")
        self.check_btn = big_button(row2, "CHECK FORM", "#1565c0", 12, 14, self.check_form)
        self.check_btn.pack(side="left")
        self.start_btn = big_button(row2, "START FILLING", "#2e7d32", 12, 18, self.start)
        self.start_btn.pack(side="left", padx=10)
        self.stop_btn = big_button(row2, "STOP", "#c62828", 11, 14, self.request_stop, state="disabled")
        self.stop_btn.pack(side="left")

        tk.Label(frame, textvariable=self.status_var, fg="#1565c0", wraplength=760,
                 justify="left").grid(row=7, column=0, sticky="w", pady=(12, 4))

        tk.Label(frame, text="Log", font=(UI_FONT, 11 + FONT_GROW, "bold")).grid(row=8, column=0, sticky="w")
        self.log_box = scrolledtext.ScrolledText(frame, height=16, state="disabled", wrap="word",
                                                 font=(MONO_FONT, 10 + FONT_GROW))
        self.log_box.grid(row=9, column=0, sticky="nsew")
        tk.Button(frame, text="Clear log", command=self.clear_log).grid(row=10, column=0, sticky="e", pady=(6, 0))

        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(9, weight=1)

        self.load_url_from_file()
        self.root.after(150, self.poll)

    # --- file handling
    def load_url_from_file(self):
        try:
            data = load_data(self.data_path.get())
            self.url_var.set(str(data.get("form_url", "")))
            self.submit_var.set(bool(data.get("submit", False)))
        except Exception:
            pass

    def choose_file(self):
        path = filedialog.askopenfilename(title="Choose your answers file",
                                          filetypes=[("JSON file", "*.json"), ("All files", "*.*")])
        if path:
            self.data_path.set(path)
            self.load_url_from_file()

    def edit_file(self):
        path = self.data_path.get()
        if not os.path.exists(path):
            messagebox.showerror("File not found", f"This file does not exist:\n{path}")
            return
        if IS_MAC:
            subprocess.Popen(["open", "-e", path])  # opens in TextEdit
        else:
            os.startfile(path)  # opens in Notepad

    def open_documents(self):
        """A small window: one row per document, with its current file and a 'Choose file' button."""
        try:
            data = load_data(self.data_path.get())
        except Exception as err:
            messagebox.showerror("Problem with the file", f"The answers file could not be read:\n\n{short(err)}")
            return
        win = tk.Toplevel(self.root)
        win.title("Documents to upload")
        win.transient(self.root)
        tk.Label(win, text="Choose the file for each document. It is saved in your answers file.",
                 fg="#1565c0").grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 6))
        rows = [(i, f) for i, f in enumerate(data.get("fields", [])) if str(f.get("type", "")).lower() == "file"]
        if not rows:
            tk.Label(win, text="There are no documents in the answers file.").grid(row=1, column=0, padx=12, pady=8)
        for row, (index, field) in enumerate(rows, start=1):
            tk.Label(win, text=str(field.get("question", "")), wraplength=380, justify="left").grid(
                row=row, column=0, sticky="w", padx=12, pady=6)
            path = str(field.get("value") or "").strip()
            ok = bool(path) and os.path.isfile(resolve_path(path))
            status = tk.Label(win, text=os.path.basename(path) if ok else "NOT CHOSEN" if not path else
                              "FILE NOT FOUND", fg="#2e7d32" if ok else "#c62828", wraplength=220, justify="left")
            status.grid(row=row, column=1, sticky="w", padx=8)
            tk.Button(win, text="Choose file...",
                      command=lambda idx=index, lbl=status: self._choose_document(idx, lbl)).grid(
                row=row, column=2, padx=12)
        tk.Button(win, text="Close", command=win.destroy).grid(row=len(rows) + 1, column=2, pady=10, padx=12)

    def _choose_document(self, index, status_label):
        path = filedialog.askopenfilename(
            title="Choose the document", filetypes=[("Documents", "*.pdf *.docx *.doc *.jpg *.jpeg *.png"),
                                                     ("All files", "*.*")])
        if not path:
            return
        try:
            data = load_data(self.data_path.get())
            data["fields"][index]["value"] = path
            with open(self.data_path.get(), "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        except Exception as err:
            messagebox.showerror("Could not save", f"The answers file could not be saved:\n\n{short(err)}")
            return
        status_label.configure(text=os.path.basename(path), fg="#2e7d32")

    def check_file(self, show_ok=True):
        try:
            data = load_data(self.data_path.get())
        except Exception as err:
            messagebox.showerror("Problem with the file",
                                 f"The answers file could not be read:\n\n{short(err)}")
            return None
        problems = validate(data, self.url_var.get().strip())
        if problems:
            messagebox.showerror("Problems in the answers file", "\n".join(f"- {p}" for p in problems))
            return None
        if show_ok:
            messagebox.showinfo("File OK", f"The answers file is fine. {len(data['fields'])} field(s) found.")
        return data

    # --- running
    def _ready(self):
        """data.json and the link, checked; None if something is wrong (a message was shown)."""
        if self.worker and self.worker.is_alive():
            return None
        data = self.check_file(show_ok=False)
        if data is None:
            return None
        url = self.url_var.get().strip()
        if not url.startswith("http"):
            messagebox.showerror("Form link", "Paste the form link (it must start with https://).")
            return None
        return data, url

    def start(self):
        ready = self._ready()
        if ready is None:
            return
        data, url = ready
        submit = self.submit_var.get()
        if submit and not messagebox.askyesno(
                "Send the form?",
                "The form WILL be sent automatically when everything is filled.\n\nContinue?"):
            return
        wait = self.wait_var.get()
        self._launch(Filler, data, url, submit, f"Started {datetime.now():%Y-%m-%d %H:%M}"
                     + (" - waiting for the form to open" if wait else ""), wait_open=wait)

    def check_form(self):
        ready = self._ready()
        if ready is None:
            return
        data, url = ready
        self._launch(FormChecker, data, url, False, f"Form check {datetime.now():%Y-%m-%d %H:%M} "
                                                    "(nothing is filled or sent)")

    def _launch(self, runner, data, url, submit, first_line, **options):
        self.stop.clear()
        self.set_running(True)
        self.clear_log()
        self.append(first_line)
        self.worker = threading.Thread(target=self._work, args=(runner, data, url, submit, options), daemon=True)
        self.worker.start()

    def _work(self, runner, data, url, submit, options):
        try:
            runner(data, url, submit, lambda kind, text: self.queue.put((kind, text)), self.stop, **options).run()
        except Exception as err:
            self.queue.put(("log", f"STOPPED WITH AN ERROR: {short(err)}"))
            self.queue.put(("status", "Something went wrong. See the log above."))
        finally:
            self.queue.put(("done", ""))

    def request_stop(self):
        self.stop.set()
        self.status_var.set("Stopping...")

    def set_running(self, running):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.check_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def poll(self):
        try:
            while True:
                kind, text = self.queue.get_nowait()
                if kind == "log":
                    self.append(text)
                elif kind == "status":
                    self.status_var.set(text)
                elif kind == "popup":
                    messagebox.showwarning("Needs your attention", text)
                elif kind == "info":
                    messagebox.showinfo("Form check", text)
                elif kind == "done":
                    self.set_running(False)
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    # --- log box
    def append(self, text):
        line = f"{datetime.now():%H:%M:%S}  {text}"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        try:
            open(LOG_FILE, "w", encoding="utf-8").close()
        except OSError:
            pass


if __name__ == "__main__":
    tk_root = tk.Tk()
    App(tk_root)
    if IS_MAC:  # started from Terminal, the window would open behind it
        tk_root.lift()
        tk_root.attributes("-topmost", True)
        tk_root.after(800, lambda: tk_root.attributes("-topmost", False))
        tk_root.focus_force()
    tk_root.mainloop()

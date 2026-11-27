FORM AUTO-FILLER - HOW TO USE
=============================

ONE TIME ONLY
  1. Double-click setup.bat and wait until it says "Setup finished".
  2. Double-click start.bat, press CHECK FORM once and sign in to Google in the
     browser window that opens. The app remembers you after that.

ON THE DAY (the form just opened)
  1. Double-click start.bat and paste the form link in "2. Form link".
  2. Press CHECK FORM. It reads every page of the form WITHOUT filling anything
     and tells you, for each of your answers, which question and option it will
     use. Fix what it reports in data.json ("Edit answers"), save, check again.
  3. Press START FILLING.
  4. Check the form in the browser and press "Envoyer" yourself, unless you
     ticked "Send the form automatically".

BEFORE IT OPENS (the form is still closed)
  1. Start the app early, paste the link, tick "Wait for the form to open,
     then fill it" and press START FILLING.
  2. The app loads the link every 3-5 seconds. The status line shows the last
     check. When the questions appear it fills the form at once.
  3. Leave the PC on and awake (no sleep mode). STOP cancels the waiting.
  Note: CHECK FORM cannot read a closed form. Check your answers on a test
  form beforehand.

THE ANSWERS FILE (data.json)
  "profile": your details, written once:
      "profile": { "last_name": "...", "first_name": "...", "phone": "...", "email": "..." }
    In any answer, {first_name}, {last_name}... are replaced by these.

  "fields": one entry per question:
    "question"  the question as written on the form
    "aliases"   other wordings of the same question, e.g. ["Nom et prénom", "Prénom et nom"]
    "keywords"  words that must all be in the question, e.g. ["attestation", "honneur"]
    "type"      text, choice (radio buttons), dropdown, multi (several checkboxes),
                file (a document to upload), tick (a single box to tick)
    "value"     your answer. For choice/dropdown it can be a list of acceptable
                answers, the first one found on the form is used:
                ["Marrakech - AEMM", "Marrakech", "AEM-M"]
                Numbers are understood: "5" picks "5ème année Médecine Générale",
                "1" or "première" picks "1ère année ...".
                For multi it is the list of boxes to tick: ["A", "B"].
                For file it is the path of the document ("Documents to upload..." sets it).
    "optional"  true = fill it if the form has it, otherwise just skip it.
                Use it to list several ways a form may ask the same thing, e.g. both
                "Nom" + "Prénom" and "Nom complet": whichever is on the form is filled.
    "section"   only for questions asked once per programme, e.g. the 5 destinations:
                "section": "SCOPE" answers them only on the page titled "... (SCOPE)",
                "section": "SCORE" only on the SCORE page. Keep one set per programme;
                the set of the programme you did not choose is skipped automatically.
    "click_before"  true = click into the box first, then type (slower).

  "option_aliases": other names of an option, e.g. "CZ": ["Tchéquie", "Czech Republic"].

HOW A QUESTION IS FOUND (surest first)
  1. same wording (case, accents, small words like "le/de/votre" and number
     spellings "1er/premier" do not matter), an alias, or all its keywords
  2. your question text is part of the form's question
  3. its answer: the only question offering your option (e.g. "FMPM")
  4. the kind of box: an e-mail or phone box for an e-mail or phone answer
  5. a very similar wording (only when it is clearly the best one)
  Nothing is guessed when two questions fit equally: it is reported instead.
  Anything found by 2-5, or an option that was not word for word, is written in
  the log ("chose ... for ...") and shown as CHECK by CHECK FORM.

WHAT THE LOG SAYS
  OK         filled
  ERROR      found but could not be filled (see the reason)
  NOT FOUND  no question matches it: add the form's wording to its "aliases"
  NEW        a question on the form that data.json does not answer
  BLOCKED    the form would not go to the next page (a required question is empty)
  MANUAL     a document to upload yourself (no file chosen)
  SKIPPED    an "optional" entry that is not on this form (normal)
  CHECK FORM also says: CHECK (look at it), PROBLEM (your answer is not an option),
  MISSING (a required question data.json does not answer - add the suggested line).

TESTS (for whoever maintains the app)
  run_tests.bat runs all tests (about 2 minutes).
  tests\google_forms\create_test_forms.gs can create test forms in your Google Drive.

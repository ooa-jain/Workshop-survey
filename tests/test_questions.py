"""
The question registry must match the forms.

app/questions.py names every question so admin can show what a student
answered. That only stays true if it tracks the templates, so this walks the
three form templates and fails when a field exists in one and not the other
-- which is what catches a reworded or renamed question at the next test run
rather than as a blank column in admin months later.

Run: python3 tests/test_questions.py   (from the project root)
"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import questions

TEMPLATES = {
    "pre": "app/templates/pre.html",
    "post_sameday": "app/templates/post_sameday.html",
    "post_week4": "app/templates/post_week4.html",
}
# The macros that render one question, and the position of the field name.
MACRO = re.compile(r'\{\{\s*(radio|likert|textq|textq_optional|checkbox_group)\(\s*"([a-z][0-9]+)"\s*,\s*"([A-Z][0-9]+)"')

failures = []


def check(label, ok):
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


for stage, path in TEMPLATES.items():
    html = open(os.path.join(os.path.dirname(__file__), "..", path)).read()
    in_form = {m.group(2): m.group(3) for m in MACRO.finditer(html)}
    # Section B is a loop over the scenario list, so it never appears literally.
    scenarios = (questions.SCENARIOS_SAMEDAY if stage == "post_sameday"
                 else questions.SCENARIOS_PRE_WEEK4)
    for sc in scenarios:
        in_form[sc["name"]] = sc["num"]

    registered = {q["key"]: q["num"] for q in questions.QUESTIONS[stage]}

    check(f"{stage}: every form field is registered",
          not (set(in_form) - set(registered)))
    check(f"{stage}: no registered field has left the form",
          not (set(registered) - set(in_form)))
    check(f"{stage}: question numbers agree",
          all(registered.get(k) == num for k, num in in_form.items() if k in registered))

    # Option values are what gets stored, so a mismatch means admin renders a
    # raw code where a label should be.
    for q in questions.QUESTIONS[stage]:
        if q["kind"] not in ("choice", "multi"):
            continue
        call = re.search(r'\{\{\s*\w+\(\s*"%s"\s*,.*?\)\s*\}\}' % q["key"], html, re.S)
        if not call:
            continue
        in_template = set(re.findall(r'\("([^"]+)"\s*,', call.group(0)))
        in_template.discard(q["key"])
        registered_values = {v for v, _ in q["options"]}
        check(f"{stage} {q['num']}: option values match the form",
              in_template == registered_values)

print("\n" + ("Registry matches the forms." if not failures else f"FAILURES: {failures}"))
sys.exit(1 if failures else 0)

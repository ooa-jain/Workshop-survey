/* ==========================================================================
   Stepper — shows one <section class="step"> at a time.

   No framework, no build step. The form itself is a plain <form> that
   still posts everything in one go, so if JS fails the page degrades to
   a long scrolling form and nothing is lost.
   ========================================================================== */

(function () {
  "use strict";

  const form = document.querySelector("form[data-stepper]");
  if (!form) return;

  const steps = Array.from(form.querySelectorAll(".step"));
  if (!steps.length) return;

  const stage = form.dataset.stage || "pre";
  const batch = form.dataset.batch || "";
  const storeKey = "jis:" + stage + ":" + batch;

  const railFill = document.querySelector(".rail-fill");
  const railCount = document.querySelector(".rail-count");
  const railDots = document.querySelector(".rail-dots");
  const wrap = document.querySelector(".wrap");

  let current = 0;
  let gateOk = true;   // flipped false when /api/check says this stage is locked

  /* ---- progress rail dots ---------------------------------------------- */
  if (railDots) {
    steps.forEach(function () {
      railDots.appendChild(document.createElement("i"));
    });
  }

  /* ---- show a step ------------------------------------------------------ */
  function show(index, skipScroll) {
    current = Math.max(0, Math.min(index, steps.length - 1));

    steps.forEach(function (s, i) {
      s.classList.toggle("active", i === current);
    });

    const accent = steps[current].dataset.accent || "violet";
    if (wrap) wrap.setAttribute("data-accent", accent);
    const rail = document.querySelector(".rail");
    if (rail) rail.setAttribute("data-accent", accent);

    const pct = ((current + 1) / steps.length) * 100;
    if (railFill) railFill.style.width = pct + "%";
    if (railCount) railCount.textContent = "Step " + (current + 1) + " / " + steps.length;
    if (railDots) {
      Array.from(railDots.children).forEach(function (dot, i) {
        dot.className = i < current ? "done" : (i === current ? "now" : "");
      });
    }

    if (!skipScroll) window.scrollTo({ top: 0, behavior: "smooth" });

    const firstField = steps[current].querySelector("input,textarea,select");
    if (firstField && window.innerWidth > 720) {
      setTimeout(function () { firstField.focus({ preventScroll: true }); }, 120);
    }
  }

  /* ---- validation ------------------------------------------------------- */
  function errorBox(step) {
    let box = step.querySelector(".step-error");
    if (!box) {
      box = document.createElement("div");
      box.className = "step-error";
      const nav = step.querySelector(".step-nav");
      (nav || step).insertAdjacentElement(nav ? "beforebegin" : "beforeend", box);
    }
    return box;
  }

  function validate(step) {
    const box = errorBox(step);
    box.classList.remove("show");

    const missing = [];
    const seenRadio = {};

    Array.from(step.querySelectorAll("[required]")).forEach(function (field) {
      if (field.type === "radio") {
        if (seenRadio[field.name]) return;
        seenRadio[field.name] = true;
        const any = step.querySelector('input[name="' + field.name + '"]:checked');
        if (!any) missing.push(field.closest(".q"));
      } else if (!field.value.trim()) {
        missing.push(field.closest(".q") || field.closest("div"));
      } else if (field.type === "email" && !/^\S+@\S+\.\S+$/.test(field.value)) {
        missing.push(field.closest(".q") || field.closest("div"));
        box.textContent = "That email doesn't look right — check it before continuing.";
        box.classList.add("show");
        return false;
      }
    });

    if (missing.length) {
      box.textContent = missing.length === 1
        ? "One question on this section still needs an answer."
        : missing.length + " questions on this section still need an answer.";
      box.classList.add("show");
      const first = missing.find(Boolean);
      if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
      return false;
    }
    return true;
  }

  /* ---- eligibility check on the identity step --------------------------- */
  const emailField = form.querySelector('input[name="email"]');
  const gateMsg = form.querySelector(".gate-msg");

  function paintGate(state, headline, detail) {
    if (!gateMsg) return;
    gateMsg.className = "gate-msg show " + (state === "locked" || state === "expired" ? "bad" : "ok");
    gateMsg.innerHTML = "<b>" + headline + "</b>" + detail;
  }

  function checkGate() {
    if (!emailField || stage === "pre") return;
    const email = emailField.value.trim();
    if (!/^\S+@\S+\.\S+$/.test(email)) return;

    fetch("/api/check?stage=" + encodeURIComponent(stage) +
          "&batch=" + encodeURIComponent(batch) +
          "&email=" + encodeURIComponent(email))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        gateOk = !!d.ok;
        if (d.state === "done") {
          paintGate("done", "Already submitted",
            "You've filled this one in. Carrying on will replace your earlier answers.");
        } else if (!d.ok) {
          paintGate(d.state, d.headline, d.detail +
            ' <a href="' + d.status_url + '" style="color:inherit">See where you are &rarr;</a>');
        } else {
          paintGate("open", "You're matched", d.detail);
        }
        const nameField = form.querySelector('input[name="name"]');
        if (d.name && nameField && !nameField.value.trim()) nameField.value = d.name;
      })
      .catch(function () { gateOk = true; });   // never block on a network blip
  }

  if (emailField && stage !== "pre") {
    emailField.addEventListener("blur", checkGate);
    if (emailField.value.trim()) checkGate();
  }


  /* ---- selected-state fallback ------------------------------------------
     Styling lives in CSS via :has(input:checked). This mirrors it onto a
     plain .selected class so browsers without :has() still show which
     option is picked. ------------------------------------------------- */
  function paintSelection(scope) {
    Array.from((scope || form).querySelectorAll("input[type=radio]")).forEach(function (input) {
      const label = input.closest("label");
      if (label) label.classList.toggle("selected", input.checked);
    });
    Array.from((scope || form).querySelectorAll("input[type=checkbox]")).forEach(function (input) {
      const label = input.closest("label");
      if (label) label.classList.toggle("selected", input.checked);
    });
  }
  form.addEventListener("change", function () { paintSelection(); });

  /* ---- autosave --------------------------------------------------------- */
  function save() {
    try {
      const data = {};
      new FormData(form).forEach(function (v, k) {
        if (data[k] === undefined) data[k] = v;
        else if (Array.isArray(data[k])) data[k].push(v);
        else data[k] = [data[k], v];
      });
      localStorage.setItem(storeKey, JSON.stringify({ t: Date.now(), data: data }));
    } catch (e) { /* private mode / quota — silently skip */ }
  }

  function restore() {
    let saved;
    try { saved = JSON.parse(localStorage.getItem(storeKey) || "null"); } catch (e) { return; }
    if (!saved || !saved.data) return;
    if (Date.now() - saved.t > 1000 * 60 * 60 * 24 * 45) {
      localStorage.removeItem(storeKey);
      return;
    }
    Object.keys(saved.data).forEach(function (k) {
      const vals = [].concat(saved.data[k]);
      vals.forEach(function (v) {
        const el = form.querySelector('[name="' + k + '"][value="' + CSS.escape(String(v)) + '"]');
        if (el) { el.checked = true; return; }
        const free = form.querySelector('[name="' + k + '"]');
        if (free && (free.tagName === "TEXTAREA" || free.type === "text" || free.type === "email")) {
          if (!free.value) free.value = v;
        }
      });
    });
    const note = document.querySelector(".autosave");
    if (note) note.textContent = "Restored your unfinished answers on this device";
  }

  form.addEventListener("change", save);
  form.addEventListener("input", function (e) {
    if (e.target.tagName === "TEXTAREA") save();
  });
  form.addEventListener("submit", function () {
    try { localStorage.removeItem(storeKey); } catch (e) {}
  });

  /* ---- navigation ------------------------------------------------------- */
  form.addEventListener("click", function (e) {
    const next = e.target.closest("[data-next]");
    const back = e.target.closest("[data-back]");
    if (next) {
      e.preventDefault();
      if (!validate(steps[current])) return;
      if (!gateOk && current === 0) {
        const box = errorBox(steps[0]);
        box.textContent = "This survey isn't open for that email yet — check the message above.";
        box.classList.add("show");
        return;
      }
      save();
      show(current + 1);
    }
    if (back) {
      e.preventDefault();
      show(current - 1);
    }
  });

  // guard the real submit too, in case someone tabs to it
  form.addEventListener("submit", function (e) {
    for (let i = 0; i < steps.length; i++) {
      if (!validate(steps[i])) {
        e.preventDefault();
        show(i);
        return;
      }
    }
    const btn = form.querySelector("[type=submit]");
    if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }
  });

  // Enter advances instead of submitting, except on the last step
  form.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || e.target.tagName === "TEXTAREA") return;
    if (current < steps.length - 1) {
      e.preventDefault();
      const btn = steps[current].querySelector("[data-next]");
      if (btn) btn.click();
    }
  });

  restore();
  paintSelection();
  show(0, true);
})();

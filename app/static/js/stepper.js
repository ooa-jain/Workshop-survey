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
  const skipPassword = form.dataset.skipPassword === "1";
  const autofill = form.dataset.autofill === "1";
  const loadTime = Date.now();

  const railFill = document.querySelector(".rail-fill");
  const railCount = document.querySelector(".rail-count");
  const railDots = document.querySelector(".rail-dots");
  const wrap = document.querySelector(".wrap");

  let current = 0;
  let gateOk = true;   // flipped false when /api/check says this stage is locked
  let hasPassword = (stage !== "pre") && !skipPassword;
  let isResetMode = false;

  const forgotLink = form.querySelector("#forgot-password-link");
  const backToPwdLink = form.querySelector("#back-to-password-link");
  const normalPwdGroup = form.querySelector(".normal-password-group");
  const resetPwdGroup = form.querySelector(".reset-password-group");
  const pwdInput = form.querySelector("#password");
  const newPwdInput = form.querySelector("#new_password");
  const confirmPwdInput = form.querySelector("#confirm_password");

  function resetMode(enabled) {
    isResetMode = enabled;
    if (enabled) {
      if (normalPwdGroup) normalPwdGroup.style.display = "none";
      if (resetPwdGroup) resetPwdGroup.style.display = "flex";
      if (pwdInput) pwdInput.removeAttribute("required");
      if (newPwdInput) newPwdInput.setAttribute("required", "");
      if (confirmPwdInput) confirmPwdInput.setAttribute("required", "");
    } else {
      if (normalPwdGroup) normalPwdGroup.style.display = "block";
      if (resetPwdGroup) resetPwdGroup.style.display = "none";
      if (pwdInput) pwdInput.setAttribute("required", "");
      if (newPwdInput) newPwdInput.removeAttribute("required");
      if (confirmPwdInput) confirmPwdInput.removeAttribute("required");
    }
  }

  if (forgotLink) {
    forgotLink.addEventListener("click", function (e) {
      e.preventDefault();
      resetMode(true);
    });
  }

  if (backToPwdLink) {
    backToPwdLink.addEventListener("click", function (e) {
      e.preventDefault();
      resetMode(false);
    });
  }

  /* ---- eye toggle: reveal/hide the password field(s) named in data-eye --- */
  Array.from(form.querySelectorAll(".pwd-eye")).forEach(function (btn) {
    btn.addEventListener("click", function () {
      const on = !btn.classList.contains("on");
      btn.classList.toggle("on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.setAttribute("aria-label", on ? "Hide password" : "Show password");
      (btn.dataset.eye || "").split(",").forEach(function (sel) {
        const el = form.querySelector(sel.trim());
        if (el) el.type = on ? "text" : "password";
      });
    });
  });

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
    if (!emailField) return;
    const email = emailField.value.trim();
    if (!/^\S+@\S+\.\S+$/.test(email)) return;

    fetch("/api/check?stage=" + encodeURIComponent(stage) +
          "&batch=" + encodeURIComponent(batch) +
          "&email=" + encodeURIComponent(email))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        gateOk = !!d.ok;
        if (!skipPassword) hasPassword = !!d.has_password;

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

        // Toggle password fields based on has_password
        const pwdLabel = form.querySelector("#password-label");
        const forgotWrapper = form.querySelector("#forgot-password-wrapper");
        if (d.has_password) {
          if (pwdLabel) pwdLabel.textContent = "Password";
          if (forgotWrapper) forgotWrapper.style.display = "block";
        } else {
          if (pwdLabel) pwdLabel.textContent = "Choose Password";
          if (forgotWrapper) forgotWrapper.style.display = "none";
          resetMode(false);
        }
      })
      .catch(function () { gateOk = true; });   // never block on a network blip
  }

  if (emailField) {
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
    const fs = form.querySelector('input[name="fill_seconds"]');
    if (fs) fs.value = Math.round((Date.now() - loadTime) / 1000);
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

      if (current === 0) {
        const email = emailField.value.trim();
        const box = errorBox(steps[0]);
        box.classList.remove("show");

        if (isResetMode) {
          const newPwd = newPwdInput.value;
          const confirmPwd = confirmPwdInput.value;
          if (newPwd !== confirmPwd) {
            box.textContent = "Passwords do not match.";
            box.classList.add("show");
            return;
          }
          if (newPwd.length < 4) {
            box.textContent = "Password must be at least 4 characters.";
            box.classList.add("show");
            return;
          }

          const btn = steps[0].querySelector("[data-next]");
          btn.disabled = true;
          const originalText = btn.textContent;
          btn.textContent = "Resetting...";

          fetch("/api/reset-password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: email, batch: batch, new_password: newPwd })
          })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            btn.disabled = false;
            btn.textContent = originalText;
            if (d.ok) {
              if (pwdInput) pwdInput.value = newPwd;
              isResetMode = false;
              hasPassword = true;
              save();
              show(current + 1);
            } else {
              box.textContent = d.detail || "Failed to reset password.";
              box.classList.add("show");
            }
          })
          .catch(function () {
            btn.disabled = false;
            btn.textContent = originalText;
            box.textContent = "Network error. Please try again.";
            box.classList.add("show");
          });
          return;
        } else if (hasPassword) {
          const pwd = pwdInput.value;
          const btn = steps[0].querySelector("[data-next]");
          btn.disabled = true;
          const originalText = btn.textContent;
          btn.textContent = "Verifying...";

          fetch("/api/verify-password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: email, batch: batch, password: pwd })
          })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            btn.disabled = false;
            btn.textContent = originalText;
            if (d.ok) {
              save();
              show(current + 1);
            } else {
              box.textContent = d.detail || "Incorrect password.";
              box.classList.add("show");
            }
          })
          .catch(function () {
            btn.disabled = false;
            btn.textContent = originalText;
            box.textContent = "Network error. Please try again.";
            box.classList.add("show");
          });
          return;
        }
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

  /* ---- auto-fill (admin "Auto-Fill All Options" / developer mode) --------
     Fills every answer field so a tester can walk the whole survey without
     hand-picking each option. The identity step (name/email/password) is
     left alone so the tester still decides who is submitting. ----------- */
  function autoFillAnswers() {
    steps.forEach(function (step, idx) {
      if (idx === 0) return;   // never touch the identity step
      const radioGroups = {};
      Array.from(step.querySelectorAll("input[type=radio]")).forEach(function (r) {
        (radioGroups[r.name] = radioGroups[r.name] || []).push(r);
      });
      Object.keys(radioGroups).forEach(function (name) {
        const group = radioGroups[name];
        group[Math.floor(Math.random() * group.length)].checked = true;
      });
      const cbSeen = {};
      Array.from(step.querySelectorAll("input[type=checkbox]")).forEach(function (c) {
        if (!cbSeen[c.name]) { c.checked = true; cbSeen[c.name] = true; }
      });
      Array.from(step.querySelectorAll("textarea")).forEach(function (t) {
        if (!t.value) t.value = "Auto-filled for testing.";
      });
    });
    paintSelection();
    save();
  }

  restore();
  paintSelection();
  if (autofill) autoFillAnswers();
  show(0, true);
})();

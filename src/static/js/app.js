// Registers the service worker (installable PWA), asks for confirmation on forms marked with
// data-confirm, locks plain forms against a double tap, keeps messages and form errors in sight,
// masks phone fields, previews a chosen photo, reports failed htmx actions, copies text from buttons marked with data-copy, shares links from buttons marked with data-share and keeps live
// screens fresh.
// Everything works without JavaScript too.
"use strict";

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  });
}

// The offline page is served at the address that failed, so its "try again" reloads that page
// instead of going to the start (without JavaScript the link still leads to the start).
document.addEventListener("click", (event) => {
  const retry = event.target instanceof Element ? event.target.closest("[data-reload]") : null;
  if (retry) {
    event.preventDefault();
    window.location.reload();
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  const question = form instanceof HTMLFormElement ? form.dataset.confirm : undefined;
  if (question && !window.confirm(question)) {
    event.preventDefault();
  }
});

// A plain form is sent once: the submit buttons go dead on the first tap (a second tap would
// repeat the action) and come back when the page is shown again, e.g. with the back button.
// htmx forms are not locked here: htmx cancels their submit and does its own request.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || event.defaultPrevented) return;
  if (form.target && form.target !== "_self") return;
  if (form.dataset.submitting) {
    event.preventDefault();
    return;
  }
  form.dataset.submitting = "true";
  // Disabled buttons are left out of the form data, so wait until the browser has read it.
  window.setTimeout(() => {
    for (const button of form.querySelectorAll("button:not([type=button]), input[type=submit]")) {
      if (!button.disabled) {
        button.disabled = true;
        button.dataset.locked = "true";
      }
    }
  }, 0);
});

window.addEventListener("pageshow", () => {
  for (const form of document.querySelectorAll("form[data-submitting]")) {
    delete form.dataset.submitting;
  }
  for (const button of document.querySelectorAll("[data-locked]")) {
    button.disabled = false;
    delete button.dataset.locked;
  }
});

// Phone fields marked with data-phone-mask show the number as (11) 98765-4321 while it is typed
// and take at most 11 digits (DDD + 9 digits); a pasted +55 is dropped. The server checks again.
const PHONE_DIGITS = 11;

function maskPhone(digits) {
  if (digits.length === 0) return "";
  if (digits.length <= 2) return `(${digits}`;
  if (digits.length <= 7) return `(${digits.slice(0, 2)}) ${digits.slice(2)}`;
  return `(${digits.slice(0, 2)}) ${digits.slice(2, 7)}-${digits.slice(7)}`;
}

document.addEventListener("input", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || !("phoneMask" in input.dataset)) return;
  let digits = input.value.replace(/\D/g, "");
  if (digits.length === PHONE_DIGITS + 2 && digits.startsWith("55")) digits = digits.slice(2);
  digits = digits.slice(0, PHONE_DIGITS);
  // Keep the cursor after the same number of digits it was after, so editing in the middle works.
  const caret = input.selectionStart ?? input.value.length;
  const digitsBeforeCaret = Math.min(input.value.slice(0, caret).replace(/\D/g, "").length, digits.length);
  const masked = maskPhone(digits);
  if (masked === input.value) return;
  input.value = masked;
  let position = 0;
  for (let seen = 0; position < masked.length && seen < digitsBeforeCaret; position++) {
    if (/\d/.test(masked[position])) seen++;
  }
  input.setSelectionRange(position, position);
});

// A photo picked in a form shows as a round thumbnail before it is sent, and can be dropped.
document.addEventListener("change", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || !("photoInput" in input.dataset)) return;
  const preview = input.form?.querySelector("[data-photo-preview]");
  if (!preview) return;
  const image = preview.querySelector("img");
  if (image.src.startsWith("blob:")) URL.revokeObjectURL(image.src);
  const file = input.files?.[0];
  if (!file || !file.type.startsWith("image/")) {
    image.removeAttribute("src");
    preview.hidden = true;
    return;
  }
  image.src = URL.createObjectURL(file);
  preview.hidden = false;
});

document.addEventListener("click", (event) => {
  const button = event.target instanceof Element ? event.target.closest("[data-photo-clear]") : null;
  if (!button) return;
  const form = button.closest("form");
  const input = form?.querySelector("[data-photo-input]");
  if (!input) return;
  input.value = "";
  input.dispatchEvent(new Event("change", { bubbles: true }));
});

// The first thing that went wrong in a form comes into view (the page reloads at the top while
// the form can be screens below), with its field around it so the fix is at hand.
function revealFirstError(root) {
  const error = root.querySelector(".form-errors, .errorlist");
  const target = error?.closest(".field") ?? error;
  target?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// On "load", not earlier: photos above the form (a receipt) still push it down until then.
window.addEventListener("load", () => revealFirstError(document));
document.addEventListener("htmx:afterSwap", (event) => revealFirstError(event.target));

// Flash messages stay in view while the screen scrolls (see .messages in the CSS). Those that
// htmx brings fade after a few seconds, errors stay until tapped; a tap dismisses any.
const MESSAGE_SECONDS = 8;
let messageTimer;

function expireMessages() {
  window.clearTimeout(messageTimer);
  messageTimer = window.setTimeout(() => {
    for (const message of document.querySelectorAll("#messages .message:not(.message--error)")) {
      message.remove();
    }
  }, MESSAGE_SECONDS * 1000);
}

document.addEventListener("click", (event) => {
  event.target.closest?.("#messages .message")?.remove();
});
document.addEventListener("htmx:afterSettle", expireMessages);

// htmx drops a failed request without a word, which looks like a button that does nothing.
function showNotice(text) {
  const box = document.getElementById("messages");
  if (!box) return;
  const item = document.createElement("li");
  item.className = "message message--error";
  item.textContent = text;
  const list = document.createElement("ul");
  list.className = "messages";
  list.append(item);
  box.replaceChildren(list);
}

document.addEventListener("htmx:responseError", (event) => {
  const status = event.detail.xhr.status;
  if (status === 401) {
    showNotice("Sua sessão expirou. Recarregue a página e tente de novo.");
  } else if (status === 403) {
    showNotice("Sua sessão expirou ou você não tem acesso a esta ação. Recarregue a página.");
  } else if (status === 404) {
    showNotice("Isso não existe mais. Recarregue a página.");
  } else {
    showNotice("O servidor teve um problema. Tente de novo.");
  }
});

for (const name of ["htmx:sendError", "htmx:timeout"]) {
  document.addEventListener(name, () => showNotice("Sem conexão, tente de novo."));
}

// The page may be open for long: a login in another tab turns the CSRF cookie over, and the
// token printed in the page (header and form field) is then refused. Send the current one.
// The cookie has another name in demo mode, so the page says which one it is: reading a fixed
// name would pick up the token of any other app on the same host.
function csrfCookie() {
  const name = document.querySelector('meta[name="csrf-cookie"]')?.content;
  if (!name) return "";
  for (const pair of document.cookie.split("; ")) {
    const [key, ...value] = pair.split("=");
    if (key === name) return decodeURIComponent(value.join("="));
  }
  return "";
}

document.addEventListener("htmx:configRequest", (event) => {
  const token = csrfCookie();
  if (!token) return;
  event.detail.headers["X-CSRFToken"] = token;
  if (event.detail.parameters.csrfmiddlewaretoken !== undefined) {
    event.detail.parameters.csrfmiddlewaretoken = token;
  }
});

// A ballot never repeats a player: once picked in one place, the others cannot pick them.
for (const form of document.querySelectorAll("form[data-distinct-selects]")) {
  const selects = Array.from(form.querySelectorAll("select"));
  const sync = () => {
    const taken = new Set(selects.map((select) => select.value).filter(Boolean));
    for (const select of selects) {
      for (const option of select.options) {
        option.disabled = option.value !== "" && taken.has(option.value) && option.value !== select.value;
      }
    }
  };
  for (const select of selects) {
    select.addEventListener("change", sync);
  }
  sync();
}

function initCopyButtons(root) {
  if (!navigator.clipboard) return;
  for (const button of root.querySelectorAll("button[data-copy]")) {
    if (button.dataset.copyReady) continue;
    button.dataset.copyReady = "true";
    button.hidden = false;
    button.addEventListener("click", () => {
      navigator.clipboard.writeText(button.dataset.copy).then(
        () => {
          const label = button.textContent;
          button.textContent = "Copiado!";
          window.setTimeout(() => {
            button.textContent = label;
          }, 2000);
        },
        () => {},
      );
    });
  }
}

// Buttons marked with data-share open the share sheet with the link; without Web Share they
// copy it. Without JavaScript they stay hidden and the link is printed under them.
function initShareButtons(root) {
  for (const button of root.querySelectorAll("button[data-share]")) {
    if (button.dataset.shareReady) continue;
    if (!navigator.share && !navigator.clipboard) continue;
    button.dataset.shareReady = "true";
    button.hidden = false;
    const label = button.textContent;
    const flash = (text) => {
      button.textContent = text;
      window.setTimeout(() => {
        button.textContent = label;
      }, 2000);
    };
    button.addEventListener("click", () => {
      const url = button.dataset.share;
      const copy = () =>
        navigator.clipboard.writeText(url).then(
          () => flash("Link copiado!"),
          () => {},
        );
      if (navigator.share) {
        navigator.share({ text: button.dataset.shareText, url }).catch((error) => {
          // A dismissed share sheet is not a failure; anything else falls back to copying.
          if (error.name !== "AbortError" && navigator.clipboard) copy();
        });
      } else {
        copy();
      }
    });
  }
}

// Links marked with data-native-share open the share sheet with the text when the device has
// one; otherwise they follow their href (a wa.me link).
function initNativeShareLinks(root) {
  if (!navigator.share) return;
  for (const link of root.querySelectorAll("a[data-native-share]")) {
    if (link.dataset.shareReady) continue;
    link.dataset.shareReady = "true";
    link.addEventListener("click", (event) => {
      event.preventDefault();
      navigator.share({ text: link.dataset.nativeShare }).catch((error) => {
        // A dismissed share sheet is not a failure; anything else falls back to the link.
        if (error.name !== "AbortError") window.open(link.href, "_blank", "noopener");
      });
    });
  }
}

// Group picker (team assembly): shows how many players are marked, shortens the save button
// to match and stops at the limit. The server checks the same limits.
function updatePicker(form) {
  const boxes = [...form.querySelectorAll('input[type="checkbox"]')];
  const max = Number(form.dataset.max);
  const min = Number(form.dataset.min);
  const checked = boxes.filter((box) => box.checked).length;
  const counter = form.querySelector("[data-picker-count]");
  const submit = form.querySelector("[data-picker-submit]");
  if (counter) counter.textContent = `${checked} de ${max}`;
  if (submit) {
    submit.textContent = checked >= min ? `Salvar grupo de ${checked}` : "Salvar grupo";
    submit.disabled = checked < min;
  }
  for (const box of boxes) {
    box.closest(".pc").classList.toggle("is-full", checked >= max && !box.checked);
    box.disabled = checked >= max && !box.checked;
  }
}

document.addEventListener("change", (event) => {
  const form = event.target.closest?.("[data-group-picker]");
  if (form) updatePicker(form);
});

function initPickers(root) {
  for (const form of root.querySelectorAll("[data-group-picker]")) updatePicker(form);
}

document.addEventListener("DOMContentLoaded", () => {
  initPickers(document);
  initCopyButtons(document);
  initShareButtons(document);
  initNativeShareLinks(document);
});
document.addEventListener("htmx:afterSwap", (event) => {
  initPickers(event.target);
  initCopyButtons(event.target);
  initShareButtons(event.target);
  initNativeShareLinks(event.target);
});

// Live screens: a block marked data-live-refresh answers the htmx "live-refresh" event by
// reloading itself. It fires every 15 s while the tab is visible and again when the tab comes
// back, unless someone is filling in a form inside the block. (htmx trigger filters would need
// eval, which the content security policy forbids.)
const LIVE_REFRESH_MS = 15000;

// A <details data-keep-open id="..."> that is open stays open across a refresh of its block.
// A <details data-keep-state id="..."> keeps whatever the person chose, open or closed, so a
// refresh never undoes a step they collapsed or a list they expanded.
let openDetails = [];
let keptStates = [];
document.addEventListener("htmx:beforeSwap", () => {
  openDetails = Array.from(document.querySelectorAll("details[data-keep-open][open]")).map((el) => el.id);
  keptStates = Array.from(document.querySelectorAll("details[data-keep-state][id]")).map((el) => [el.id, el.open]);
});
document.addEventListener("htmx:afterSwap", () => {
  for (const id of openDetails) {
    const details = document.getElementById(id);
    if (details) details.open = true;
  }
  for (const [id, open] of keptStates) {
    const details = document.getElementById(id);
    if (details) details.open = open;
  }
  openDetails = [];
  keptStates = [];
});

// Game table strip: the tapped match opens below the strip, one at a time. A refresh of the
// block keeps the match the viewer chose (the server opens the current one by default).
let chosenMatch = null;

function selectMatch(strip, matchId, { scroll = true } = {}) {
  const tabs = strip.querySelectorAll("[data-faixa-tab]");
  if (!Array.from(tabs).some((tab) => tab.dataset.faixaTab === matchId)) return;
  for (const tab of tabs) {
    const on = tab.dataset.faixaTab === matchId;
    tab.setAttribute("aria-selected", String(on));
    if (on && scroll) tab.scrollIntoView({ block: "nearest", inline: "center" });
  }
  for (const panel of strip.querySelectorAll("[data-faixa-panel]")) {
    panel.hidden = panel.dataset.faixaPanel !== matchId;
  }
}

document.addEventListener("click", (event) => {
  const trigger = event.target instanceof Element ? event.target.closest("[data-faixa-tab], [data-faixa-go]") : null;
  const strip = trigger && trigger.closest("[data-faixa]");
  if (!strip) return;
  let matchId = trigger.dataset.faixaTab || trigger.dataset.faixaGo;
  if (matchId === "prev") {
    const previous = trigger.closest("[data-faixa-panel]").previousElementSibling;
    matchId = previous && previous.dataset.faixaPanel;
  }
  if (!matchId) return;
  chosenMatch = matchId;
  selectMatch(strip, matchId);
});

function showCurrentMatch() {
  for (const strip of document.querySelectorAll("[data-faixa]")) {
    const open = strip.querySelector('[data-faixa-tab][aria-selected="true"]');
    if (chosenMatch) selectMatch(strip, chosenMatch, { scroll: false });
    const shown = strip.querySelector('[data-faixa-tab][aria-selected="true"]') || open;
    if (shown) shown.scrollIntoView({ block: "nearest", inline: "center" });
  }
}
document.addEventListener("DOMContentLoaded", showCurrentMatch);
document.addEventListener("htmx:afterSwap", showCurrentMatch);
// A folded table has no layout to scroll in: center the open match once the table opens.
document.addEventListener("toggle", (event) => {
  if (event.target instanceof HTMLDetailsElement && event.target.open) showCurrentMatch();
}, true);

function isBeingEdited(block) {
  const active = document.activeElement;
  const typing = active && block.contains(active) && active.matches("input, select, textarea");
  const hasFile = Array.from(block.querySelectorAll('input[type="file"]')).some((input) => input.files.length > 0);
  return Boolean(typing || hasFile);
}

function refreshLiveBlocks() {
  if (document.visibilityState !== "visible" || typeof htmx === "undefined") return;
  for (const block of document.querySelectorAll("[data-live-refresh]")) {
    if (!isBeingEdited(block)) htmx.trigger(block, "live-refresh");
  }
}

window.setInterval(refreshLiveBlocks, LIVE_REFRESH_MS);
document.addEventListener("visibilitychange", refreshLiveBlocks);

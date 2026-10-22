// The 7-minute match timer on the diretoria's match screen. It runs only on this phone and
// survives a page reload (the start time is kept in localStorage). Without JS it stays hidden.
"use strict";

(() => {
  const root = document.querySelector("[data-match-timer]");
  if (!root) {
    return;
  }
  const total = Number(root.dataset.seconds) * 1000;
  const key = root.dataset.key;
  const clock = root.querySelector(".timer__clock");
  const done = root.querySelector(".timer__done");
  const toggle = root.querySelector("[data-timer-toggle]");
  const reset = root.querySelector("[data-timer-reset]");

  // { startedAt: epoch ms while running, elapsed: ms counted before the last start }
  const load = () => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(key) || "null");
      if (saved && typeof saved.elapsed === "number") {
        return saved;
      }
    } catch {
      // Storage blocked or corrupt: start fresh.
    }
    return { startedAt: null, elapsed: 0 };
  };
  const save = (value) => {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Storage blocked: the timer still works until the page is reloaded.
    }
  };

  let state = load();
  let ticker = null;
  let buzzed = false;

  const elapsed = () => state.elapsed + (state.startedAt ? Date.now() - state.startedAt : 0);

  const format = (ms) => {
    const seconds = Math.ceil(Math.max(ms, 0) / 1000);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  };

  const render = () => {
    const left = total - elapsed();
    const over = left <= 0;
    clock.textContent = format(left);
    root.classList.toggle("is-over", over);
    done.hidden = !over;
    toggle.textContent = state.startedAt ? "Pausar" : elapsed() > 0 ? "Continuar" : "Iniciar";
    toggle.disabled = over;
    reset.disabled = !state.startedAt && elapsed() === 0;
    if (over && state.startedAt) {
      state = { startedAt: null, elapsed: total };
      save(state);
      stop();
      if (!buzzed && "vibrate" in navigator) {
        navigator.vibrate([400, 200, 400]);
      }
      buzzed = true;
      render();
    }
  };

  const start = () => {
    if (!ticker) {
      ticker = window.setInterval(render, 250);
    }
  };
  const stop = () => {
    window.clearInterval(ticker);
    ticker = null;
  };

  toggle.addEventListener("click", () => {
    if (state.startedAt) {
      state = { startedAt: null, elapsed: elapsed() };
      stop();
    } else {
      state = { startedAt: Date.now(), elapsed: state.elapsed };
      buzzed = false;
      start();
    }
    save(state);
    render();
  });

  reset.addEventListener("click", () => {
    if (!window.confirm("Zerar o cronômetro? O tempo volta para o início.")) {
      return;
    }
    state = { startedAt: null, elapsed: 0 };
    buzzed = false;
    stop();
    save(state);
    render();
  });

  root.hidden = false;
  if (state.startedAt) {
    start();
  }
  render();
})();

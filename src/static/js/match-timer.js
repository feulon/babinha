// The 7-minute match timer on the diretoria's match screen. It runs only on this phone and
// survives a page reload (the start time is kept in localStorage). Without JS it stays hidden.
// At zero it vibrates and blows a whistle (iPhones do not vibrate from a browser), and while it
// runs the screen is kept awake where the browser allows it.
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
  const sound = root.querySelector("[data-timer-sound]");
  // One choice per phone, shared by every match (the timer state above is per match).
  const soundKey = "baba-timer-sound";
  // [seconds from the start, length]: two short blasts and a long one.
  const blasts = [
    [0, 0.25],
    [0.4, 0.25],
    [0.8, 0.7],
  ];

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

  const loadSound = () => {
    try {
      return window.localStorage.getItem(soundKey) !== "off";
    } catch {
      return true;
    }
  };
  const saveSound = (on) => {
    try {
      window.localStorage.setItem(soundKey, on ? "on" : "off");
    } catch {
      // Storage blocked: the choice lasts until the page is reloaded.
    }
  };

  let state = load();
  let ticker = null;
  let buzzed = false;
  let soundOn = loadSound();
  let audio = null;
  let wakeLock = null;
  let wakeLockAsked = false;

  // Browsers play sound only after a tap, so this runs from the clicks on the screen. Starting
  // a silent tick inside the tap is what makes iOS Safari keep the context for later.
  const unlockAudio = () => {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context || (audio && audio.state === "running")) {
      return;
    }
    try {
      audio = audio || new Context();
      audio.resume().catch(() => {});
      const tick = audio.createBufferSource();
      tick.buffer = audio.createBuffer(1, 1, 22050);
      tick.connect(audio.destination);
      tick.start(0);
    } catch {
      audio = null;
    }
  };

  // A pea whistle: a high tone that trills, three times.
  const whistle = () => {
    if (!soundOn || !audio || audio.state !== "running") {
      return;
    }
    try {
      const begin = audio.currentTime + 0.05;
      for (const [offset, length] of blasts) {
        const from = begin + offset;
        const until = from + length;
        const tone = audio.createOscillator();
        const trill = audio.createOscillator();
        const depth = audio.createGain();
        const volume = audio.createGain();
        tone.frequency.value = 2900;
        trill.frequency.value = 38;
        depth.gain.value = 130;
        volume.gain.setValueAtTime(0.0001, from);
        volume.gain.exponentialRampToValueAtTime(0.5, from + 0.02);
        volume.gain.setValueAtTime(0.5, until - 0.04);
        volume.gain.exponentialRampToValueAtTime(0.0001, until);
        trill.connect(depth);
        depth.connect(tone.frequency);
        tone.connect(volume);
        volume.connect(audio.destination);
        tone.start(from);
        trill.start(from);
        tone.stop(until);
        trill.stop(until);
      }
    } catch {
      // No sound is better than a broken timer: the vibration and the text remain.
    }
  };

  // Keeps the screen on while the timer runs. The browser drops the lock when the page is hidden,
  // so it is asked for again when the page comes back (see visibilitychange below).
  const holdScreen = () => {
    if (
      !("wakeLock" in navigator) ||
      wakeLock ||
      wakeLockAsked ||
      document.visibilityState !== "visible"
    ) {
      return;
    }
    wakeLockAsked = true;
    navigator.wakeLock
      .request("screen")
      .then((lock) => {
        wakeLockAsked = false;
        if (!state.startedAt) {
          // Paused or ended while the browser was still answering.
          lock.release().catch(() => {});
          return;
        }
        wakeLock = lock;
        lock.addEventListener("release", () => {
          if (wakeLock === lock) {
            wakeLock = null;
          }
        });
      })
      .catch(() => {
        // Not allowed (battery saver, unsupported): the screen sleeps as usual.
        wakeLockAsked = false;
      });
  };
  const releaseScreen = () => {
    if (wakeLock) {
      wakeLock.release().catch(() => {});
      wakeLock = null;
    }
  };

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
    sound.textContent = soundOn ? "Som ligado" : "Som desligado";
    reset.disabled = !state.startedAt && elapsed() === 0;
    if (over && state.startedAt) {
      state = { startedAt: null, elapsed: total };
      save(state);
      stop();
      if (!buzzed) {
        if ("vibrate" in navigator) {
          navigator.vibrate([400, 200, 400]);
        }
        whistle();
      }
      buzzed = true;
      render();
    }
  };

  const start = () => {
    if (!ticker) {
      ticker = window.setInterval(render, 250);
    }
    holdScreen();
  };
  const stop = () => {
    window.clearInterval(ticker);
    ticker = null;
    releaseScreen();
  };

  toggle.addEventListener("click", () => {
    if (state.startedAt) {
      state = { startedAt: null, elapsed: elapsed() };
      stop();
    } else {
      state = { startedAt: Date.now(), elapsed: state.elapsed };
      buzzed = false;
      if (soundOn) {
        unlockAudio();
      }
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

  sound.addEventListener("click", () => {
    soundOn = !soundOn;
    saveSound(soundOn);
    if (soundOn) {
      unlockAudio();
    }
    render();
  });

  // After a reload with the timer running there was no tap on Iniciar: any tap unlocks the sound.
  document.addEventListener(
    "click",
    () => {
      if (soundOn && state.startedAt) {
        unlockAudio();
      }
    },
    true,
  );

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.startedAt) {
      holdScreen();
      if (audio && audio.state !== "running") {
        audio.resume().catch(() => {});
      }
    }
  });

  root.hidden = false;
  if (state.startedAt) {
    start();
  }
  render();
})();

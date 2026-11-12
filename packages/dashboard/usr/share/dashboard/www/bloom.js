// Dahlia backdrop shared by the dashboard (app.js) and the local screen (screen.js):
// weather moods on <body> and the particles in #weather-fx.

const WX_KINDS = ["clear", "partly", "cloudy", "fog", "rain", "snow", "storm"];

function span(cls) {
  const s = document.createElement("span");
  s.className = cls;
  return s;
}

// The local screen (body.screen) is drawn in software on the server's CPU:
// any continuous motion repaints the whole monitor 60 times a second and keeps
// a core busy. There everything moves in steps, twice a second, and falling
// rain and snow are left out (style.css has the rest).
const STEPS_PER_SECOND = 2;

// The backdrop follows the weather of the selected place.
function setMood(p) {
  const local = document.body.classList.contains("screen");
  const body = document.body;
  for (const k of [...WX_KINDS, "night"]) body.classList.remove(`wx-${k}`);
  const fx = document.getElementById("weather-fx");
  fx.replaceChildren();
  if (!p || p.error) return;
  body.classList.add(`wx-${p.kind}`);
  if (!p.is_day) body.classList.add("wx-night");
  const spans = (n, cls, styleOf) => {
    for (let i = 0; i < n; i++) {
      const s = span(cls);
      s.style.cssText = styleOf(i);
      fx.append(s);
    }
  };
  if (p.kind === "clear" && p.is_day) fx.append(span("sun"));
  if (["partly", "cloudy", "fog", "rain", "storm"].includes(p.kind)) {
    spans(p.kind === "partly" ? 2 : 4, "cloud", (i) =>
      `top:${6 + i * 18}%;animation-duration:${70 + i * 22}s;animation-delay:-${i * 25}s`
      + (local ? `;animation-timing-function:steps(${(70 + i * 22) * STEPS_PER_SECOND})` : ""));
  }
  if (!local && (p.kind === "rain" || p.kind === "storm")) {
    spans(70, "drop", (i) => `left:${(i * 37) % 100}%;height:${3 + (i % 4)}rem;`
      + `animation-duration:${(0.7 + (i % 5) * 0.13).toFixed(2)}s;animation-delay:-${((i * 0.29) % 1.4).toFixed(2)}s`);
  }
  if (p.kind === "storm") fx.append(span("flash"));
  if (!local && p.kind === "snow") {
    spans(55, "flake", (i) => `left:${(i * 41) % 100}%;width:${0.2 + (i % 4) * 0.1}rem;height:${0.2 + (i % 4) * 0.1}rem;`
      + `animation-duration:${(9 + (i % 6) * 1.7).toFixed(1)}s;animation-delay:-${((i * 1.3) % 14).toFixed(1)}s`);
  }
  if (!p.is_day && ["clear", "partly"].includes(p.kind)) {
    spans(45, "star", (i) => `top:${(i * 53) % 60}%;left:${(i * 29) % 100}%;animation-delay:-${i % 3}s`);
  }
}


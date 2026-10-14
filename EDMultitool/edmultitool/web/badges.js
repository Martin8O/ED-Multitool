"use strict";
// Rank emblems drawn for this tool, in the spirit of a ship's HUD: one flat, glowing emblem per rank the
// game keeps, in the rank's colour (CSS `color`), that grows as the rank rises — wings sweep out, a crest
// joins, a wreath at Elite and stars for Elite I–V. Original drawings: no game insignia are reproduced.

/* exported rankEmblem, rankCard, emblemStage, RANK_LOOK */

const EMBLEM_NS = "http://www.w3.org/2000/svg";

// How each rank looks on the page: its card label, colour (a CSS variable) and highest number.
const RANK_LOOK = {
  Combat: { label: "Combat rank", colour: "--rk-orange", top: 13 },
  Trade: { label: "Trade rank", colour: "--rk-white", top: 13 },
  Explore: { label: "Explorer rank", colour: "--rk-cyan", top: 13 },
  Soldier: { label: "Mercenary rank", colour: "--rk-orange", top: 13 },
  Exobiologist: { label: "Exobiologist rank", colour: "--rk-cyan", top: 13 },
  CQC: { label: "CQC rank", colour: "--rk-red", top: 8 },
  Federation: { label: "Federal Navy", colour: "--rk-blue", top: 14, navy: true },
  Empire: { label: "Imperial Navy", colour: "--rk-gold", top: 14, navy: true },
};


// The core of each emblem, centred on (24, 24) and kept within x 16..32 so the wings have room.
const CORES = {
  // a diving fighter under a spark
  Combat: [
    ["path", { d: "M18.5 17.5L24 21l5.5-3.5-2 10L24 35l-3.5-7.5z" }],
    ["path", { d: "M24 10.5l2.2 3.3L24 17l-2.2-3.2z" }],
  ],
  // a cut gem
  Trade: [
    ["path", { d: "M18 19.5l3-4.5h6l3 4.5L24 34z" }],
    ["path", { d: "M21 15l3 4.5 3-4.5M18 19.5h12M24 19.5V34", fill: "none", stroke: "var(--rk-cut)", "stroke-width": 1 }],
  ],
  // a crest opening upwards round a guiding star
  Explore: [
    ["path", { d: "M16.5 15.5L24 23.5l7.5-8v5.5L24 31l-7.5-10z" }],
    ["path", { d: "M24 9.5l1.4 3.6L29 14.5l-3.6 1.4L24 19.5l-1.4-3.6L19 14.5l3.6-1.4z" }],
  ],
  // a blade between its guard and pommel
  Soldier: [
    ["path", { d: "M21.5 20h5L24 35z" }],
    ["path", { d: "M17 17.5h14l-1.5 2.3h-11z" }],
    ["path", { d: "M24 10.5l2 2.8-2 2.6-2-2.6z" }],
  ],
  // a double helix inside a broken ring
  Exobiologist: [
    ["path", { d: "M24 15.5a8.5 8.5 0 0 1 8.2 6.3M32.2 26.2a8.5 8.5 0 0 1-6 6M21.8 32.2a8.5 8.5 0 0 1-6.1-6.2M15.8 21.8a8.5 8.5 0 0 1 6-6.1", fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round" }],
    ["path", { d: "M20.5 18.5c6 3 1 8 7 11M27.5 18.5c-6 3-1 8-7 11", fill: "none", stroke: "currentColor", "stroke-width": 1.6, "stroke-linecap": "round" }],
    ["path", { d: "M22 21.5h4M21.8 26.5h4.4", fill: "none", stroke: "currentColor", "stroke-width": 1.2, "stroke-linecap": "round" }],
  ],
  // a dart fighter, nose up
  CQC: [
    ["path", { d: "M24 12.5l2.3 8 5.7 5.2-5.6.8L24 33l-2.4-6.5-5.6-.8 5.7-5.2z" }],
  ],
  // a five-point star on a bar
  Federation: [
    ["path", { d: "M24 13l2.6 6 6.5.6-4.9 4.3 1.5 6.4L24 27l-5.7 3.3 1.5-6.4-4.9-4.3 6.5-.6z" }],
    ["path", { d: "M18 33.5h12l-1.2 1.8h-9.6z" }],
  ],
  // a crown
  Empire: [
    ["path", { d: "M17 31l-1.5-12 5.2 4.2L24 14l3.3 9.2 5.2-4.2L31 31z" }],
    ["path", { d: "M17 33h14l-1 1.8H18z" }],
  ],
};

// The right-hand wings, from the inner short one out to the long upper one (the left ones are mirrored).
const WINGS = [
  "M33 25l7.2-3.6.9 2.1-7.3 4.4z",
  "M33.4 21.4l9.4-6.1 1 2.4-9.6 6.2z",
  "M33.6 17.6l11.2-8.8 1.1 2.8-11.5 8.6z",
];
// over the core: the crest (stage 4), doubled at stage 7
const CRESTS = ["M18.5 8L24 4.5 29.5 8l-1 1.6L24 6.8l-4.5 2.8z", "M20 4L24 1.5 28 4l-.9 1.4L24 3.5l-3.1 1.9z"];
// under the core: chevrons (stages 5 and 6; the wreath takes their place at Elite)
const CHEVRONS = ["M19 37l5 3 5-3v1.9l-5 3-5-3z", "M20 41.2l4 2.4 4-2.4v1.8l-4 2.4-4-2.4z"];
// a navy rank's half step: a small diamond at the foot
const HALF = "M24 46.2l1.6 1.8L24 49.8l-1.6-1.8z";

/** What a rank shows: the stage (0..8, Elite = 8) adds one piece per rank — a wing, a second, a third, the
 * crest, a chevron, a second, a second crest, then the wreath; a navy rank climbs one stage per two ranks, the
 * rank between marked by a half step. @param {string} kind @param {number} rank
 * @returns {{stage: number, wings: number, crests: number, chevrons: number, wreath: boolean, stars: number,
 *   half: boolean, dim: boolean}} */
function emblemStage(kind, rank) {
  const look = RANK_LOOK[kind] || { top: 13 };
  rank = Math.max(0, Math.min(rank, look.top));
  const navy = Boolean(look.navy);
  const stage = navy ? (rank === look.top ? 8 : Math.ceil(rank / 2)) : Math.min(rank, 8);
  return {
    stage,
    wings: Math.min(stage, 3),
    crests: stage >= 7 ? 2 : stage >= 4 ? 1 : 0,
    chevrons: stage >= 8 ? 0 : Math.max(0, Math.min(stage - 4, 2)),
    wreath: stage >= 8,
    stars: navy ? 0 : Math.max(0, rank - 8),
    half: navy && rank > 0 && rank < look.top && rank % 2 === 0,
    dim: navy && rank === 0, // not enlisted
  };
}

/** @param {string} name @param {Record<string, string | number>} attrs @returns {SVGElement} */
function emblemNode(name, attrs = {}) {
  const e = /** @type {SVGElement} */ (document.createElementNS(EMBLEM_NS, name));
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  return e;
}

/** @param {number} cx @param {number} cy @param {number} r @returns {string} a four-point star */
function sparkPath(cx, cy, r) {
  const q = r * 0.3;
  return `M${cx} ${cy - r}L${cx + q} ${cy - q}L${cx + r} ${cy}L${cx + q} ${cy + q}L${cx} ${cy + r}L${cx - q} ${cy + q}L${cx - r} ${cy}L${cx - q} ${cy - q}z`;
}

/** The Elite wreath: two branches of swept leaves under the core. @returns {SVGElement} */
function wreath() {
  const g = emblemNode("g", { class: "em-wreath" });
  const r = 15.8;
  const at = (side, deg, out = 0) => {
    const t = (deg * Math.PI) / 180; // 0 = straight below the centre
    return [24 + side * (r + out) * Math.sin(t), 25 + (r + out) * Math.cos(t)];
  };
  for (const side of [-1, 1]) {
    const [x0, y0] = at(side, 8);
    const [x1, y1] = at(side, 66);
    g.append(emblemNode("path", {
      d: `M${x0.toFixed(2)} ${y0.toFixed(2)}A${r} ${r} 0 0 ${side < 0 ? 1 : 0} ${x1.toFixed(2)} ${y1.toFixed(2)}`,
      fill: "none", stroke: "currentColor", "stroke-width": 0.9,
    }));
    for (let i = 0; i < 4; i += 1) {
      const deg = 14 + i * 16;
      const t = (deg * Math.PI) / 180;
      const along = (Math.atan2(-Math.sin(t), side * Math.cos(t)) * 180) / Math.PI;
      const [x, y] = at(side, deg, i % 2 ? 1.6 : -1.6);
      const turn = along + side * (i % 2 ? -24 : 24);
      g.append(emblemNode("path", {
        d: "M-3.8 0Q0 -2.3 3.8 0Q0 2.3 -3.8 0z",
        transform: `translate(${x.toFixed(2)} ${y.toFixed(2)}) rotate(${turn.toFixed(1)})`,
      }));
    }
  }
  return g;
}

/**
 * The emblem for one rank, drawn in `currentColor`. @param {string} kind "Combat", "Federation", …
 * @param {number} rank the game's number @param {number} [size] pixels @returns {SVGElement}
 */
function rankEmblem(kind, rank, size = 64) {
  const s = emblemStage(kind, rank);
  const root = emblemNode("svg", { viewBox: "-1 0 50 50", width: size, height: size, class: "emblem", "aria-hidden": "true" });
  Object.assign(root.dataset, {
    kind, stage: String(s.stage), wings: String(s.wings), crests: String(s.crests), chevrons: String(s.chevrons),
    wreath: String(s.wreath), stars: String(s.stars), half: String(s.half),
  });
  if (s.dim) root.classList.add("dim");
  const art = emblemNode("g", { fill: "currentColor" });
  if (s.wreath) art.append(wreath());
  for (let i = 0; i < s.wings; i += 1) {
    art.append(
      emblemNode("path", { d: WINGS[i], class: "em-wing" }),
      emblemNode("path", { d: WINGS[i], class: "em-wing", transform: "translate(48 0) scale(-1 1)" }),
    );
  }
  for (let i = 0; i < s.crests; i += 1) art.append(emblemNode("path", { d: CRESTS[i], class: "em-crest" }));
  for (let i = 0; i < s.chevrons; i += 1) art.append(emblemNode("path", { d: CHEVRONS[i], class: "em-chevron" }));
  if (s.half) art.append(emblemNode("path", { d: HALF, class: "em-half" }));
  for (const [name, attrs] of CORES[kind] || []) art.append(emblemNode(name, attrs));
  // Elite I–V: sparks in a row under the wreath
  for (let i = 0; i < s.stars; i += 1) {
    const mid = (s.stars - 1) / 2;
    const x = 24 + (i - mid) * 5.6;
    art.append(emblemNode("path", { d: sparkPath(x, 45.2 - 0.35 * (i - mid) ** 2, 2.3), class: "em-star" }));
  }
  root.append(art);
  return root;
}

/**
 * One rank as a HUD card: emblem, label, progress bar, title, and what comes next.
 * @param {{kind: string, rank: number, top: number, title: string, next_title: string, progress: number,
 *   how: string, note: string, ladder: {title: string, unlocks: string}[]}} r
 * @returns {HTMLElement}
 */
function rankCard(r) {
  const look = RANK_LOOK[r.kind] || { label: `${r.kind} rank`, colour: "--rk-white" };
  const top = !r.next_title;
  const li = document.createElement("li");
  li.className = "rk";
  li.dataset.kind = r.kind;
  li.style.setProperty("--rk", `var(${look.colour})`);
  const body = document.createElement("div");
  body.className = "rk-body";
  const label = document.createElement("span");
  label.className = "rk-label";
  label.textContent = look.label;
  const bar = document.createElement("div");
  bar.className = "rk-bar";
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", "100");
  bar.setAttribute("aria-valuenow", String(top ? 100 : r.progress));
  bar.setAttribute("aria-label", `${look.label}: progress to the next`);
  const fill = document.createElement("span");
  fill.style.width = `${top ? 100 : Math.max(0, Math.min(r.progress, 100))}%`;
  bar.append(fill);
  const title = document.createElement("strong");
  title.className = "rk-title";
  title.textContent = r.title;
  const next = document.createElement("span");
  next.className = "rk-next";
  next.textContent = top ? "The top rank" : `${r.progress} % to ${r.next_title}`;
  body.append(label, bar, title, next);
  // the whole card opens its ladder in the page's (i) bubble; the emblem is its button for the keyboard
  li.dataset.info = `rk-ladder-${r.kind}`;
  const open = document.createElement("button");
  open.type = "button";
  open.className = "info rk-open";
  open.setAttribute("aria-describedby", li.dataset.info);
  open.setAttribute("aria-label", `${look.label}: every rank`);
  open.append(rankEmblem(r.kind, r.rank));
  li.append(open, body, rankLadder(r, look.label));
  return li;
}

/**
 * Every rank of one kind, yours marked, with what moves you up and what each rank opens: the text of the
 * (i) bubble the card opens (hidden in the card).
 * @param {{kind: string, rank: number, progress: number, next_title: string, how: string, note: string,
 *   ladder: {title: string, unlocks: string}[]}} r
 * @param {string} label
 * @returns {HTMLElement}
 */
function rankLadder(r, label) {
  const box = document.createElement("div");
  box.id = `rk-ladder-${r.kind}`;
  box.className = "info-text";
  box.hidden = true;
  const head = document.createElement("p");
  head.className = "rk-ladder-head";
  head.textContent = label;
  const how = document.createElement("p");
  how.textContent = r.how;
  const list = document.createElement("ol");
  list.className = "rk-ladder";
  list.start = 0;
  list.style.setProperty("--rows", String(Math.ceil(r.ladder.length / 2)));
  r.ladder.forEach((step, i) => {
    const item = document.createElement("li");
    item.className = i < r.rank ? "done" : i === r.rank ? "here" : "ahead";
    const name = document.createElement("span");
    name.className = "rk-step";
    name.textContent = step.title;
    item.append(name);
    if (i === r.rank) {
      item.setAttribute("aria-current", "step");
      const you = document.createElement("span");
      you.className = "rk-you";
      you.textContent = r.next_title ? `you · ${r.progress} %` : "you";
      item.append(you);
    }
    if (step.unlocks) {
      const opens = document.createElement("span");
      opens.className = "rk-opens";
      opens.textContent = step.unlocks;
      item.append(opens);
    }
    list.append(item);
  });
  box.append(head, how, list);
  if (r.note) {
    const note = document.createElement("p");
    note.className = "rk-note";
    note.textContent = r.note;
    box.append(note);
  }
  return box;
}

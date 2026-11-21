"use strict";
const el = id => document.getElementById(id);
const query = new URLSearchParams(location.search);
// VK links use ASCII code points. Previously sent links contain the word itself.
const codes = query.get("chars");
const letters = codes === null ? Array.from(query.get("word") ?? "你好") :
  codes.split("-").map(code => {
    const value = Number.parseInt(code, 16);
    return /^[0-9a-f]{1,6}$/i.test(code) && value <= 0x10ffff ? String.fromCodePoint(value) : "�";
  });
const chars = [];
const dataByLetter = new Map();
const previews = [];
let timing, writer, index = 0, pass = 0, target = 3, playback = 0;

for (let n = 1; n <= 10; n++) el("count").add(new Option(n, n));
el("count").value = "3";
try {
  const saved = Number(localStorage.getItem("memoryRepeats"));
  if (Number.isInteger(saved) && saved >= 1 && saved <= 10) el("count").value = String(saved);
} catch {}
el("count").onchange = () => {
  try { localStorage.setItem("memoryRepeats", el("count").value); } catch {}
};

async function json(path) {
  const response = await fetch(path);
  if (!response.ok) throw Error(path);
  return response.json();
}

function create(node, entry, size, outline) {
  return HanziWriter.create(node, entry.letter, {
    width: size, height: size, padding: size / 16,
    showCharacter: false, showOutline: outline, outlineColor: "#cbd2cc",
    strokeColor: "#20382f", drawingColor: "#205741", highlightColor: "#b4502d",
    showHintAfterMisses: 3, highlightOnComplete: false, ...timing,
    charDataLoader: (letter, resolve) => resolve(dataByLetter.get(letter))
  });
}

async function replay() {
  const generation = ++playback;
  await Promise.all(previews.map(w => w.hideCharacter({duration: 0})));
  for (const w of previews) {
    if (generation !== playback) return;
    await w.animateCharacter();
  }
}

async function practice() {
  writer?.cancelQuiz();
  el("cell").hidden = false;
  el("count").disabled = true;
  el("start").hidden = true;
  const entry = chars[index];
  const stage = pass ? "По памяти " + pass + " из " + target : "По светлому контуру";
  el("stage").textContent = entry.letter + " · " + stage;
  if (!writer) writer = create(el("cell"), entry, 240, pass === 0);
  else await writer.setCharacter(entry.letter);
  await writer.hideCharacter({duration: 0});
  if (pass === 0) await writer.showOutline({duration: 0});
  else await writer.hideOutline({duration: 0});
  writer.quiz({onComplete() {
    if (index === chars.length - 1 && pass === target) {
      el("stage").textContent = letters.join("") + " · Серия завершена";
      el("start").textContent = "Начать серию заново";
      el("start").hidden = false;
      el("count").disabled = false;
      return;
    }
    index++;
    if (index === chars.length) {
      index = 0;
      pass++;
    }
    requestAnimationFrame(practice);
  }});
}

function start() {
  index = 0;
  pass = 0;
  target = Number(el("count").value);
  practice();
}
el("start").onclick = start;
el("replay").onclick = replay;

async function init() {
  el("word").textContent = letters.join("");
  if (location.protocol === "file:") {
    el("notice").textContent = "Страница открыта с диска. Для загрузки черт откройте ссылку на тренажёр в домашней сети или кнопку «Порядок черт» в ВК.";
    el("count").disabled = true;
    return;
  }
  const speeds = await json("speeds.json");
  timing = speeds[query.get("speed")] ?? speeds.normal;
  const entries = await Promise.all(letters.map(async letter => {
    let data;
    if (/^\p{Script=Han}$/u.test(letter)) {
      try { data = await json("characters/" + encodeURIComponent(letter) + ".json"); } catch {}
    }
    return {letter, data};
  }));
  const missing = new Set();
  for (const entry of entries) {
    const node = document.createElement("span");
    el("preview").append(node);
    if (entry.data?.strokes?.length && entry.data.medians?.length === entry.data.strokes.length) {
      chars.push(entry);
      dataByLetter.set(entry.letter, entry.data);
      previews.push(create(node, entry, 96, true));
    } else {
      missing.add(entry.letter);
      node.textContent = entry.letter;
    }
  }
  el("notice").textContent = missing.size ? "Нет данных о чертах для " + [...missing].join(", ") : "";
  if (chars.length) {
    el("replay").disabled = el("start").disabled = false;
    await replay();
  }
}
init().catch(() => { el("notice").textContent = "Не удалось загрузить страницу. Обновите её."; });

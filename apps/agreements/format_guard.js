/* Formatting protection for custom service text: a line-for-line port of format_guard.py, used by the builder's live
   "How it will print" preview and the Preview text dialog. tests/test_formatting_guard.py keeps both in step on the shared
   examples in tests/format_examples.json. */
"use strict";
(function (root) {
  const BULLET_SYMBOLS = "•●◦▪▫■□➢➤►▶▸✓✔∙·○◆❖";
  const DASH_SYMBOLS = "-–—*";
  const SPACES = "\t               　";
  const INVISIBLE = "​‌‍⁠﻿­";
  const BREAKS = "  \u000b\u000c\u0085";
  const escape = s => s.replace(/[\\\]\[\-^]/g, "\\$&");
  const SYMBOL = "[" + escape(BULLET_SYMBOLS) + "]";
  const DASH = "[" + escape(DASH_SYMBOLS) + "]";
  const BULLET = new RegExp("^(?:" + SYMBOL + " *|" + DASH + " +)(?=[^ ])(.*)$", "su");
  const MARKER = new RegExp("^(?:" + SYMBOL + "|" + DASH + ")$", "u");
  const DECIMAL = /^(?:\((\d{1,2})\)|(\d{1,2})[.)]) +(?=[^ ])(.*)$/su;
  const LETTER = /^(?:\(([a-z])\)|([a-z])[.)]) +(?=[^ ])(.*)$/su;
  const LABEL = /^([^:]{1,80}?) *: +(?=[^ ])(.*)$/su;
  const CLEAN_RUNS = new RegExp("[" + SPACES + "]+| {2,}", "gu");
  const TERMINAL = /[.:;!?]["'”’)\]]*$/u;
  const MAX_LABEL_WORDS = 8;
  const WRAPPED_LINE = 60;

  const isUpper = ch => !!ch && ch.toLowerCase() !== ch && ch.toUpperCase() === ch;
  const isLower = ch => !!ch && ch.toUpperCase() !== ch && ch.toLowerCase() === ch;
  const isDigit = ch => /^[0-9]$/.test(ch || "");
  const strip = s => s.replace(/^ +| +$/g, "");

  function labelOf(text) {
    const m = LABEL.exec(text);
    if (!m) return "";
    const label = strip(m[1]);
    if (!label || label.split(" ").length > MAX_LABEL_WORDS || /[.;!?"“”]/.test(label)) return "";
    if (!(isUpper(label[0]) || isDigit(label[0]))) return "";
    return label;
  }

  function normalise(text) {
    let s = String(text ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    for (const ch of BREAKS) s = s.split(ch).join("\n");
    for (const ch of INVISIBLE) s = s.split(ch).join("");
    for (const ch of SPACES) s = s.split(ch).join(" ");
    return s;
  }

  function tidy(text, stats) {
    text = strip(text.replace(/ {2,}/g, " "));
    const first = text.split(" ")[0];
    if (text && isLower(text[0]) && first === first.toLowerCase()) {
      text = text[0].toUpperCase() + text.slice(1);
      if (stats) stats.capitalised += 1;
    }
    return text;
  }

  function continues(previous, previousLine, line) {
    if (TERMINAL.test(previous)) return false;
    return isLower(line[0]) || "(&,".includes(line[0]) || previousLine >= WRAPPED_LINE;
  }

  function join(previous, line) {
    if (previous.endsWith("-") && previous.length > 1 && previous[previous.length - 2] !== " " && isLower(line[0])) return previous + line;
    return previous + " " + line;
  }

  function classify(line) {
    if (MARKER.test(line)) return ["marker", "", null];
    let m = BULLET.exec(line);
    if (m) return ["bullet", m[1], null];
    m = DECIMAL.exec(line);
    if (m) return ["number", m[3], ["decimal", Number(m[1] || m[2])]];
    m = LETTER.exec(line);
    if (m) return ["number", m[3], ["letter", (m[1] || m[2]).charCodeAt(0) - 96]];
    if (labelOf(line)) return ["label", line, null];
    return ["text", line, null];
  }

  function inspect(text) {
    const raw = String(text ?? "");
    const stats = {bullets: 0, numbers: 0, leads: 0, labels: 0, joined: 0, capitalised: 0,
      spaces: raw.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n").reduce((n, line) => n + (line.match(CLEAN_RUNS) || []).length, 0)};
    const lines = normalise(raw).split("\n").map(strip);
    const entries = [];
    let pending = false;
    for (const line of lines) {
      if (!line) { entries.push(null); continue; }
      let [kind, body, extra] = classify(line);
      if (pending) {
        pending = false;
        if (kind === "text" || kind === "label") kind = "bullet";
      }
      if (kind === "marker") { pending = true; continue; }
      entries.push([kind, body, extra]);
    }
    const neighbour = (index, step) => {
      index += step;
      while (index >= 0 && index < entries.length && entries[index] === null) index += step;
      return index >= 0 && index < entries.length ? entries[index] : null;
    };
    const labelled = entry => !!entry && (entry[0] === "label" || ((entry[0] === "bullet" || entry[0] === "number") && !!labelOf(tidy(entry[1]))));
    entries.forEach((entry, i) => {
      if (entry && entry[0] === "label" && (labelled(neighbour(i, -1)) || labelled(neighbour(i, 1)))) entries[i] = ["bullet", entry[1], "run"];
    });

    const blocks = [];
    let paragraph = null, lastBlank = true, lastLine = 0;
    const flush = () => { if (paragraph !== null) { blocks.push({type: "p", text: tidy(paragraph, stats)}); paragraph = null; } };
    for (const entry of entries) {
      if (entry === null) { flush(); lastBlank = true; continue; }
      const [kind, body, extra] = entry;
      const last = blocks[blocks.length - 1];
      if (kind === "bullet" || kind === "number") {
        flush();
        const item = {type: kind, text: tidy(body, stats)};
        if (kind === "number") {
          const [style, value] = extra;
          const previous = blocks[blocks.length - 1];
          if (value === 1 || !(previous && previous.type === "number" && previous.style === style)) item.start = value;
          item.style = style;
        }
        const label = labelOf(item.text);
        if (label) item.label = label;
        blocks.push(item);
      } else if (paragraph === null && !lastBlank && kind === "text" && last && (last.type === "bullet" || last.type === "number") && continues(last.text, lastLine, body)) {
        last.text = join(last.text, body);
        stats.joined += 1;
      } else if (paragraph !== null && kind === "text" && continues(paragraph, lastLine, body)) {
        paragraph = join(paragraph, body);
        stats.joined += 1;
      } else {
        flush();
        paragraph = body;
      }
      lastBlank = false;
      lastLine = [...body].length;
    }
    flush();
    blocks.forEach((block, i) => {
      if (i < blocks.length - 1 && block.type === "p" && block.text.endsWith(":") && (blocks[i + 1].type === "bullet" || blocks[i + 1].type === "number")) block.type = "lead";
    });
    stats.bullets = blocks.filter(b => b.type === "bullet").length;
    stats.numbers = blocks.filter(b => b.type === "number").length;
    stats.leads = blocks.filter(b => b.type === "lead").length;
    stats.labels = blocks.filter(b => "label" in b).length;
    return {blocks, stats};
  }

  const api = {inspect, structure: text => inspect(text).blocks, labelOf};
  root.TFSCFormat = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

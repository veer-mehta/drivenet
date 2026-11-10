const $ = (id) => document.getElementById(id);
const worker = new Worker("worker.js");
const pending = new Map();
let nextId = 1;

const ui = {
  state: null,
  email: "",
  cwd: "/",
  found: null,
  busy: false,
  creating: false,
  heartbeat: null,
  searchTimer: null,
  noticeTimer: null,
  onProgress: null,
  dropped: null,
  sel: 0,
};

function call(op, ...args) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, op, args });
  });
}

worker.onmessage = async ({ data }) => {
  if (data.type === "token") {
    try {
      worker.postMessage({ type: "token-reply", id: data.id, token: await Auth.tokenFor(data.account) });
    } catch (err) {
      worker.postMessage({ type: "token-reply", id: data.id, error: err.message });
    }
    return;
  }
  if (data.type === "boot") return bootStep(data.text);
  if (data.type === "progress") return ui.onProgress && ui.onProgress(data.done, data.total, data.idx);
  if (data.id === 0) {
    if (!data.ok) {
      $("boot").append(element("p", "note", data.error));
      return;
    }
    await call("start", Auth.sessionId());
    return showGate();
  }
  const waiter = pending.get(data.id);
  pending.delete(data.id);
  data.ok ? waiter.resolve(data.result) : waiter.reject(new Error(data.error));
};

const LOGO_GRID = [
  "...W",
  ".WWW",
  "W..W",
  "W..W",
  ".RWW",
];
const LOGO_TONE = { W: "c0", R: "c1", G: "c2" };

function buildLogo() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${LOGO_GRID[0].length} ${LOGO_GRID.length}`);
  svg.setAttribute("preserveAspectRatio", "xMinYMax meet");
  svg.setAttribute("aria-hidden", "true");
  LOGO_GRID.forEach((row, y) => {
    [...row].forEach((mark, x) => {
      if (mark === ".") return;
      const cell = document.createElementNS(ns, "rect");
      cell.setAttribute("x", x + 0.04);
      cell.setAttribute("y", y + 0.04);
      cell.setAttribute("width", 0.92);
      cell.setAttribute("height", 0.92);
      cell.setAttribute("class", LOGO_TONE[mark]);
      svg.append(cell);
    });
  });
  return svg;
}

function fitLogo() {
  const style = getComputedStyle(document.querySelector(".mark"));
  const context = document.createElement("canvas").getContext("2d");
  context.font = `${style.fontWeight} 100px ${style.fontFamily}`;
  const metrics = context.measureText("d");
  document.documentElement.style.setProperty("--logo-h", `${metrics.actualBoundingBoxAscent / 100}em`);
  document.documentElement.style.setProperty("--logo-w", `${metrics.width / 100}em`);
}

document.querySelectorAll(".logo").forEach((node) => node.append(buildLogo()));
fitLogo();

function startDemo() {
  const root = $("demo");
  const content = "datadatadatadatada";
  const blocks = content.length;
  const drives = 3;
  const perDrive = blocks / drives;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const hexDigit = () => "0123456789abcdef"[Math.floor(Math.random() * 16)];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const wait = async (ms) => {
    await sleep(ms);
    while ($("gate").hidden) await sleep(400);
  };

  const lane = element("div", "lane");
  const fileBox = element("div", "col");
  const driveBox = element("div", "col");
  fileBox.append(element("span", "lab", "file"));
  driveBox.append(element("span", "lab", "your drives"));
  const fileGrid = element("div", "filegrid solid");
  fileBox.append(fileGrid);
  const driveRows = element("div", "driverows");
  driveBox.append(driveRows);
  lane.append(fileBox, driveBox);

  const fileCells = Array.from({ length: blocks }, () => fileGrid.appendChild(element("span", "cell plain")));
  const slots = Array.from({ length: drives }, (_, d) => {
    const line = element("div", "drow");
    const cells = element("span", "sq");
    line.append(element("span", "lab", "drive " + (d + 1)), cells);
    driveRows.append(line);
    return Array.from({ length: perDrive }, () => cells.appendChild(element("span", "cell slot")));
  });

  const steps = [
    ["file", "this is your file"],
    ["split", "it is cut into blocks"],
    ["encrypt", "every block is locked with a key made from your password"],
    ["send", "the locked blocks are spread across your google drives"],
  ];
  const stepper = element("div", "stages");
  const stages = steps.map(([text]) => stepper.appendChild(element("span", "", text)));
  const caption = element("p", "caption");
  root.append(lane, stepper, caption);

  const stage = (n) => {
    stages.forEach((node, i) => node.classList.toggle("on", i === n));
    caption.textContent = steps[n][1];
  };
  const glyphs = Array.from({ length: blocks }, hexDigit);
  const place = (cell, drive, glyph) => {
    cell.className = "cell enc " + shardClass(drive);
    cell.textContent = glyph;
  };

  const reset = () => {
    fileGrid.classList.add("solid");
    fileCells.forEach((cell, i) => { cell.className = "cell plain"; cell.textContent = content[i]; });
    slots.flat().forEach((cell) => { cell.className = "cell slot"; cell.textContent = ""; });
  };

  const encrypt = async (cell, index, delay) => {
    await wait(delay);
    cell.className = "cell enc s0 working";
    for (let n = 0; n < 9; n++) {
      cell.textContent = hexDigit();
      await sleep(60);
    }
    cell.textContent = glyphs[index];
    cell.className = "cell enc s0";
  };

  const fly = (index) => {
    const drive = index % drives;
    const target = slots[drive][Math.floor(index / drives)];
    const from = fileCells[index];
    const base = root.getBoundingClientRect();
    const start = from.getBoundingClientRect();
    const end = target.getBoundingClientRect();
    const ghost = element("span", "cell enc ghost " + shardClass(drive), glyphs[index]);
    ghost.style.left = end.left - base.left + "px";
    ghost.style.top = end.top - base.top + "px";
    root.append(ghost);
    from.className = "cell slot";
    from.textContent = "";
    ghost.animate(
      [{ transform: `translate(${start.left - end.left}px, ${start.top - end.top}px)` }, { transform: "translate(0, 0)" }],
      { duration: 700, easing: "cubic-bezier(0.5, 0, 0.2, 1)" },
    ).finished.then(() => {
      place(target, drive, glyphs[index]);
      ghost.remove();
    });
  };

  if (reduced) {
    fileGrid.classList.remove("solid");
    fileCells.forEach((cell) => { cell.className = "cell slot"; });
    slots.forEach((cells, d) => cells.forEach((cell, k) => place(cell, d, glyphs[k * drives + d])));
    stage(3);
    return;
  }

  (async () => {
    for (;;) {
      reset();
      stage(0);
      await wait(1800);
      stage(1);
      fileGrid.classList.remove("solid");
      await wait(1800);
      stage(2);
      await Promise.all(fileCells.map((cell, i) => encrypt(cell, i, i * 90)));
      await wait(1800);
      stage(3);
      for (let i = 0; i < blocks; i++) {
        fly(i);
        await wait(200);
      }
      await wait(2800);
    }
  })();
}

let bootDone = 0;

function bootStep() {
  const steps = document.querySelectorAll("#boot-steps li");
  if (steps[bootDone]) steps[bootDone].className = "done";
  bootDone++;
  if (steps[bootDone]) steps[bootDone].className = "active";
}

function makeSelect(root, onChange) {
  const button = root.querySelector(".select-btn");
  const list = root.querySelector(".select-list");
  const label = root.querySelector(".select-label");
  const items = [...list.children];
  let active = Math.max(0, items.findIndex((item) => item.getAttribute("aria-selected") === "true"));

  const highlight = (index) => {
    active = index;
    items.forEach((item, n) => item.classList.toggle("active", n === index));
  };
  const open = () => {
    list.hidden = false;
    button.setAttribute("aria-expanded", "true");
    highlight(items.findIndex((item) => item.getAttribute("aria-selected") === "true"));
  };
  const close = () => {
    list.hidden = true;
    button.setAttribute("aria-expanded", "false");
  };
  const choose = (index) => {
    items.forEach((item, n) => item.setAttribute("aria-selected", String(n === index)));
    label.textContent = items[index].textContent;
    root.dataset.value = items[index].dataset.value;
    close();
    button.focus();
    onChange();
  };

  button.onclick = () => (list.hidden ? open() : close());
  button.onkeydown = (event) => {
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (step) {
      event.preventDefault();
      if (list.hidden) open();
      else highlight((active + step + items.length) % items.length);
    } else if (event.key === "Enter" && !list.hidden) {
      event.preventDefault();
      choose(active);
    } else if (event.key === "Escape" && !list.hidden) {
      event.stopPropagation();
      close();
    } else if (event.key === "Tab") {
      close();
    }
  };
  items.forEach((item, index) => {
    item.onmouseenter = () => highlight(index);
    item.onclick = () => choose(index);
  });
  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) close();
  });
}

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

function formatBytes(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${unit ? value.toFixed(value < 10 ? 2 : 1) : value} ${units[unit]}`;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function textButton(label, onclick, className = "") {
  const node = element("button", className, label);
  node.type = "button";
  node.onclick = onclick;
  return node;
}

function icon(name) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "ico");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

function iconButton(name, label, onclick, className = "") {
  const node = element("button", "icon-btn " + className);
  node.type = "button";
  node.title = label;
  node.setAttribute("aria-label", label);
  node.append(icon(name));
  node.onclick = onclick;
  return node;
}

function accountIndex(email) {
  return ui.state ? ui.state.accounts.indexOf(email) : -1;
}

const shardClass = (index) => "s" + (index < 0 ? 3 : index % 4);

function cellRow(indexes, className = "cells") {
  const box = element("span", className);
  for (const index of indexes) box.append(element("span", shardClass(index), "■"));
  return box;
}

function sample(shards, width) {
  if (shards.length <= width) return shards.map(accountIndex);
  return Array.from({ length: width }, (_, i) => accountIndex(shards[Math.floor((i * shards.length) / width)]));
}

function lookup(path) {
  let node = ui.state.tree;
  for (const name of path.split("/").filter(Boolean)) {
    if (node.type !== "dir" || !node.children[name]) return null;
    node = node.children[name];
  }
  return node;
}

function weight(node) {
  return node.type === "file" ? node.size : Object.values(node.children).reduce((sum, child) => sum + weight(child), 0);
}

function entries(node, base) {
  const join = (name) => (base === "/" ? "" : base) + "/" + name;
  return Object.entries(node.children)
    .map(([name, child]) => ({ name, path: join(name), type: child.type, size: weight(child), shards: child.shards || [] }))
    .sort((a, b) => (b.type === "dir") - (a.type === "dir") || a.name.localeCompare(b.name));
}

const parentOf = (path) => "/" + path.split("/").filter(Boolean).slice(0, -1).join("/");

function syncDock() {
  $("dock").hidden = !$("notice").textContent && !$("transfers").children.length;
  document.body.classList.toggle("has-dock", !$("dock").hidden);
}

function notice(text, isError = false) {
  clearTimeout(ui.noticeTimer);
  $("notice").textContent = text;
  $("notice").className = isError ? "err" : "";
  if (!isError) ui.noticeTimer = setTimeout(() => { $("notice").textContent = ""; syncDock(); }, 6000);
  syncDock();
}

function setBusy(text) {
  $("busy-text").textContent = text || "";
  $("gate-busy").hidden = !text;
}

async function run(action, target = "app", label = "working") {
  if (ui.busy) {
    notice("wait for the current job to finish.", true);
    return false;
  }
  ui.busy = true;
  document.body.classList.add("busy");
  if (target === "gate") setBusy(label);
  try {
    await action();
    return true;
  } catch (err) {
    if (target === "gate") $("gate-note").textContent = err.message;
    else notice(err.message, true);
    return false;
  } finally {
    ui.busy = false;
    document.body.classList.remove("busy");
    if (target === "gate") setBusy("");
  }
}

function requireConnected() {
  const missing = ui.state.accounts.filter((email) => !Auth.isConnected(email));
  if (missing.length) throw new Error(`connect ${missing.join(", ")} first (see accounts)`);
}

function transfer(label, owner = () => 0) {
  const row = element("div", "xfer");
  const box = element("span", "cells bar");
  const percent = element("span", "pct", "0%");
  row.append(element("span", "what", label), box, percent);
  $("transfers").append(row);
  while ($("transfers").children.length > 4) $("transfers").firstChild.remove();
  syncDock();

  let slots = [];
  let blocks = -1;
  const build = (total) => {
    blocks = total;
    box.replaceChildren();
    slots = Array.from({ length: Math.max(1, Math.min(total, 24)) }, () => {
      const slot = element("span", "pending", "□");
      box.append(slot);
      return slot;
    });
  };
  build(24);
  blocks = -1;

  ui.onProgress = (done, total, idx) => {
    if (total !== blocks) build(total);
    const position = idx >= 0 ? idx : done - 1;
    const slot = slots[Math.min(slots.length - 1, Math.floor((position * slots.length) / Math.max(total, 1)))];
    slot.textContent = "■";
    slot.className = shardClass(owner(position));
    percent.textContent = Math.round((done / Math.max(total, 1)) * 100) + "%";
  };

  return {
    finish() {
      ui.onProgress = null;
      if (blocks <= 0) box.replaceChildren();
      percent.textContent = blocks <= 0 ? "empty" : "100%";
      setTimeout(() => { row.remove(); syncDock(); }, 8000);
    },
    fail() {
      ui.onProgress = null;
      percent.textContent = "stopped";
      percent.className = "pct bad";
      setTimeout(() => { row.remove(); syncDock(); }, 12000);
    },
  };
}

async function tracked(bar, work) {
  try {
    const result = await work;
    bar.finish();
    return result;
  } catch (err) {
    bar.fail();
    throw err;
  }
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function toggleLabel(open) {
  if (open) return "close";
  return Auth.ownClientId ? "edit" : "use your own google project";
}

function showGate() {
  $("boot").hidden = true;
  $("gate").hidden = false;
  $("origin").textContent = location.origin;
  $("own-id").hidden = false;
  $("own-id-reset").hidden = !Auth.ownClientId;
  $("own-id-state").textContent = Auth.ownClientId ? "using your own google project" : "";
  $("own-id-toggle").textContent = toggleLabel(false);
  if (!Auth.clientId) {
    $("own-id-state").textContent = "none set";
    $("own-id-toggle").hidden = true;
    $("own-id-form").hidden = false;
    $("owner-note").hidden = false;
    $("why-default").hidden = true;
  } else if (Auth.remembered() || Auth.lastAccount()) {
    const email = Auth.lastAccount() || Auth.remembered();
    showUnlock(email);
    restoreSession(email);
  } else {
    $("signin").hidden = false;
  }
}

function setPasswordMode(on, automatic = false) {
  $("password").hidden = !on;
  document.querySelector('label[for="password"]').hidden = !on;
  $("unlock-btn").hidden = !on;
  $("mode").hidden = !on;
  $("resume").hidden = on || automatic;
  $("use-password").hidden = on || automatic;
}

function showUnlock(email) {
  ui.email = email;
  setPasswordMode(!Keep.has(), true);
  Auth.remember(email);
  $("who").textContent = email;
  $("about").hidden = true;
  $("signin").hidden = true;
  $("own-id").hidden = true;
  $("unlock").hidden = false;
  setCreating(false);
  $("password").focus();
}

function setNote(text, info = false) {
  $("gate-note").textContent = text;
  $("gate-note").classList.toggle("info", info);
}

async function openSaved(saved, email) {
  for (let attempt = 0; ; attempt++) {
    try {
      await call("open_with_key", saved.secret, JSON.stringify([email]));
      return;
    } catch (err) {
      if (/no longer matches/.test(err.message)) {
        await Keep.clear();
        throw new Error("the saved session expired. enter your password");
      }
      if (attempt >= 2) throw err;
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
  }
}

async function restoreSession(email) {
  const saved = await Keep.load();
  if (!saved || saved.email !== email) {
    setPasswordMode(true);
    return;
  }
  if (!Auth.isConnected(email)) {
    setPasswordMode(false);
    return;
  }
  setPasswordMode(false, true);
  const opened = await run(async () => {
    await openSaved(saved, email);
    await afterUnlock();
  }, "gate", "restoring your session");
  if (!opened) setPasswordMode(true);
}

function setCreating(creating) {
  ui.creating = creating;
  if (creating) setPasswordMode(true);
  $("repeat-box").hidden = !creating;
  $("unlock-btn").textContent = creating ? "create vault" : "unlock";
  $("mode").textContent = creating ? "i have a vault" : "new vault";
}

async function enterApp() {
  $("password").value = "";
  $("password2").value = "";
  $("gate-note").textContent = "";
  setCreating(false);
  $("gate").hidden = true;
  $("app").hidden = false;
  ui.cwd = "/";
  ui.found = null;
  $("q").value = "";
  await refresh();
  clearInterval(ui.heartbeat);
  ui.heartbeat = setInterval(() => call("renew").catch((err) => notice(err.message, true)), 30000);
  if (Keep.enabled()) call("export_key").then((key) => Keep.save(ui.email, key)).catch(() => {});
}

async function refresh() {
  ui.state = JSON.parse(await call("state"));
  while (ui.cwd !== "/" && !(lookup(ui.cwd) && lookup(ui.cwd).type === "dir")) ui.cwd = parentOf(ui.cwd);
  renderAll();
}

function renderAll() {
  renderPath();
  renderList();
  renderAccounts();
  renderSettings();
  renderUnfinished();
  renderStatus();
}

const PRESETS = { block_mb: [0.5, 1, 2, 4, 5], transfers: [1, 2, 4, 8, 16], kdf: [15, 16, 17] };
const SETTING_LABELS = { kdf: { 15: "2^15", 16: "2^16", 17: "2^17" } };

function renderSettings() {
  const { settings, limits } = ui.state;
  for (const key of Object.keys(limits)) {
    const box = $("set-" + key);
    box.replaceChildren();
    const [low, high] = limits[key];
    const save = (value) => run(async () => {
      await call("set_settings", JSON.stringify({ [key]: value }));
      await refresh();
    });
    for (const value of PRESETS[key]) {
      const label = (SETTING_LABELS[key] && SETTING_LABELS[key][value]) || String(value);
      const option = textButton(label, () => save(value));
      option.setAttribute("aria-pressed", String(settings[key] === value));
      box.append(option);
    }
    const custom = element("input", "custom");
    custom.type = "text";
    custom.inputMode = "decimal";
    custom.autocomplete = "off";
    custom.placeholder = `custom ${low}-${high}`;
    custom.title = "type a number and press enter";
    custom.setAttribute("aria-label", `custom value from ${low} to ${high}`);
    if (!PRESETS[key].includes(settings[key])) {
      custom.value = String(settings[key]);
      custom.setAttribute("aria-pressed", "true");
    }
    custom.onkeydown = (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      const text = custom.value.trim();
      const value = Number(text);
      if (!text || !Number.isFinite(value) || value < low || value > high) {
        notice(`enter a number from ${low} to ${high}`, true);
        return;
      }
      save(value);
    };
    box.append(custom);
  }
  const stay = $("set-keep");
  stay.replaceChildren();
  for (const [label, value] of [["off", "off"], ["this tab", "tab"], ["1 day", "1"], ["7 days", "7"], ["30 days", "30"]]) {
    const option = textButton(label, async () => {
      await Keep.setMode(value);
      if (value !== "off") call("export_key").then((key) => Keep.save(ui.email, key)).catch(() => {});
      renderSettings();
    });
    option.setAttribute("aria-pressed", String(Keep.mode() === value));
    stay.append(option);
  }
}

function renderUnfinished() {
  const box = $("unfinished");
  box.replaceChildren();
  box.hidden = !ui.state.unfinished.length;
  for (const item of ui.state.unfinished) {
    const row = element("div", "stalled");
    const go = element("button", "btn with-ico");
    go.type = "button";
    go.append(icon("play"), "continue");
    go.onclick = () => resume(item);
    row.append(
      element("span", "name", item.path),
      element("span", "meta", `${item.done} of ${item.total} blocks saved`),
      go,
      iconButton("x", "discard this upload", () => run(async () => {
        await call("discard_upload", item.path);
        await refresh();
        notice(`discarded ${item.path}`);
      })),
    );
    box.append(row);
  }
}

function resume(item) {
  run(async () => {
    requireConnected();
    const [picked] = await pick(false);
    if (!picked) return;
    if (picked.file.size !== item.size || picked.file.lastModified !== item.stamp) {
      throw new Error("that is not the same file. choose the exact file you were uploading");
    }
    const accounts = ui.state.accounts.length;
    const bar = transfer(`continuing ${item.path}`, (idx) => idx % accounts);
    try {
      await tracked(bar, call("upload", picked.file, item.path));
      notice(`finished ${item.path}`);
    } finally {
      await refresh();
    }
  });
}

function renderStatus() {
  const { accounts, files, usage } = ui.state;
  const blocks = Object.values(usage).reduce((sum, count) => sum + count, 0);
  $("top-email").textContent = ui.email;
  $("st-stored").textContent = formatBytes(files.reduce((sum, file) => sum + file.size, 0));
  $("st-sub").textContent = `${plural(files.length, "file")} · ${plural(blocks, "block")} · ${plural(accounts.length, "drive")}`;
  renderMap(accounts, usage, blocks);
}

function renderMap(accounts, usage, blocks) {
  const map = $("map");
  map.replaceChildren();
  if (!blocks) {
    $("map-note").textContent = "no blocks stored yet";
    return;
  }
  const perSquare = Math.max(1, Math.ceil(blocks / 480));
  accounts.forEach((email, index) => {
    const count = usage[email] || 0;
    const squares = count ? Math.max(1, Math.round(count / perSquare)) : 0;
    for (let n = 0; n < squares; n++) map.append(element("span", "cell " + shardClass(index)));
  });
  $("map-note").textContent = perSquare === 1 ? "each square is one encrypted block, coloured by the drive that holds it" : `each square is about ${perSquare} blocks, coloured by the drive that holds them`;
}

function go(path) {
  ui.sel = 0;
  ui.cwd = path;
  ui.found = null;
  $("q").value = "";
  $("search-note").textContent = "";
  renderAll();
}

function renderPath() {
  const nav = $("path");
  nav.replaceChildren();
  const parts = ui.cwd.split("/").filter(Boolean);
  const crumb = (label, path, current) => {
    const button = textButton(label, () => go(path));
    if (current) button.setAttribute("aria-current", "location");
    nav.append(button);
  };
  crumb("/", "/", !parts.length);
  parts.forEach((part, index) => {
    if (index) nav.append(element("span", "", "/"));
    crumb(part, "/" + parts.slice(0, index + 1).join("/"), index === parts.length - 1);
  });
}

function makeRow(item, flat) {
  const dir = item.type === "dir";
  const row = element("div", "row");
  const kind = element("span", "kind");
  kind.append(icon(dir ? "folder" : "file"));
  const label = flat ? item.path : item.name + (dir ? "/" : "");
  const name = dir || flat ? textButton(label, () => go(dir ? item.path : parentOf(item.path)), "link name" + (dir ? " dir" : "")) : element("span", "name", label);
  const strip = cellRow(dir ? [] : sample(item.shards, 10), "strip");
  if (!dir) strip.title = plural(item.shards.length, "block");
  const actions = element("span", "acts");
  const idle = () => {
    actions.replaceChildren(
      iconButton("download", dir ? "download as zip" : "download", () => download(item)),
      iconButton("trash", "delete", () => {
        actions.replaceChildren(
          element("span", "ask", "delete?"),
          textButton("yes", () => remove(item), "txt yes"),
          textButton("no", idle, "txt"),
        );
      }, "danger"),
    );
  };
  idle();
  row.append(kind, name, strip, element("span", "size", formatBytes(item.size)), actions);
  return row;
}

function renderList() {
  const list = $("list");
  list.replaceChildren();
  let rows;
  let flat = false;
  if (ui.found) {
    flat = true;
    rows = ui.found.map((hit) => {
      const node = lookup(hit.path);
      return { ...hit, size: node ? weight(node) : hit.size, shards: (node && node.shards) || [] };
    });
  } else {
    const node = lookup(ui.cwd);
    rows = entries(node, ui.cwd);
    if (ui.cwd !== "/") {
      const up = element("div", "row");
      const upKind = element("span", "kind");
      upKind.append(icon("up"));
      up.append(upKind, textButton("..", () => go(parentOf(ui.cwd)), "link name dir"), element("span"), element("span"), element("span"));
      list.append(up);
    }
  }
  if (!rows.length) {
    const none = ui.found ? "no matches." : ui.cwd === "/" ? "nothing here yet. drop files anywhere, or use upload." : "empty folder.";
    list.append(element("div", "empty", none));
    return;
  }
  rows.forEach((row) => list.append(makeRow(row, flat)));
  select(Math.min(ui.sel, list.children.length - 1));
}

function select(index) {
  const rows = [...document.querySelectorAll("#list .row")];
  ui.sel = Math.max(0, Math.min(index, rows.length - 1));
  rows.forEach((row, n) => row.classList.toggle("sel", n === ui.sel));
  if (rows[ui.sel]) rows[ui.sel].scrollIntoView({ block: "nearest" });
}

$("list").addEventListener("mouseover", (event) => {
  const row = event.target.closest(".row");
  if (row) select([...$("list").children].indexOf(row));
});

function renderAccounts() {
  const list = $("accounts");
  list.replaceChildren();
  const { accounts, usage } = ui.state;
  accounts.forEach((email, index) => {
    const connected = Auth.isConnected(email);
    const item = element("li", "account " + shardClass(index));
    const tools = element("span", "acc-tools");
    const idle = () => {
      tools.replaceChildren();
      if (!connected) {
        tools.append(iconButton("plug", "connect this account", () => run(async () => {
          await Auth.connect(email);
          renderAccounts();
        })));
      }
      tools.append(iconButton("trash", "remove from vault", () => {
        tools.replaceChildren(
          element("span", "ask", "remove?"),
          textButton("yes", () => run(async () => {
            await call("remove_account", email);
            await refresh();
            notice(`removed ${email}`);
          }), "txt"),
          textButton("no", idle, "txt"),
        );
      }, "danger"));
    };
    idle();
    const meta = element("div", "acc-meta");
    meta.append(`${plural(usage[email] || 0, "block")} · `, element("span", connected ? "" : "off", connected ? "connected" : "not connected"));
    const top = element("div", "acc-top");
    top.append(element("span", "email", email), tools);
    item.append(top, meta);
    list.append(item);
  });
}

async function download(item) {
  run(async () => {
    requireConnected();
    const dir = item.type === "dir";
    const name = item.path.split("/").pop();
    const bar = transfer(`downloading ${item.name || name}`, dir ? undefined : (idx) => accountIndex(item.shards[idx]));
    const blob = await tracked(bar, call(dir ? "download_folder" : "download", item.path));
    saveBlob(blob, dir ? name + ".zip" : name);
  });
}

function remove(item) {
  run(async () => {
    requireConnected();
    const dir = item.type === "dir";
    const bar = transfer(`deleting ${item.name || item.path}`, dir ? undefined : (idx) => accountIndex(item.shards[idx]));
    await tracked(bar, call(dir ? "delete_folder" : "delete_file", item.path));
    await refresh();
    if (ui.found) runSearch();
  });
}

async function uploadAll(files) {
  if (!files.length) return;
  requireConnected();
  const base = ui.cwd === "/" ? "" : ui.cwd;
  const accounts = ui.state.accounts.length;
  const total = files.reduce((sum, item) => sum + item.file.size, 0);
  try {
    for (const { file, rel } of files) {
      const bar = transfer(`uploading ${rel}`, (idx) => idx % accounts);
      await tracked(bar, call("upload", file, `${base}/${rel}`));
    }
  } finally {
    try { await refresh(); } catch (err) { notice(err.message, true); }
  }
  notice(`uploaded ${plural(files.length, "file")}, ${formatBytes(total)}`);
}

function pick(folder) {
  return new Promise((resolve) => {
    const input = $(folder ? "pick-folder" : "pick-files");
    input.onchange = () => {
      const files = [...input.files].map((file) => ({ file, rel: file.webkitRelativePath || file.name }));
      input.value = "";
      resolve(files);
    };
    input.oncancel = () => resolve([]);
    input.click();
  });
}

async function walkEntry(entry, prefix, files) {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    files.push({ file, rel: prefix + entry.name });
    return;
  }
  const reader = entry.createReader();
  for (;;) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) return;
    for (const child of batch) await walkEntry(child, prefix + entry.name + "/", files);
  }
}

async function filesFromDrop(roots, plain) {
  const found = roots.filter(Boolean);
  if (!found.length) return plain.map((file) => ({ file, rel: file.name }));
  const files = [];
  for (const root of found) await walkEntry(root, "", files);
  return files;
}

async function runSearch() {
  const query = $("q").value.trim();
  const type = $("opt-type").dataset.value;
  $("search-note").textContent = "";
  if (!query && !type) {
    ui.found = null;
    return renderList();
  }
  try {
    ui.found = JSON.parse(await call("search", query, $("opt-exact").checked, $("opt-regex").checked, type));
  } catch (err) {
    $("search-note").textContent = err.message;
    return;
  }
  renderList();
}

function setCreatingFromNoVault() {
  setCreating(true);
  throw new Error("no vault under this account yet. repeat the password to create one.");
}

$("google").onclick = () => run(async () => {
  const email = await Auth.connect(ui.email || "");
  $("gate-note").textContent = "";
  showUnlock(email);
}, "gate", "waiting for google");

$("own-id-toggle").onclick = () => {
  const opening = $("own-id-form").hidden;
  $("own-id-form").hidden = !opening;
  $("own-id-toggle").textContent = toggleLabel(opening);
  if (opening) $("own-id-input").focus();
};

$("own-id-form").onsubmit = (event) => {
  event.preventDefault();
  const value = $("own-id-input").value.trim();
  if (!/^[\w.-]+\.apps\.googleusercontent\.com$/.test(value)) {
    $("gate-note").textContent = "that does not look like a google client id";
    return;
  }
  Auth.setClientId(value);
  location.reload();
};

$("own-id-reset").onclick = () => {
  Auth.setClientId("");
  location.reload();
};

$("switch").onclick = () => {
  Auth.forget();
  Keep.clear();
  $("unlock").hidden = true;
  $("connect-step").hidden = true;
  $("about").hidden = false;
  $("signin").hidden = !Auth.clientId;
  $("own-id").hidden = false;
  $("gate-note").textContent = "";
  ui.email = "";
};

$("mode").onclick = () => setCreating(!ui.creating);

$("unlock").onsubmit = (event) => {
  event.preventDefault();
  run(async () => {
    setNote("");
    const password = $("password").value;
    try {
      await Auth.ensure(ui.email);
    } catch (err) {
      $("signin").hidden = false;
      throw err;
    }
    if (!password) throw new Error("enter your password");
    if (ui.creating) {
      if (password.length < 10) throw new Error("use at least 10 characters");
      if (password !== $("password2").value) throw new Error("passwords do not match");
      await call("create", password, JSON.stringify([ui.email]));
      await Tabs.claim(ui.email);
      await enterApp();
    } else {
      try {
        await call("open_vault", password, JSON.stringify([ui.email]));
      } catch (err) {
        if (err.message.includes("no vault found")) setCreatingFromNoVault();
        throw err;
      }
      await afterUnlock();
    }
  }, "gate", ui.creating ? "creating your vault" : "checking your password");
};

const Tabs = {
  held: new Map(),
  async claim() {
    if (this.held.has("vault") || !navigator.locks) return true;
    for (let attempt = 0; attempt < 4; attempt++) {
      const got = await new Promise((resolve) => {
        navigator.locks.request("drivenet:vault", { ifAvailable: true }, (lock) => {
          if (!lock) {
            resolve(false);
            return null;
          }
          return new Promise((release) => {
            this.held.set("vault", release);
            resolve(true);
          });
        });
      });
      if (got) return true;
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    return false;
  },
  release() {
    const release = this.held.get("vault");
    if (release) release();
    this.held.delete("vault");
  },
};

async function beginSession() {
  setBusy("syncing vault copies");
  if (!(await Tabs.claim(ui.email))) {
    throw new Error("this vault is open in another tab of this browser. close that tab first");
  }
  try {
    await call("begin_session", Auth.browserId() + "-");
  } catch (err) {
    Tabs.release(ui.email);
    throw err;
  }
  await enterApp();
}

async function afterUnlock() {
  setBusy("reading your accounts");
  ui.state = JSON.parse(await call("state"));
  const missing = ui.state.accounts.filter((email) => !Auth.isConnected(email));
  if (!missing.length) return beginSession();
  setBusy("");
  $("unlock").hidden = true;
  $("connect-step").hidden = false;
  const box = $("missing");
  box.replaceChildren();
  for (const email of missing) {
    const row = element("div", "miss");
    const status = element("span", "dim", "not connected");
    const button = textButton("connect", () => run(async () => {
      await Auth.connect(email);
      button.hidden = true;
      status.textContent = "connected";
      if (ui.state.accounts.every((account) => Auth.isConnected(account))) await beginSession();
    }, "gate", "waiting for google"), "btn");
    row.append(element("span", "email", email), status, button);
    box.append(row);
  }
}

$("use-password").onclick = () => {
  setPasswordMode(true);
  setNote("");
  $("password").focus();
};

$("resume").onclick = () => run(async () => {
  try {
    await Auth.ensure(ui.email);
  } catch (err) {
    $("signin").hidden = false;
    throw err;
  }
  const saved = await Keep.load();
  if (!saved || saved.email !== ui.email) throw new Error("no saved session. enter your password");
  await openSaved(saved, ui.email);
  await afterUnlock();
}, "gate", "signing in with google");

$("connect-skip").onclick = () => run(beginSession, "gate", "opening your vault");

$("connect-cancel").onclick = () => {
  call("abandon");
  $("connect-step").hidden = true;
  $("unlock").hidden = false;
  $("gate-note").textContent = "";
  $("password").value = "";
  $("password").focus();
};

$("lock").onclick = () => run(async () => {
  clearInterval(ui.heartbeat);
  try {
    await call("release");
  } finally {
    await Keep.clear();
    Tabs.release(ui.email);
    ui.state = null;
    $("app").hidden = true;
    $("gate").hidden = false;
    $("notice").textContent = "";
    $("transfers").replaceChildren();
    syncDock();
    $("password").focus();
  }
});

function openSettings(open) {
  $("settings-panel").hidden = !open;
  $("scrim").hidden = !open;
}

$("settings-toggle").onclick = () => openSettings($("settings-panel").hidden);
$("settings-close").onclick = () => openSettings(false);
$("scrim").onclick = () => openSettings(false);

$("clean").onclick = () => run(async () => {
  requireConnected();
  const removed = await call("clean_orphans");
  await refresh();
  notice(removed ? `removed ${plural(removed, "unused block")}` : "nothing to clean up");
});

$("up-files").onclick = () => run(async () => uploadAll(await pick(false)));
$("up-folder").onclick = () => run(async () => uploadAll(await pick(true)));

const scheduleSearch = () => {
  clearTimeout(ui.searchTimer);
  ui.searchTimer = setTimeout(runSearch, 180);
};
$("search").onsubmit = (event) => event.preventDefault();
for (const id of ["q", "opt-exact", "opt-regex"]) $(id).addEventListener("input", scheduleSearch);
makeSelect($("opt-type"), scheduleSearch);

$("add-account").onclick = () => run(async () => {
  const email = await Auth.connect("");
  await call("add_account", email);
  await refresh();
  notice(`added ${email}`);
});

$("pw-toggle").onclick = () => {
  $("pw-form").hidden = !$("pw-form").hidden;
  if (!$("pw-form").hidden) $("pw-new").focus();
};
$("pw-cancel").onclick = () => {
  $("pw-form").hidden = true;
  $("pw-new").value = $("pw-new2").value = "";
};
$("pw-form").onsubmit = (event) => {
  event.preventDefault();
  run(async () => {
    const first = $("pw-new").value;
    if (first.length < 10) throw new Error("use at least 10 characters");
    if (first !== $("pw-new2").value) throw new Error("passwords do not match");
    await call("change_password", first);
    $("pw-cancel").click();
    notice("password changed");
  });
};

const press = (selector, index = 0) => {
  const buttons = document.querySelectorAll(`#list .row.sel ${selector}`);
  if (buttons[index]) buttons[index].click();
};

document.addEventListener("keydown", (event) => {
  if ($("app").hidden || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key === "Escape" && !$("settings-panel").hidden) return openSettings(false);
  if (["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) {
    if (event.key === "Escape" && event.target === $("q")) {
      $("q").value = "";
      $("q").blur();
      runSearch();
    }
    return;
  }
  if (event.target.closest(".select")) return;
  const askingDelete = document.querySelector("#list .row.sel .yes");
  const keys = {
    "/": () => $("q").focus(),
    ArrowDown: () => select(ui.sel + 1),
    j: () => select(ui.sel + 1),
    ArrowUp: () => select(ui.sel - 1),
    k: () => select(ui.sel - 1),
    ArrowRight: () => press(".link"),
    Backspace: () => go(ui.found ? ui.cwd : parentOf(ui.cwd)),
    ArrowLeft: () => go(ui.found ? ui.cwd : parentOf(ui.cwd)),
    d: () => press(".acts button"),
    x: () => press(".danger"),
    y: () => askingDelete && press(".yes"),
    n: () => askingDelete && press(".acts button", 1),
    u: () => $("up-files").click(),
  };
  if (event.key === "Enter" && event.target === document.body) return press(".link");
  const action = keys[event.key];
  if (!action) return;
  event.preventDefault();
  action();
});

let dragDepth = 0;
const canDrop = () => ui.state && !$("app").hidden;
document.addEventListener("dragenter", (event) => {
  event.preventDefault();
  if (!canDrop()) return;
  dragDepth++;
  $("drop-to").textContent = `into ${ui.cwd}`;
  $("drop").hidden = false;
});
document.addEventListener("dragover", (event) => event.preventDefault());
document.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $("drop").hidden = true;
});
document.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  $("drop").hidden = true;
  if (!canDrop()) return;
  const transfer = event.dataTransfer;
  const roots = [...transfer.items].map((item) => item.webkitGetAsEntry && item.webkitGetAsEntry());
  const plain = [...transfer.files];
  run(async () => uploadAll(await filesFromDrop(roots, plain)));
});

startDemo();

if (document.fonts) document.fonts.load("1em mask", "•");

let refreshing = false;
let lastRefreshWarning = 0;
async function keepFresh() {
  if (refreshing || !ui.state || $("app").hidden) return;
  const account = ui.state.accounts.find((email) => Auth.knows(email) && Auth.expiresIn(email) < 10 * 60 * 1000);
  if (!account) return;
  refreshing = true;
  try {
    await Auth.ensure(account, 10 * 60 * 1000);
  } catch (err) {
    if (Date.now() - lastRefreshWarning > 120000) {
      lastRefreshWarning = Date.now();
      notice(`google sign-in for ${account} could not refresh (${err.message})`, true);
    }
  } finally {
    refreshing = false;
  }
}
document.addEventListener("pointerdown", keepFresh, true);
document.addEventListener("keydown", keepFresh, true);

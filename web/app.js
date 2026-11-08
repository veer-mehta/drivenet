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
  if (data.type === "boot") return;
  if (data.type === "progress") return ui.onProgress && ui.onProgress(data.done, data.total, data.idx);
  if (data.id === 0) {
    if (!data.ok) return ($("boot").textContent = data.error);
    await call("start", Auth.apiBase);
    return showGate();
  }
  const waiter = pending.get(data.id);
  pending.delete(data.id);
  data.ok ? waiter.resolve(data.result) : waiter.reject(new Error(data.error));
};

const capitalize = (text) => (/^\S*@/.test(text) ? text : text.charAt(0).toUpperCase() + text.slice(1));
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
}

function notice(text, isError = false) {
  clearTimeout(ui.noticeTimer);
  $("notice").textContent = capitalize(text);
  $("notice").className = isError ? "err" : "";
  if (!isError) ui.noticeTimer = setTimeout(() => { $("notice").textContent = ""; syncDock(); }, 6000);
  syncDock();
}

async function run(action, target = "app") {
  if (ui.busy) {
    notice("Wait for the current job to finish.", true);
    return false;
  }
  ui.busy = true;
  document.body.classList.add("busy");
  try {
    await action();
    return true;
  } catch (err) {
    if (target === "gate") $("gate-note").textContent = capitalize(err.message);
    else notice(err.message, true);
    return false;
  } finally {
    ui.busy = false;
    document.body.classList.remove("busy");
  }
}

function requireConnected() {
  const missing = ui.state.accounts.filter((email) => !Auth.isConnected(email));
  if (missing.length) throw new Error(`Connect ${missing.join(", ")} first (Accounts, on the right)`);
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
  };
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function showGate() {
  $("boot").hidden = true;
  $("gate").hidden = false;
  $("origin").textContent = location.origin;
  $("own-id").hidden = Boolean(Auth.mock);
  $("own-id-reset").hidden = !Auth.ownClientId;
  $("own-id-state").textContent = Auth.ownClientId ? "Using your own client ID." : "";
  if (Auth.mock) {
    $("mock-box").hidden = false;
    $("signin").hidden = false;
  } else if (!Auth.clientId) {
    $("setup").hidden = false;
  } else {
    $("signin").hidden = false;
  }
}

function showUnlock(email) {
  ui.email = email;
  $("who").textContent = email;
  $("signin").hidden = true;
  $("own-id").hidden = true;
  $("unlock").hidden = false;
  setCreating(false);
  $("password").focus();
}

function setCreating(creating) {
  ui.creating = creating;
  $("repeat-box").hidden = !creating;
  $("unlock-btn").textContent = creating ? "Create vault" : "Unlock";
  $("mode").textContent = creating ? "I have a vault" : "New vault";
}

async function enterApp() {
  $("password").value = "";
  $("password2").value = "";
  $("gate-note").textContent = "";
  $("gate").hidden = true;
  $("app").hidden = false;
  ui.cwd = "/";
  ui.found = null;
  $("q").value = "";
  await refresh();
  clearInterval(ui.heartbeat);
  ui.heartbeat = setInterval(() => call("renew").catch((err) => notice(err.message, true)), 30000);
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
  renderStatus();
}

function renderStatus() {
  const { accounts, files } = ui.state;
  const stored = formatBytes(files.reduce((sum, file) => sum + file.size, 0));
  $("status-left").textContent = `${ui.email}  ${ui.cwd}`;
  $("status-right").textContent = `${plural(accounts.length, "account")}  ·  ${plural(files.length, "file")}  ·  ${stored}`;
}

function go(path) {
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
  const label = flat ? item.path : item.name + (dir ? "/" : "");
  const name = dir || flat ? textButton(label, () => go(dir ? item.path : parentOf(item.path)), "link name" + (dir ? " dir" : "")) : element("span", "name", label);
  const strip = cellRow(dir ? [] : sample(item.shards, 10), "strip");
  if (!dir) strip.title = plural(item.shards.length, "block");
  const actions = element("span", "acts");
  const idle = () => {
    actions.replaceChildren(
      textButton(dir ? "Download .zip" : "Download", () => download(item)),
      textButton("Delete", () => {
        actions.replaceChildren(
          element("span", "ask", "Delete?"),
          textButton("Yes", () => remove(item), "yes"),
          textButton("No", idle),
        );
      }, "danger"),
    );
  };
  idle();
  row.append(name, strip, element("span", "size", formatBytes(item.size)), actions);
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
      up.append(textButton("..", () => go(parentOf(ui.cwd)), "link name dir"), element("span"), element("span"), element("span"));
      list.append(up);
    }
  }
  if (!rows.length) {
    const none = ui.found ? "No matches." : ui.cwd === "/" ? "Nothing here yet. Drop files anywhere, or use Upload." : "Empty folder.";
    list.append(element("div", "empty", none));
    return;
  }
  rows.forEach((row) => list.append(makeRow(row, flat)));
}

function renderAccounts() {
  const list = $("accounts");
  list.replaceChildren();
  const { accounts, usage } = ui.state;
  const counts = accounts.map((email) => usage[email] || 0);
  const peak = Math.max(1, ...counts);
  accounts.forEach((email, index) => {
    const connected = Auth.isConnected(email);
    const item = element("li", "account");
    const meta = element("div", "meta");
    meta.append(`${plural(counts[index], "block")} · `, element("span", connected ? "" : "off", connected ? "connected" : "not connected"));
    const width = counts[index] ? Math.max(1, Math.round((counts[index] / peak) * 24)) : 0;
    const bars = element("span", "bars");
    bars.append(cellRow(Array(width).fill(index)));
    const small = element("div", "small");
    const idle = () => {
      small.replaceChildren();
      if (!connected) {
        small.append(textButton("Connect", () => run(async () => {
          await Auth.connect(email);
          renderAccounts();
        })));
      }
      small.append(textButton("Remove", () => {
        small.replaceChildren(
          element("span", "ask", "Remove from vault?"),
          textButton("Yes", () => run(async () => {
            await call("remove_account", email);
            await refresh();
            notice(`Removed ${email}`);
          }), "ask"),
          textButton("No", idle),
        );
      }));
    };
    idle();
    item.append(element("div", "email", email), meta, bars, small);
    list.append(item);
  });
}

async function download(item) {
  run(async () => {
    requireConnected();
    const dir = item.type === "dir";
    const name = item.path.split("/").pop();
    const bar = transfer(`Downloading ${item.name || name}`, dir ? undefined : (idx) => accountIndex(item.shards[idx]));
    const blob = await call(dir ? "download_folder" : "download", item.path);
    bar.finish();
    saveBlob(blob, dir ? name + ".zip" : name);
  });
}

function remove(item) {
  run(async () => {
    requireConnected();
    const dir = item.type === "dir";
    const bar = transfer(`Deleting ${item.name || item.path}`, dir ? undefined : (idx) => accountIndex(item.shards[idx]));
    await call(dir ? "delete_folder" : "delete_file", item.path);
    bar.finish();
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
  for (const { file, rel } of files) {
    const bar = transfer(`Uploading ${rel}`, (idx) => idx % accounts);
    await call("upload", file, `${base}/${rel}`);
    bar.finish();
  }
  await refresh();
  notice(`Uploaded ${plural(files.length, "file")}, ${formatBytes(total)}`);
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
  const type = $("opt-type").value;
  $("search-note").textContent = "";
  if (!query && !type) {
    ui.found = null;
    return renderList();
  }
  try {
    ui.found = JSON.parse(await call("search", query, $("opt-exact").checked, $("opt-regex").checked, type));
  } catch (err) {
    $("search-note").textContent = capitalize(err.message);
    return;
  }
  renderList();
}

function setCreatingFromNoVault() {
  setCreating(true);
  throw new Error("No vault under this account yet. Repeat the password to create one.");
}

$("google").onclick = () => run(async () => {
  const email = await Auth.connect(Auth.mock ? $("mock-email").value.trim() : "");
  if (!email) throw new Error("Enter an email");
  $("gate-note").textContent = "";
  showUnlock(email);
}, "gate");

$("own-id-toggle").onclick = () => {
  $("own-id-form").hidden = !$("own-id-form").hidden;
  if (!$("own-id-form").hidden) $("own-id-input").focus();
};

$("own-id-form").onsubmit = (event) => {
  event.preventDefault();
  const value = $("own-id-input").value.trim();
  if (!/^[\w.-]+\.apps\.googleusercontent\.com$/.test(value)) {
    $("gate-note").textContent = "That doesn't look like a client ID";
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
  $("unlock").hidden = true;
  $("signin").hidden = !Auth.clientId && !Auth.mock;
  $("own-id").hidden = Boolean(Auth.mock);
  $("gate-note").textContent = "";
  ui.email = "";
};

$("mode").onclick = () => setCreating(!ui.creating);

$("unlock").onsubmit = (event) => {
  event.preventDefault();
  run(async () => {
    $("gate-note").textContent = "";
    const password = $("password").value;
    if (ui.creating) {
      if (password.length < 8) throw new Error("Use at least 8 characters");
      if (password !== $("password2").value) throw new Error("Passwords don't match");
      await call("create", password, JSON.stringify([ui.email]));
    } else {
      try {
        await call("open_vault", password, JSON.stringify([ui.email]));
      } catch (err) {
        if (err.message.includes("no vault found")) setCreatingFromNoVault();
        throw err;
      }
    }
    await enterApp();
  }, "gate");
};

$("lock").onclick = () => run(async () => {
  clearInterval(ui.heartbeat);
  try {
    await call("release");
  } finally {
    ui.state = null;
    $("app").hidden = true;
    $("gate").hidden = false;
    $("notice").textContent = "";
    $("transfers").replaceChildren();
    syncDock();
    $("password").focus();
  }
});

$("up-files").onclick = () => run(async () => uploadAll(await pick(false)));
$("up-folder").onclick = () => run(async () => uploadAll(await pick(true)));

$("search").onsubmit = (event) => event.preventDefault();
for (const id of ["q", "opt-exact", "opt-regex", "opt-type"]) {
  $(id).addEventListener("input", () => {
    clearTimeout(ui.searchTimer);
    ui.searchTimer = setTimeout(runSearch, 180);
  });
}

const addAccountBox = $("mock-new");
addAccountBox.hidden = !Auth.mock;
$("add-account").onclick = () => run(async () => {
  const email = await Auth.connect(Auth.mock ? $("mock-new-email").value.trim() : "");
  if (!email) throw new Error("Enter an email");
  await call("add_account", email);
  $("mock-new-email").value = "";
  await refresh();
  notice(`Added ${email}`);
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
    if (first.length < 8) throw new Error("Use at least 8 characters");
    if (first !== $("pw-new2").value) throw new Error("Passwords don't match");
    await call("change_password", first);
    $("pw-cancel").click();
    notice("Password changed");
  });
};

document.addEventListener("keydown", (event) => {
  if ($("app").hidden) return;
  const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName);
  if (event.key === "Escape" && event.target === $("q")) {
    $("q").value = "";
    $("q").blur();
    runSearch();
  } else if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey) {
    event.preventDefault();
    $("q").focus();
  }
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

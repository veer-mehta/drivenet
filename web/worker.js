importScripts("https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js");

const CORE_FILES = [
  "__init__.py", "fs_tree.py", "transport.py", "engine.py",
  "blockcrypt.py", "settings.py", "vault.py", "vault_sync.py", "drive_rest.py", "app.py",
];

const tokenWaiters = new Map();
let nextTokenId = 1;

function getToken(account) {
  return new Promise((resolve, reject) => {
    const id = nextTokenId++;
    tokenWaiters.set(id, { resolve, reject });
    self.postMessage({ type: "token", id, account });
  });
}

function onProgress(done, total, idx) {
  self.postMessage({ type: "progress", done, total, idx });
}

async function fetchText(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`could not load ${path} (${response.status})`);
  return response.text();
}

function boot(label) {
  self.postMessage({ type: "boot", text: label.padEnd(26, ".") + " OK" });
}

const ready = (async () => {
  const pyodide = await loadPyodide();
  boot("PYTHON RUNTIME");
  await pyodide.loadPackage("cryptography");
  boot("CRYPTO LIBRARY");
  pyodide.FS.mkdir("/home/pyodide/core");
  for (const name of CORE_FILES) {
    pyodide.FS.writeFile(`/home/pyodide/core/${name}`, await fetchText(`core/${name}`));
  }
  pyodide.FS.writeFile("/home/pyodide/bridge.py", await fetchText("bridge.py"));
  pyodide.runPython('import sys; sys.path.insert(0, "/home/pyodide")');
  boot("DRIVE CORE");
  return pyodide.pyimport("bridge");
})();

self.onmessage = async ({ data }) => {
  if (data.type === "token-reply") {
    const waiter = tokenWaiters.get(data.id);
    tokenWaiters.delete(data.id);
    data.error ? waiter.reject(new Error(data.error)) : waiter.resolve(data.token);
    return;
  }

  const { id, op, args } = data;
  try {
    const bridge = await ready;
    const result = op === "start"
      ? bridge.start(getToken, onProgress, args[0])
      : await bridge[op](...args);
    self.postMessage({ id, ok: true, result });
  } catch (err) {
    const message = String(err.message || err).trim().split("\n").pop().replace(/^(\w+\.)*\w+: /, "");
    self.postMessage({ id, ok: false, error: message });
  }
};

ready.then(
  () => self.postMessage({ id: 0, ok: true }),
  (err) => self.postMessage({ id: 0, ok: false, error: "failed to load python: " + err.message })
);

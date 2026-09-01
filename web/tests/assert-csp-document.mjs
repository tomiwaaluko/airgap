import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(root, "..");

function fail(message) {
  console.error(message);
  process.exit(1);
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address === null || typeof address === "string") {
        server.close();
        reject(new Error("could not bind"));
        return;
      }
      const port = address.port;
      server.close((error) => {
        if (error) {
          reject(error);
          return;
        }
        resolve(port);
      });
    });
    server.on("error", reject);
  });
}

function startServer(port) {
  return new Promise((resolve, reject) => {
    const nextBin = path.join(webRoot, "node_modules", "next", "dist", "bin", "next");
    const child = spawn(
      process.execPath,
      [nextBin, "start", "--hostname", "127.0.0.1", "--port", String(port)],
      {
        cwd: webRoot,
        stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, PORT: String(port) },
      },
    );
    let output = "";
    const onData = (chunk) => {
      output += chunk.toString();
      if (/Ready/i.test(output)) {
        child.stdout?.off("data", onData);
        child.stderr?.off("data", onData);
        resolve(child);
      }
    };
    child.stdout?.on("data", onData);
    child.stderr?.on("data", onData);
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code && code !== 0) {
        reject(new Error(`next start exited ${code}\n${output}`));
      }
    });
    setTimeout(() => {
      reject(new Error(`next start timed out\n${output}`));
    }, 20000);
  });
}

function extractNonce(csp) {
  const match = /'nonce-([^']+)'/.exec(csp);
  return match ? match[1] : null;
}

function scriptNonces(html) {
  return [...html.matchAll(/<script\b([^>]*)>/gi)].map((match) => {
    const attrs = match[1];
    const nonce = /\bnonce=["']([^"']+)["']/.exec(attrs);
    return { attrs: attrs.trim(), nonce: nonce ? nonce[1] : null };
  });
}

if (!existsSync(path.join(webRoot, ".next"))) {
  fail("tests/assert-csp-document.mjs requires a production build (.next). Run npm run build first.");
}

const port = await freePort();
const child = await startServer(port);
try {
  const response = await fetch(`http://127.0.0.1:${port}/`, {
    headers: { Accept: "text/html" },
  });
  if (!response.ok) {
    fail(`GET / returned ${response.status}`);
  }
  const csp = response.headers.get("content-security-policy") ?? "";
  if (!/default-src\s+'self'/.test(csp)) {
    fail(`CSP missing default-src 'self': ${csp}`);
  }
  if (/unsafe-inline|unsafe-eval/i.test(csp)) {
    fail(`CSP weakened: ${csp}`);
  }
  if (/script-src[^;]*https?:\/\//i.test(csp)) {
    fail(`CSP has a remote script origin: ${csp}`);
  }
  const nonce = extractNonce(csp);
  if (!nonce) {
    fail(`CSP has no nonce: ${csp}`);
  }
  const html = await response.text();
  const scripts = scriptNonces(html);
  if (scripts.length === 0) {
    fail("document has no <script> tags");
  }
  const unstamped = scripts.filter((script) => script.nonce !== nonce);
  if (unstamped.length > 0) {
    fail(
      `script nonce mismatch (csp nonce=${nonce}): ${JSON.stringify(unstamped)}`,
    );
  }
  console.log(`CSP document OK: ${scripts.length} script tags stamped nonce=${nonce}`);
  console.log(`CSP: ${csp}`);
} finally {
  child.kill();
}

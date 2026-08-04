import crypto from "node:crypto";
import fs from "node:fs";

function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
}
const [input, output] = process.argv.slice(2);
if (!input || !output) throw new Error("usage: node sign-update-manifest.mjs signed.json envelope.json");
const keyPem = process.env.TIANGONG_UPDATE_PRIVATE_KEY_PEM || (process.env.TIANGONG_UPDATE_PRIVATE_KEY_FILE ? fs.readFileSync(process.env.TIANGONG_UPDATE_PRIVATE_KEY_FILE, "utf8") : "");
const keyId = process.env.TIANGONG_UPDATE_KEY_ID || "";
if (!keyPem || !keyId) throw new Error("update signing key is not configured");
const signed = JSON.parse(fs.readFileSync(input, "utf8"));
const bytes = Buffer.from(canonical(signed), "utf8");
const sig = crypto.sign(null, bytes, keyPem).toString("base64");
fs.writeFileSync(output, JSON.stringify({ signed, signature: { key_id: keyId, sig } }, null, 2));

// Add someone to the AskAtlas allowlist.
//   node scripts/askatlas_user.mjs name@example.com            # makes a random access code
//   node scripts/askatlas_user.mjs name@example.com "their code"
// Prints the access code to give that person (once) and the JSON entry to merge into
// the ASKATLAS_USERS environment variable in Vercel. Only the hash is stored there.
import { randomBytes } from "node:crypto";
import { sha256 } from "../api/_lib/atlas.mjs";

const [email, given] = process.argv.slice(2);
if (!email || !email.includes("@")) { console.error("usage: node scripts/askatlas_user.mjs name@example.com [code]"); process.exit(1); }
const code = given || randomBytes(9).toString("base64url");
console.log(`access code for ${email}: ${code}`);
console.log(`ASKATLAS_USERS entry: ${JSON.stringify({ [email.trim().toLowerCase()]: sha256(code) })}`);

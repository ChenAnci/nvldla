// DeepSeek PoW (DeepSeekHashV1) solver — runs the official browser WASM module.
//
// Usage:
//   node pow_solver.mjs <challenge.json> [path-to-sha3_wasm_bg.wasm]
//
// challenge.json shape:
//   { "algorithm":"DeepSeekHashV1", "challenge":"...", "salt":"...",
//     "difficulty":N, "expire_at":N, "signature":"...", "target_path":"..." }
//
// Prints the integer answer on stdout; exits non-zero on failure.
// The glue mirrors the reference implementation (web-client.ts solveWithWasm).
import { readFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

function fail(msg) {
  process.stderr.write(msg + "\n");
  process.exit(1);
}

async function main() {
  const challengePath = process.argv[2];
  if (!challengePath) fail("usage: node pow_solver.mjs <challenge.json> [wasm]");
  const wasmPath = process.argv[3] || join(__dirname, "sha3_wasm_bg.wasm");

  let challenge;
  try {
    challenge = JSON.parse(readFileSync(challengePath, "utf8"));
  } catch (e) {
    fail("cannot read challenge json: " + e.message);
  }

  if (challenge.algorithm !== "DeepSeekHashV1") {
    fail("unsupported pow algorithm: " + challenge.algorithm);
  }

  let wasmBytes;
  try {
    wasmBytes = readFileSync(wasmPath);
  } catch (e) {
    fail("cannot read wasm: " + e.message);
  }

  const prefix = `${challenge.salt}_${challenge.expire_at}_`;

  const wasmModule = await WebAssembly.compile(new Uint8Array(wasmBytes));
  const instance = await WebAssembly.instantiate(wasmModule);
  const exports = instance.exports;
  const memory = exports.memory;
  const addToStack = exports.__wbindgen_add_to_stack_pointer;
  const alloc = exports.__wbindgen_export_0;
  const wasmSolve = exports.wasm_solve;

  const encoder = new TextEncoder();
  function writeString(str) {
    const bytes = encoder.encode(str);
    const ptr = alloc(bytes.length, 1);
    new Uint8Array(memory.buffer, ptr, bytes.length).set(bytes);
    return [ptr, bytes.length];
  }

  const retptr = addToStack(-16);
  const [challengePtr, challengeLen] = writeString(challenge.challenge);
  const [prefixPtr, prefixLen] = writeString(prefix);

  wasmSolve(
    retptr,
    challengePtr,
    challengeLen,
    prefixPtr,
    prefixLen,
    challenge.difficulty,
  );

  const resultView = new DataView(memory.buffer);
  const status = resultView.getInt32(retptr, true);
  const value = resultView.getFloat64(retptr + 8, true);
  addToStack(16);

  if (status === 0) fail("pow solve failed");
  process.stdout.write(String(Math.floor(value)));
}

main().catch((e) => fail("error: " + (e && e.stack ? e.stack : e)));

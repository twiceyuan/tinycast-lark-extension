<!-- Filed upstream: https://github.com/abue-ammar/tinycast/issues/1592 (2026-10-15) -->

# Issue draft for abue-ammar/tinycast

> Title: Raycast runtime: 5 Node-semantics gaps break `got`-based extensions (repro: Raycast "Lark Documents")

## Summary

Tinycast v0.11.19's built-in Node compatibility runtime (`RaycastRuntime.generated.js`) is missing several standard Node.js semantics. The Raycast extension **Lark Documents** (which uses `got` 12.6 as its HTTP client) hits them one after another — after fixing each layer locally (by patching the extension's bundled `node_modules`), the next one surfaces. All five are fixed in https://github.com/twiceyuan/tinycast-lark-extension (script + write-up); details below so they can be fixed properly in the runtime.

## Repro

1. Install Tinycast 0.11.19, add the Raycast store extension **Lark Documents** (v0.0.10).
2. Run "Search Documents". Login QR flow fails / hangs.

## Findings

### 1. `EventEmitter` is missing `prependOnceListener`

`Scripts/raycast-runtime/src/events.js` implements `prependListener` but not `prependOnceListener` (standard since forever in Node). `got`'s dependency `@szmarczak/http-timer` calls it on every request, so **every `got`-based extension crashes** with `TypeError: n.prependOnceListener is not a function`.
Fix: mirror `once` + `prependListener`.

### 2. `ClientRequest.end()` doesn't support Node's callback overloads

`node-shims.js` implements `end(chunk)` and forwards the first argument to `write()` → `Buffer.from(chunk)`. got legally calls `request.end(errorFirstCallback)` from its stream `_final`, so the callback function lands in `Buffer.from` → `Buffer.from: unsupported input`.
Also note: the HTTP `ClientRequest` never emits `"finish"` (only child_process streams do), so a fix should fire the callback per Node semantics (on finish) — today there is no finish to attach to.
Fix: implement `end([chunk][, encoding][, callback])` overloads.

### 3. `Readable.read()` never emits `end` in paused mode

`streams.js` only schedules the end-emission microtask from `push()`, `resume()`, `on('data')`, `on('readable')`. When a consumer arrives late and drains an already-EOF'd stream via `read()` (exactly what got does: `while ((data = response.read()) !== null)`), nothing re-schedules, and `end` is never emitted (Node has emitEOF logic in the read path). The response then "never ends" from got's perspective.
Fix: after a draining `read()` of an ended stream, schedule the end emission.

### 4. `_pull()` deadlocks on `highWaterMark: 0`

got constructs its Request Duplex with `super({ autoDestroy: false, highWaterMark: 0 })` (intentional, pull-based). The runtime's guard `if (state.length >= state.highWaterMark) return` makes `0 >= 0` always true, so `_read()` is never called and nothing is ever consumed.
Fix: special-case non-positive highWaterMark (e.g. treat as 1, or bypass the check like Node does).

### 5. `stream.pipeline()` throws while piping a got Request, and the rejection is swallowed

`get-stream` reads response bodies via `promisify(stream.pipeline)(gotRequest, collector)`. The runtime's pipeline (`Uu`) throws `TypeError: Right hand side of instanceof is not an object` from inside `a.reduce((a,b) => a.pipe(b))` (`RaycastRuntime.generated.js:20:35610`, at the `y.pipe(b)` call expression). I could not pin down which `instanceof` explodes from the minified bundle — but it reproduces 100% with a got Request (a runtime `Duplex` subclass with hwm 0) piped into a `PassThrough`.
The rejection is then swallowed three times (get-stream catch → got `_setRawBody` empty catch → `await x && emit` short-circuit), so the promise never settles: the extension shows an **eternal spinner with no error**.
Fix: track down the instanceof in the pipe path; making `pipeline()` robust for Duplex→PassThrough would cover the most common extension pattern.

## Suggested verification

With the patches from the repo above applied, Lark Documents works fully on 0.11.19: QR login renders, sign-in completes, document search works. The patch script (`scripts/tinycast-lark-fix.py`) is a precise, commented specification of each fix.

Happy to provide additional traces from `/tmp/tc_bufdiag.log` instrumentation if useful. Thanks for Tinycast — the Raycast-extension support is a great feature, and these fixes would make it solid for the large fraction of store extensions that use `got`.

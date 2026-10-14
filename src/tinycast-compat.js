/*
 * Tinycast compatibility layer.
 *
 * Tinycast (https://github.com/abue-ammar/tinycast) runs Raycast extensions on
 * its own Node.js-compatibility runtime, which — as of v0.11.19 — is missing a
 * few standard semantics that `got` (and its transitive dependencies) rely on.
 * This module applies the fixes at module level. Every fix is guarded, so on a
 * real Node runtime (Raycast, `ray develop`) or a future fixed Tinycast this is
 * a no-op.
 *
 * Import this module FIRST in every command entry, before anything that can
 * pull in `got`.
 *
 * The five gaps (full write-up: docs/diagnosis.md in the repo this file ships in):
 *   1. EventEmitter is missing prependOnceListener (used by @szmarczak/http-timer).
 *   2. ClientRequest.end() lacks Node's callback overloads — end(fn) would hand
 *      the function to Buffer.from; the runtime's HTTP ClientRequest also never
 *      emits "finish", so the callback is fired on a microtask instead.
 *   3. Readable.read() never emits "end" in paused mode once a late consumer
 *      drains an already-EOF'd stream (got consumes responses exactly so).
 *   4. Readable._pull() bails on `length >= highWaterMark`, which is always
 *      true for got's `highWaterMark: 0` request streams — _read() never runs.
 *   5. stream.pipeline() throws while piping got Request streams and the
 *      rejection is swallowed upstream (get-stream → got), leaving got's
 *      promise pending forever: an eternal spinner with no error. get-stream's
 *      buffer() is replaced with a plain flowing-mode collector instead.
 */
(function () {
  // 1) EventEmitter.prototype.prependOnceListener
  try {
    const events = require("node:events");
    const EE = events && (events.EventEmitter || (events.default && events.default.EventEmitter));
    if (EE && typeof EE.prototype.prependOnceListener !== "function") {
      Object.defineProperty(EE.prototype, "prependOnceListener", {
        value: function (event, listener) {
          const wrapper = (...args) => {
            this.off(event, wrapper);
            listener.apply(this, args);
          };
          wrapper.listener = listener;
          return this.prependListener(event, wrapper);
        },
        writable: true,
        configurable: true,
      });
    }
  } catch {}

  // 2) ClientRequest.end([chunk][, encoding][, callback]) — function arguments
  // are callbacks per Node, never body data. Also cover the case where the
  // runtime's request objects are created before this module loads.
  try {
    let http;
    try {
      http = require("node:http");
    } catch {
      http = require("http");
    }
    const ClientRequest = http && http.ClientRequest;
    if (ClientRequest && ClientRequest.prototype && typeof ClientRequest.prototype.end === "function" && !ClientRequest.prototype.end.__tinycastEndFix) {
      const origEnd = ClientRequest.prototype.end;
      ClientRequest.prototype.end = function (a, b, c) {
        let callback;
        if (typeof a === "function") {
          callback = a;
          a = undefined;
        } else if (typeof b === "function") {
          callback = b;
          b = undefined;
        } else if (typeof c === "function") {
          callback = c;
        }
        const result =
          a === undefined ? origEnd.call(this) : b === undefined ? origEnd.call(this, a) : origEnd.call(this, a, b);
        if (typeof callback === "function") queueMicrotask(() => callback());
        return result;
      };
      ClientRequest.prototype.end.__tinycastEndFix = true;
    }
    // Belt and suspenders: guarantee the EventEmitter surface on every request
    // the runtime hands out, even if its class slipped past the polyfill above.
    if (http && typeof http.request === "function" && !http.request.__tinycastRequestFix) {
      const origRequest = http.request;
      http.request = function (...args) {
        const request = origRequest.apply(this, args);
        if (request && typeof request.prependOnceListener !== "function") {
          request.prependOnceListener = function (event, listener) {
            const wrapper = (...a) => {
              this.off(event, wrapper);
              listener.apply(this, a);
            };
            wrapper.listener = listener;
            return this.prependListener(event, wrapper);
          };
        }
        return request;
      };
      http.request.__tinycastRequestFix = true;
    }
  } catch {}

  // 3) Readable.read(): after a read that drains an already-ended stream,
  // re-schedule so the runtime emits "end" (Node's emitEOF behavior).
  // 4) Readable._pull(): treat non-positive highWaterMark as 1 so got's
  // `highWaterMark: 0` request streams actually start consuming.
  try {
    const stream = require("stream");
    const proto = stream.Readable && stream.Readable.prototype;
    if (proto && typeof proto.read === "function" && !proto.__tinycastReadEndFix) {
      const origRead = proto.read;
      proto.read = function () {
        const result = origRead.apply(this, arguments);
        try {
          const state = this._readableState;
          if (
            state &&
            state.ended &&
            !state.buffer.length &&
            !state.endEmitted &&
            typeof this._schedule === "function" &&
            !state.scheduled
          ) {
            this._schedule();
          }
        } catch {}
        return result;
      };
      proto.__tinycastReadEndFix = true;
    }
    if (proto && typeof proto._pull === "function" && !proto.__tinycastHwmFix) {
      const origPull = proto._pull;
      proto._pull = function () {
        const state = this && this._readableState;
        if (state && !(state.highWaterMark > 0)) state.highWaterMark = 1;
        return origPull.apply(this, arguments);
      };
      proto.__tinycastHwmFix = true;
    }
  } catch {}

  // 5) get-stream.buffer(): collect via plain data/end/error events. Must run
  // before got first loads (it destructures `buffer` from the module).
  try {
    let getStream;
    try {
      getStream = require("get-stream");
    } catch {}
    const mod = getStream && (typeof getStream.buffer === "function" ? getStream : getStream.default);
    if (mod && typeof mod.buffer === "function" && !mod.buffer.__tinycastBypass) {
      mod.buffer = function (stream) {
        return new Promise((resolve, reject) => {
          const chunks = [];
          let total = 0;
          let settled = false;
          const done = (error) => {
            if (settled) return;
            settled = true;
            if (error) reject(error);
            else resolve(Buffer.concat(chunks, total));
          };
          stream.on("data", (chunk) => {
            if (typeof chunk === "string") chunk = Buffer.from(chunk);
            else if (!Buffer.isBuffer(chunk) && !(chunk instanceof Uint8Array)) chunk = Buffer.from(String(chunk));
            chunks.push(chunk);
            total += chunk.length;
          });
          stream.once("end", () => done(null));
          stream.once("error", done);
          stream.once("aborted", () => done(new Error("request aborted")));
          stream.once("close", () => {
            if (!settled) done(new Error("stream closed before end"));
          });
          if (stream.readableEnded) done(null);
        });
      };
      mod.buffer.__tinycastBypass = true;
    }
  } catch {}
})();

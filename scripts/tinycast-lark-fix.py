#!/usr/bin/env python3
"""
tinycast-lark-fix.py — patch the Raycast "Lark Documents" extension so it works
inside Tinycast (https://github.com/abue-ammar/tinycast).

Tinycast ships its own Node.js-compatibility runtime for Raycast extensions
(Resources/RaycastRuntime.generated.js). That runtime is missing several standard
Node semantics that `got` (the HTTP client used by the Lark extension) relies on.
This script patches the extension's bundled node_modules in place:

  1. EventEmitter.prependOnceListener — missing in the runtime's events shim;
     got's dependency @szmarczak/http-timer calls it on every request.
     Fix: guard the call sites + add the real method via require("node:events").

  2. ClientRequest.end(callback) — the runtime treats the first end() argument
     as body data, so got's legal `request.end(errorFirstCallback)` (from its
     stream `_final`) crashes with "Buffer.from: unsupported input".
     Fix: normalize Node's end() overloads and fire the callback via
     queueMicrotask (the runtime's HTTP ClientRequest never emits "finish").

  3. Readable.read() EOF emission — in paused mode, once the consumer drains a
     stream whose EOF was already pushed, the runtime never emits "end"
     (Node re-schedules via emitEOF). got waits for the response "end" forever.
     Fix: re-schedule the end emission after a draining read().

  4. highWaterMark: 0 deadlock — got constructs its Request Duplex with
     `highWaterMark: 0` (intentionally: pull-based, no buffering). The runtime's
     _pull() guard `length >= highWaterMark` makes `0 >= 0` always bail out,
     so `_read()` is never called and nothing is ever consumed.
     Fix: treat non-positive highWaterMark as 1 in _pull().

  5. stream.pipeline crash — the runtime's pipeline() throws
     "Right hand side of instanceof is not an object" while piping a got Request
     to a PassThrough, and get-stream swallows the rejection, so the got promise
     never settles (eternal spinner, no error).
     Fix: replace get-stream's buffer() with a plain flowing-mode collector that
     avoids pipeline/pipe entirely.

The script is idempotent — run it again after the extension (or Tinycast) updates
and re-downloads node_modules. Originals are backed up once as *.bak-tcfix.

Usage:  python3 tinycast-lark-fix.py [extension-dir]
        (extension-dir defaults to the standard Tinycast install location)
"""

import os
import sys
import shutil

DEFAULT_DIR = os.path.expanduser(
    "~/Library/Application Support/com.tinycast.app/extensions/lark"
)

# ---------------------------------------------------------------------------
# injected code blocks (run inside the extension's JS context)
# ---------------------------------------------------------------------------

# [1] EventEmitter.prependOnceListener polyfill + [2] end() overload fix +
# [3] read() EOF re-emission + [4] highWaterMark<=0 fix + [5] Buffer.from guard.
GOT_HEAD_LINES = [
    # [2] ClientRequest.end(): strip function args (callbacks per Node overloads),
    # call the original end with data args only, fire the callback on a microtask.
    'try{(function(){var _h=null;try{_h=require("node:http")}catch(_1){try{_h=require("http")}catch(_2){return}}var _CR=_h&&_h.ClientRequest;if(_CR&&_CR.prototype&&typeof _CR.prototype.end==="function"&&!_CR.prototype.end.__tcEndFix){var _oe=_CR.prototype.end;_CR.prototype.end=function(a,b,c){var cb;if(typeof a==="function"){cb=a;a=void 0}else if(typeof b==="function"){cb=b;b=void 0}else if(typeof c==="function"){cb=c}var r=a===void 0?_oe.call(this):(b===void 0?_oe.call(this,a):_oe.call(this,a,b));if(typeof cb==="function")queueMicrotask(function(){cb()});return r};_CR.prototype.end.__tcEndFix=true}})()}catch(_){}',
    # [3] Readable.read(): after a read that drains the buffer of an already-ended
    # stream, re-schedule so the runtime emits "end" (Node emitEOF semantics).
    'try{(function(){var _st=require("stream");var _R=_st&&_st.Readable;var _rp=_R&&_R.prototype;if(_rp&&_rp.read&&!_rp.__tcReadEndFix){var _or=_rp.read;_rp.read=function(){var r=_or.apply(this,arguments);try{var s=this._readableState;if(s&&s.ended&&!s.buffer.length&&!s.endEmitted&&typeof this._schedule==="function"&&!s.scheduled)this._schedule()}catch(_){}return r};_rp.__tcReadEndFix=true}})()}catch(_){}',
    # [4] Readable._pull(): the runtime bails when length >= highWaterMark, which
    # is always true for got's highWaterMark:0 streams. Treat <=0 as 1.
    'try{(function(){var _st=require("stream");var _rp=_st.Readable.prototype;if(_rp._pull&&!_rp.__tcHwmFix){var _ol=_rp._pull;_rp._pull=function(){var s=this&&this._readableState;if(s&&!(_s.highWaterMark>0))s.highWaterMark=1;return _ol.apply(this,arguments)};_rp.__tcHwmFix=true}})()}catch(_){}',
    # [5] Buffer.from diagnostics: the runtime shim rejects more input types than
    # Node; when it does, surface the input kind + stack instead of a bare
    # "unsupported input" (writes one line to /tmp/tc_bufdiag.log on failure only).
    'try{(function(){var _B=require("buffer").Buffer;if(!_B||!_B.from||_B.from.__tcDiag)return;var _orig=_B.from;var _w=function(v,e,l){try{return _orig.call(_B,v,e,l)}catch(err){if(err&&/unsupported input/.test(String(err.message))){var kind=v===null?"null":v===undefined?"undefined":typeof v==="object"?((v.constructor&&v.constructor.name)||"object"):typeof v;var preview="";try{preview=typeof v==="object"?JSON.stringify(v):String(v)}catch(_){}var st=(err.stack||"").split("\\n").slice(1,5).join(" <- ");var detail="Buffer.from: unsupported input [kind="+kind+" preview="+String(preview).slice(0,80)+"] at "+String(st).slice(0,400);try{var _fs=require("fs");try{_fs.appendFileSync("/tmp/tc_bufdiag.log",new Date().toISOString()+" "+detail+"\\n")}catch(_2){_fs.writeFileSync("/tmp/tc_bufdiag.log",new Date().toISOString()+" "+detail+"\\n")}}catch(_3){}throw new TypeError(detail)}throw err}};_w.__tcDiag=true;_B.from=_w;if(globalThis.Buffer&&globalThis.Buffer!==_B&&globalThis.Buffer.from&&!globalThis.Buffer.from.__tcDiag){globalThis.Buffer.from=_w}})()}catch(_){}',
    # [1] EventEmitter.prototype.prependOnceListener (the runtime implements
    # prependListener but forgot this one; @szmarczak/http-timer needs it).
    'try{const _ev=require("node:events");const _EE=_ev&&(_ev.EventEmitter||(_ev.default&&_ev.default.EventEmitter));if(_EE&&typeof _EE.prototype.prependOnceListener!=="function"){Object.defineProperty(_EE.prototype,"prependOnceListener",{value:function(e,l){const w=(...a)=>{this.off(e,w);l.apply(this,a)};w.listener=l;return this.prependListener(e,w)},writable:true,configurable:true})}}catch(_){}',
]

GOT_HEAD_MARKERS = ["__tcEndFix", "__tcReadEndFix", "__tcHwmFix", "__tcDiag", "prependOnceListener"]

# [1] call-site guards: X.prependOnceListener("evt",cb) -> guarded fallback to once.
def guard_sites(text, sites):
    for var, evt in sites:
        orig = f'{var}.prependOnceListener("{evt}",'
        repl = f'({var}.prependOnceListener??{var}.once).call({var},"{evt}",'
        count = text.count(orig)
        if count == 0 and text.count(repl) > 0:
            continue  # already patched
        if count != 1:
            raise AssertionError(f"expected exactly 1 occurrence of {orig!r}, found {count}")
        text = text.replace(orig, repl)
    return text

HTTP_TIMER_SITES = [("n","abort"),("t","lookup"),("n","socket"),("n","finish"),
                    ("n","response"),("t","end"),("t","aborted")]
GOT_SITES = [("a","end")]

# [5] get-stream bypass: flowing-mode collector, no pipeline/pipe.
GET_STREAM_BYPASS = '''
// [tinycast-lark-fix] flowing-mode buffer() replacement.
// The Tinycast runtime's stream.pipeline throws while piping got Request
// streams, and the rejection is swallowed upstream, leaving promises pending
// forever. Collect via plain data/end/error events instead.
try{(function(){
var m=module.exports;
if(m&&typeof m.buffer==="function"&&!m.buffer.__tcBypass){
  m.buffer=function(stream,options){
    return new Promise(function(resolve,reject){
      var chunks=[],total=0,settled=false;
      var done=function(err){
        if(settled)return; settled=true;
        if(err)reject(err);
        else resolve(Buffer.concat(chunks,total));
      };
      stream.on("data",function(c){
        if(typeof c==="string")c=Buffer.from(c);
        else if(!Buffer.isBuffer(c)&&!(c instanceof Uint8Array))c=Buffer.from(String(c));
        chunks.push(c); total+=c.length;
      });
      stream.once("end",function(){ done(null) });
      stream.once("error",function(e){ done(e) });
      stream.once("aborted",function(){ done(new Error("request aborted")) });
      stream.once("close",function(){ if(!settled) done(new Error("stream closed before end")) });
      if(stream.readableEnded){ done(null); return }
    });
  };
  m.buffer.__tcBypass=true;
}
})()}catch(_e){}
'''

# ---------------------------------------------------------------------------

def patch_file(path, fn, label):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    before = text
    text = fn(text)
    if text == before:
        print(f"  = {label}: already patched, skipped")
        return
    backup = path + ".bak-tcfix"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  + {label}: patched (backup: {os.path.basename(backup)})")

def patch_got(text):
    # head injection, idempotent: each block carries a unique marker
    def marker_of(block):
        for key in ("__tcEndFix", "__tcReadEndFix", "__tcHwmFix", "__tcDiag"):
            if key in block:
                return key
        return "_EE.prototype.prependOnceListener"
    missing = [b for b in GOT_HEAD_LINES if marker_of(b) not in text]
    if missing:
        text = "\n".join(missing) + "\n" + text
    return guard_sites(text, GOT_SITES)

def patch_http_timer(text):
    return guard_sites(text, HTTP_TIMER_SITES)

def patch_get_stream(text):
    if "__tcBypass" in text:
        return text
    return text + GET_STREAM_BYPASS

def main():
    ext = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    nm = os.path.join(ext, "node_modules")
    targets = [
        (os.path.join(nm, "got", "index.js"), patch_got, "got/index.js"),
        (os.path.join(nm, "@szmarczak", "http-timer", "index.js"), patch_http_timer,
         "@szmarczak/http-timer/index.js"),
        (os.path.join(nm, "get-stream", "index.js"), patch_get_stream, "get-stream/index.js"),
    ]
    if not os.path.isdir(nm):
        sys.exit(f"error: extension not found at {ext}\n"
                 f"Install the Lark Documents extension in Tinycast first, or pass the "
                 f"extension directory as the first argument.")
    print(f"Patching Tinycast Lark extension at:\n  {ext}")
    for path, fn, label in targets:
        if not os.path.isfile(path):
            sys.exit(f"error: missing {label} — was the extension installed differently?")
        patch_file(path, fn, label)
    print("Done. Fully quit Tinycast (⌘Q) and reopen it for the patch to load.")

if __name__ == "__main__":
    main()

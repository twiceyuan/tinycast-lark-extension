# 诊断记录：Tinycast 运行时与 got 的五层兼容缺口

环境：Tinycast v0.11.19 (build 118) / macOS 26 (arm64) / Raycast 扩展 Lark Documents v0.0.10（依赖 got 12.6.x、qrcode、tough-cookie、get-stream）。

症状演进（每修一层露出下一层）：

```
Load QR Code failed (n.prependOnceListener is not a function)
  → 修复 1
Load QR Code failed (Buffer.from: unsupported input)
  → 修复 2
HTTP 200 后永久 loading，无任何报错
  → 修复 3 + 4 + 5
```

## 1. EventEmitter 缺 `prependOnceListener`

- 位置：`Scripts/raycast-runtime/src/events.js`（运行时自制 EventEmitter）。
- 实现了 `on / addListener / prependListener / once / off / removeListener / removeAllListeners / emit`，**漏了 `prependOnceListener`**（真 Node 标准方法）。
- 触发：`@szmarczak/http-timer`（got 依赖）对每个请求调用 `request.prependOnceListener("abort", ...)` 等共 8 处。
- 修复：调用点改为 `(x.prependOnceListener ?? x.once).call(x, ev, cb)` + 经 `require("node:events")` 给原型补上真正的方法。

## 2. `ClientRequest.end()` 不支持回调重载

- 位置：`Scripts/raycast-runtime/src/node-shims.js` 的 `ClientRequest`。
- 运行时实现：`end(chunk) { if (chunk != null) this.write(chunk); ... }` —— 第一参数一律当数据。
- got 的合法用法（`core/index.ts` 的 `_final`）：`this._request.end(error => { ...callback(error) })`，回调承重（上传记账 + `upload-complete` 事件 + 完成内部流）。
- 结果：函数被传给 `write()` → `Buffer.from(fn)` → `TypeError: Buffer.from: unsupported input`，被 got 的 `catch(_){this._beforeError(_)}` 原样抛出。
- 注意：运行时 HTTP ClientRequest **从不 emit "finish"**（只有 child_process 有），所以回调不能挂 finish，改用 `queueMicrotask` 在 `end()` 返回后触发。

## 3. `Readable.read()` 暂停模式下不发射 `end`

- 位置：`Scripts/raycast-runtime/src/streams.js`。
- 运行时：`_schedule()` 微任务里 `if (state.ended && !state.buffer.length && !state.endEmitted) emit("end")`。但只有 `push()` 和 `resume()`/`on('data')`/`on('readable')` 会调度它；**`read()` 抽干缓冲后没有任何代码再调度**。真 Node 在 read 路径有 emitEOF 逻辑。
- 触发场景：生产者（运行时 `_send()` 收到响应后）先 `push(body)` + `push(null)`，消费者（got）后到，用暂停式 `while ((d = r.read()) !== null)` 拉取——数据拿走了，`end` 永不发射。
- 修复：包装 `Readable.prototype.read`，抽干且 `ended && !endEmitted` 时补调 `this._schedule()`。
- 用复刻运行时语义的最小模型验证：修复前 STUCK（数据可达但 end 永不发射），修复后正常。

## 4. `highWaterMark: 0` 死锁

- got 的 Request（extends 运行时 Duplex）构造为 `super({ autoDestroy: !1, highWaterMark: 0 })` —— 故意的，语义是"不缓冲、按需拉取"。真 Node 对 hwm=0 有特殊处理。
- 运行时 `_pull()`：`if (state.reading || state.ended || state.destroyed) return; if (state.length >= state.highWaterMark) return;` —— `0 >= 0` 恒真，**`_read()` 永远不会被调用**。
- 修复：`_pull` 包装里把非正 hwm 当 1 处理（语义恰为"缓冲空才拉"）。

## 5. `stream.pipeline()` 抛错且被三层静默吞掉

- get-stream（got 依赖）读响应体：`await promisify(stream.pipeline)(gotRequest, collector)`。
- 运行时 `Uu`（pipeline 的 promise 实现）在 `a.reduce((a,b) => a.pipe(b))` 处抛 `TypeError: Right hand side of instanceof is not an object`（`RaycastRuntime.generated.js:20:35610`，`y.pipe(` 调用表达式本身；压缩代码中 instanceof 的真实来源未能进一步定位）。
- 异常链路全部被吞：get-stream 的 `catch(f){a(f)}`（其 Promise 被 got 忽略）→ got `_setRawBody` 的 `catch {}` 返回 false → `await this._setRawBody() && this.emit("response")` 短路。**promise 永不落定，无任何报错**——永久 loading 的直接原因。
- 修复：绕过 pipeline——用 flowing 模式（`on('data'/'end'/'error'/'aborted'/'close')`）重写 get-stream 的 `buffer()`。

## 诊断方法备忘

给插件 node_modules 注入生命周期探针（请求 end/send/response/resp-end/看门狗、流 pipe/resume/_pull 守卫快照、pipeline 包装、get-stream 拒绝路径），每轮用户重试后读日志逐环排除。关键判据：`response 200` 后永远没有 `resp-end`；`[gs] REJECTED: ... instanceof ...` 一锤定音。

## 上游 issue 建议

五处修复对应的上游改动都很小：events.js 补 `prependOnceListener`；node-shims.js 的 `end()` 支持 Node 回调重载（或至少不把函数当数据）；streams.js 的 `read()` 补 emitEOF 调度；`_pull` 对 hwm<=0 特判；排查 pipeline/pipe 的 instanceof 崩溃。完整英文草稿见 [issue-draft.md](issue-draft.md)。

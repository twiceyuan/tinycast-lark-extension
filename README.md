# tinycast-lark-extension

让 Raycast 的飞书扩展 **Lark Documents** 在 [Tinycast](https://github.com/abue-ammar/tinycast)（开源 Raycast 替代启动器）里正常工作。

Tinycast 自带一个 Node.js 兼容运行时（`RaycastRuntime.generated.js`）来执行 Raycast 扩展，但它对若干标准 Node 语义的实现有缺口。飞书扩展依赖的 HTTP 客户端 `got` 恰好把这些缺口挨个踩了一遍，表现为三种症状：

| 症状 | 直接原因 |
|---|---|
| `Load QR Code failed (n.prependOnceListener is not a function)` | 运行时 EventEmitter 缺 `prependOnceListener` |
| `Load QR Code failed (Buffer.from: unsupported input)` | 运行时把 `end(回调函数)` 的函数参数当数据写入了 |
| 请求成功（HTTP 200）但界面**永久 loading**，无任何报错 | 运行时流实现的多处语义缺口导致 got 的 promise 永不落定 |

实际上背后是**五层独立的缺口**，逐层修复后才全部打通（诊断过程见 [docs/diagnosis.md](docs/diagnosis.md)）：

1. **EventEmitter 缺 `prependOnceListener`** — got 的依赖 `@szmarczak/http-timer` 对每个请求都会调用它。
2. **`ClientRequest.end()` 不支持 Node 的回调重载** — got 按标准语义调用 `request.end(errorFirstCallback)`，运行时却把函数当 body 传给 `Buffer.from`；且运行时的 HTTP ClientRequest 从不发出 `finish` 事件。
3. **`Readable.read()` 在暂停模式下不发射 `end`** — 缓冲被抽干且 EOF 已 push 后，运行时不会像 Node 那样重新调度 emitEOF，got 永远等不到响应结束。
4. **`highWaterMark: 0` 死锁** — got 故意用 `highWaterMark: 0` 构造请求流（按需拉取），但运行时 `_pull()` 的守卫 `length >= highWaterMark` 使得 `0 >= 0` 恒成立，`_read()` 永远不会被执行。
5. **`stream.pipeline()` 内部崩溃且异常被吞** — 运行时 pipeline 在 pipe got 请求流时抛 `Right hand side of instanceof is not an object`，get-stream 捕获后静默返回，got 的 promise 永不落定（这正是"永久 loading 无报错"的直接原因）。

## 使用方法

### 方式一：补丁脚本（推荐）

对 Tinycast 已安装的扩展原地打补丁，幂等可重跑：

```bash
python3 scripts/tinycast-lark-fix.py
```

- 默认定位 `~/Library/Application Support/com.tinycast.app/extensions/lark/`，也可传自定义路径。
- 修改前会把原始文件备份为 `*.bak-tcfix`。
- **Tinycast 或扩展更新后补丁会被覆盖，重跑一次即可。**
- 打完补丁后需完全退出 Tinycast（⌘Q）再重开才会生效。

### 方式二：直接替换（drop-in）

`dist/` 是打好补丁的完整扩展（基于商店版本 v0.0.10）。把 `dist/` 的内容覆盖到上面的扩展目录即可。注意：如果你的扩展版本比 v0.0.10 新，建议用方式一。

## 验证

- 补丁脚本对纯净安装应用后，通过真实 HTTPS POST 全流程回归（200 + 响应体回显）。
- 五项修复各自有针对性单测（模拟运行时行为 + 真实 Node 回归），见诊断文档。
- 在 Tinycast v0.11.19 + Lark Documents v0.0.10 实测：登录二维码正常显示、扫码登录、文档搜索均正常。

## 上游

这些缺口影响**所有**使用 `got`（及流式 API）的 Raycast 扩展在 Tinycast 上的兼容性。完整的复现信息和建议修复已整理成 issue 草稿：[docs/issue-draft.md](docs/issue-draft.md)，欢迎到 [abue-ammar/tinycast](https://github.com/abue-ammar/tinycast) 提交/附议。

## 许可

原始扩展 [Lark Documents](https://www.raycast.com)（作者 tmk / tofrankie）与所含依赖均为 MIT 许可；本仓库的运行时兼容补丁同样以 MIT 发布，见 [LICENSE](LICENSE)。

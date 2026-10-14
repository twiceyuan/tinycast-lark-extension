# tinycast-lark-extension

在 [Tinycast](https://github.com/abue-ammar/tinycast)（开源 Raycast 替代启动器）里正常工作的飞书扩展 **Lark Documents**。

这是 Raycast 商店 [Lark Documents](docs/README-upstream.md)（作者 tmk / tofrankie，MIT）的兼容分支：上游源码 + 一个 `src/tinycast-compat.js` 兼容层。Tinycast 自带的 Node 兼容运行时（`RaycastRuntime.generated.js`）有几处标准语义缺口，`got` 等 HTTP 依赖会挨个踩中，表现为：

- `Load QR Code failed (n.prependOnceListener is not a function)`
- `Load QR Code failed (Buffer.from: unsupported input)`
- 请求成功（HTTP 200）但界面**永久 loading**，无任何报错

兼容层修复了背后的五层运行时缺口，全部有守卫，在真 Node（Raycast、`ray develop`）或已修复的 Tinycast 上是空操作。完整诊断：[docs/diagnosis.md](docs/diagnosis.md)。

## 安装

### 方式一：Tinycast 内直接安装（推荐）

Tinycast → 扩展设置 → **Install from GitHub** → 粘贴本仓库地址：

```
https://github.com/twiceyuan/tinycast-lark-extension
```

Tinycast 会自动拉源码、装依赖（需要机器上有 Node.js 和 npm/pnpm/yarn/bun 之一）、构建并安装。之后如需更新，重新执行一次即可。

### 方式二：已从商店安装，原地修复

如果扩展已经从 Raycast 商店装好（或机器上没有 Node.js），对现有安装原地打补丁：

```bash
python3 scripts/tinycast-lark-fix.py
```

脚本幂等可重跑（Tinycast/扩展更新覆盖补丁后再跑一次即可），修改前自动备份。打完 ⌘Q 重启 Tinycast 生效。

## 与上游的差异

- `src/tinycast-compat.js`：新增的兼容层（五项运行时修复），在每个命令入口最先导入。
- `src/search-docs.tsx` / `src/search-minutes.tsx`：仅新增一行 `import "./tinycast-compat";`。
- `package.json`：版本号 `-tinycast.1` 后缀，描述加注。
- 其余源码与上游 [raycast/extensions@extensions/lark](https://github.com/raycast/extensions/tree/main/extensions/lark) v0.0.10 一致。

## 许可

MIT，见 [LICENSE](LICENSE)。原扩展版权归 tmk / tofrankie 所有；Tinycast 兼容补丁部分同样以 MIT 发布。

# 开发 / 构建期网络允许清单

产品运行期白名单只在 `cutora/core/net.py::ALLOWED_HOSTS`（通用规范 §2.2）。
下列主机**允许出现在开发或构建脚本里**，但**不得**加入运行期白名单：程序
运行时不需要它们。

| 主机 | 用途 | 调用方 |
|---|---|---|
| `raw.githubusercontent.com` | 从 `google/fonts` 与 `adobe-fonts/source-han-sans` 取字体文件和许可证 | `tools/fetch_t09_fonts.py`（开发期） |

`*.hf.co` **不是**运行期可直连的主机。它只允许作为 `huggingface.co`（含子域）HTTPS 302 的落点，见 `cutora/core/net.py` 的 `_GuardRedirect`。这条例外的安全前提是：**每个经此下载的文件都强制 SHA256 校验**，CDN 被替换也会被哈希挡住。

规则：

1. 运行期 `cutora/core/net.py` 拒绝上述主机（见 `tests/test_t09_security.py`）。
2. 每个从这里下载的文件必须在 `resources/fonts/FONTS_MANIFEST.json` 记录
   `download_url` + `sha256`。
3. 新增开发期主机先改本文，再改脚本；不要改 `net.py`。

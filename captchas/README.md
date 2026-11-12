# captchas/ —— 万盾求解器副本（本项目维护，不追上游）

11 厂商验证码/挑战求解器，源自 `captcha-solver`（上游目录无 LICENSE 文件；项目所有者 2026-11-11
确认按 MIT 开源，来源与许可状态见根 `NOTICE`）。**本目录是项目维护的副本，不是上游依赖。**

## 为什么是副本而不是依赖

主项目是「CF 专项」，上游是「多厂商通解」。两者的演进节奏不同：上游追厂商改版，
本项目追 CF 挑战与回放工程。把上游当依赖意味着每一次上游改动都会传进主链路；
做成副本则相反——**我们决定何时 cherry-pick**，主链路永远不被上游节奏绑架。

## 与原版（captcha-solver）的差异

- 二进制 ONNX 模型（`aliyun/best.onnx`、`recaptcha/models/recaptcha_cls_s.onnx`）
  **不入库**（见 `.gitignore`）；缺失时 aliyun 自动回落 cv2 gap 检测，
  reCAPTCHA 的 `classifier=auto` 回落 mistral。
- import 统一为 `captchas.common.*`（原版是 `common.*`，依赖 cwd）。
- 原 `server.py` 的 FastAPI 外壳不搬：入口统一走主服务的 `POST /solve`
  （`want=<扩展类型>`），分发在 `solver/multicaptcha.py`。
- 仍然**只解挑战、不建号**：账号创建与表单侦察是调用方的事。

## 目录

| 目录 | 厂商 | 产物 |
|---|---|---|
| `turnstile/` | Cloudflare Turnstile | 一次性 token |
| `recaptcha/` | Google reCAPTCHA v2/v3/invisible | 一次性 token（可带 score） |
| `hcaptcha/` | hCaptcha | 一次性 token |
| `cloudflare/` | Cloudflare | `cf_clearance` cookie |
| `awswaf/` | AWS WAF | `aws-waf-token` |
| `botguard/` | Google OAuth BotGuard | `bgRequest` token |
| `datadome/` | DataDome | `datadome` cookie |
| `perimeterx/` | PerimeterX / HUMAN | `_px3` cookie（CDP press-hold） |
| `akamai/` | Akamai Bot Manager | `_abck` cookie（`bmak` 遥测） |
| `aliyun/` | 阿里云 Captcha 2.0 | `{certifyId, deviceToken, data}`（滑块，**强制子进程**） |
| `arkose/` | Arkose FunCaptcha | `fc_token`（ONNX 视觉拼图） |
| `common/` | — | 选择器解析 / pre_actions / post_fetch / Mistral key pool |

## 依赖

`pip install cloakbrowser`（必需）**并先 `cloakbrowser login`**——Chromium 二进制按需下载，
没有许可证时 `launch_async` 抛 `CloakBrowserLicenseError`；`aliyun`/`arkose` 另需
`onnxruntime` + `opencv-python-headless`。缺失或未登录时请求报 `not-configured`(503)
+ 安装/登录提示，不影响主链路。

**不走人家 license 的本地路径（v6.4.3 实测）**：设置
`CLOAKBROWSER_BINARY_PATH=<本机 Chromium/Chrome 路径>` 即可跳过 license 校验
（`cloakbrowser/download.py::ensure_binary` 先检查该变量，命中即直接返回，不读 license）。
本机用 Playwright Chromium 在官方 Turnstile 测试 sitekey 上机械跑通（7.3s，token 正常返回）。
但本地 Chromium **没有 CloakBrowser 的 stealth 补丁**：这只证明机械链路
（启动/路由/solve/取 token），不证明真实挑战的反检测成功率；stealth 仍需自建引擎或专有二进制。

## 上游同步策略

不做自动同步。需要某个上游修复时，手动 diff 对应子目录后 cherry-pick，
并在 `CHANGELOG.md` 记下来源与日期。改动本目录视为改本仓库代码（同样过测试与门禁）。

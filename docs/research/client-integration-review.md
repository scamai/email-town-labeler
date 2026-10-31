中文 · [报告](2026-10-30-client-integration.html) · [English overview](../../README.md)

[← 返回 README](../../README.md)

# 客户端集成评估：复核记录

目录：[范围](#范围) · [渲染检查](#渲染检查) · [独立复核](#独立复核) · [交付](#交付)

## 范围

- 日期：2026-10-30；代码基线 4bbfa24；报告 SHA256：`2d10050d182bbc866e4202d0b18317a36680010dd1fae48925215c2cb6b14d90`。
- 读者：希望自动分类在常用邮件客户端可见、方便每日处理的项目负责人。
- 问题：哪些显示和操作已可用，应该先完善现有客户端还是另做软件？
- 类型：decision；本地 HTML；沿用此前报告与项目色系。
- 预期理解：同一工作 Gmail 在三客户端的分类显示及列表打开已实测；纠错写回和完成流程未实测；先验证原生客户端闭环，再开发账户管理与第二类邮箱接入。
- 证据：本机 Outlook 16.114.3、Apple Mail 16.0、Gmail 网页的只读界面检查；代码与官方文档。没有邮件写操作或持久设置变更，不包含私人邮件截图。

## 渲染检查

- `artifact-lint.py docs/research/2026-10-30-client-integration.html --strict`：0 FAIL、0 WARN、2 INFO。无统计图或首页指标是定性决策的有意选择。
- `design-lint.py html ...`：4 PASS；工具有一个不支持的选择器，实际浏览器主题另行验证。
- Chromium：light/dark × 1280×900/390×844；首屏、全页、方案表、实测表与 200% 字体截图。实际背景分别 rgb(255,255,255)、rgb(13,17,23)；无页面水平溢出、无失效内部锚点；附加检查无 pageerror。
- 作者检查首屏、手机表格与放大字体；文字可辨，窄屏表格独立横向滚动并有提示。键盘 Tab 可聚焦链接；手机表格聚焦后 ArrowRight 滚动 40px。JavaScript 关闭仍可阅读；无脚本、图片、外部字体或交互控件。
- 标题最初在桌面尾行只剩一个字，已缩短并重渲染。
- 相同色系的正文、次要文字、链接对页面／面板背景，最小对比度 light 5.69:1、dark 6.85:1；详见此前色值检查。报告不依赖颜色表达状态。
- 本机临时证据 `/tmp/email-town-client-review/`；复现脚本 `/tmp/email-town-client-render.py`、`/tmp/email-town-extra-checks.py`，通过 `uv run --no-project --with playwright python <script>` 运行。截图仅为报告页面。
- 原报告新增后续实测提示，并以四主题／尺寸组合重新检查首屏；无整体溢出。原文作为历史基线保留。

## 独立复核

独立模型只收到文件、截图、受众与读者问题，未收到作者预期答案。其复述：同一 Gmail 在三个客户端显示与打开已验证；写回、完成状态、多账户和 Microsoft 接入未验证；下一步是可控测试邮件闭环，再固定每日入口。内容无误导性结论。

发现并修复：手机表格右侧限制列需横向滚动，而原提示仅在长表格之后。已在两张表格之前增加明确滑动提示，重新渲染四组合并复核。

最终原话：“PASS. Verified both updated screenshots: the scroll instruction is legible immediately above each table and explicitly directs phone readers to the right-side limitations and unverified items. The required repair is resolved; no further repairs identified.”

这是模型独立阅读检查，不是实际用户可用性实验。PASS 仅指报告清晰度与渲染，不包括客户端写操作。

## 交付

本地研究资料；未公开发布。没有修改产品代码，本轮没有重复运行产品测试；前次 pytest 结果在报告中明确标为前次检查。实时客户端显示证据不能代替写回、模型准确率或跨服务验收。

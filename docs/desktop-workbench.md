简体中文 · [English](desktop-workbench.en.md)

[返回 README](../README.md)

# 本机统一邮件工作台

[下载应用](#下载应用) · [从源码打开](#从源码打开) · [连接邮箱](#连接邮箱) · [统一处理](#统一处理) · [键盘快捷键](#键盘快捷键) · [语言与颜色](#语言与颜色) · [数据与隐私](#数据与隐私) · [恢复与验证](#恢复与验证)

工作台把已授权账户的邮件放进同一列表，支持收件箱、已发送、归档和垃圾箱视图、分类、搜索和完成状态。
它也支持明确操作的读/未读、星标、归档、垃圾箱、恢复以及纯文本收发、回复、转发和附件。
手动分类与完成状态保存在本机；外部邮箱变更只在用户明确操作时发生。

它可以作为 Email Town 桌面应用运行（macOS 版已签名并经 Apple 公证；Windows/Linux 版尚未签名；不在应用商店），也可以从源码在浏览器中运行。

## 下载应用

从 v0.1.1 之后的第一个版本起（v0.1.1 没有），每个 Release 页面都附带可直接运行的应用，
不需要终端、Python 或 uv。macOS 打开磁盘映像，把 Email Town 拖进“应用程序”；Windows 和 Linux 解压后双击即可。

| 文件 | 适用系统 |
|---|---|
| `Email-Town-<version>-macos-arm64.dmg` | Apple 芯片的 macOS 14 或更新版本。由 Reality Inc. 签名并经 Apple 公证，打开时不会有警告。Intel Mac 请[从源码打开](#从源码打开) |
| `Email-Town-<version>-windows-x64.zip` | Windows 10/11 x64，需要 Microsoft Edge WebView2 Runtime（Windows 11 自带） |
| `Email-Town-<version>-linux-x64.zip` | 在 Ubuntu 24.04 上构建（需 glibc 2.39 或更新）。需要发行版自带的 GTK 3 和 WebKitGTK 4.1：Debian/Ubuntu 运行 `sudo apt install gir1.2-webkit2-4.1` |

v0.1.2 没有 Windows 文件（它的 Windows 构建未通过发布检查）；v0.1.3 起提供。

**Windows 和 Linux 版目前未签名**，第一次打开需要多一步。只对从本仓库 Releases 页面下载的 zip 这样做：

- **Windows：** SmartScreen 显示“Windows 已保护你的电脑”。点击**更多信息 → 仍要运行**。
- **Linux：** 在解压出的 `email-town` 文件夹中运行 `email-town`。

窗口里显示的工作台和浏览器版相同。跳到 Gmail/Outlook 原邮件的链接、Microsoft 登录页面和 Google
登录会在默认浏览器中打开；下载附件时会询问保存位置。**关闭窗口会停止工作台**及其模型进程。
如果 `email-town-labeler desktop` 已经在同一个数据目录运行，应用会在它上面打开窗口，关闭窗口后它继续运行。

应用与 CLI 使用相同的配置和数据目录（`EMAIL_TOWN_LABELER_HOME` 同样可以改变位置）。模型不在应用里：
没有 `config.toml` 时，工作台使用已发布的本机模型 `town-modernbert-int8`，在第一次分类邮件时下载一次并校验
sha256（下载 263 MB，解压后 382 MB）；下载期间列表上方会提示，邮件照常可读。`config.toml` 中指定的模型（包括纯关键词的 `rules`）始终优先。日志是数据目录中的 `workbench/desktop.log`
（macOS `~/Library/Application Support/email-town-labeler/`，Windows `%LOCALAPPDATA%\email-town-labeler\`，
Linux `~/.local/share/email-town-labeler/`），不包含邮件内容。要检查下载的应用，用 `--self-check` 运行它；
它报告应用能加载哪些组件，不打开窗口，也不读写你的数据：

```sh
"/Applications/Email Town.app/Contents/MacOS/Email Town" --self-check
```

下载的应用取代下面的源码启动器；如果生成过 `~/Applications/Email Town.app`，请删除它，只保留一个“Email Town”。

**自己构建**（在本仓库检出中，在目标平台上运行）：

```sh
uv run --extra app --group app-build python scripts/build_app.py --zip
uv run --extra app --group app-build python scripts/smoke_app.py -- "dist/Email Town.app/Contents/MacOS/Email Town"
```

`build_app.py` 生成 `dist/Email Town.app`（macOS）、`dist/Email Town/`（Windows）或 `dist/email-town/`（Linux），
加 `--zip` 时还生成发布用的压缩包。在 Apple M4 Pro（macOS，Python 3.12）上它报告构建用时 11 秒、应用 136 MB、
zip 49 MB。`smoke_app.py` 运行自检，在新的临时数据目录上打开应用，检查服务、第二次启动和干净退出，从不接触你的邮件数据。
Linux 还需要 `pywebview[gtk]`（PyGObject）和 GTK 开发包，[Desktop app 工作流](../.github/workflows/app.yml)
列出了它们。该工作流在三个平台上构建并冒烟测试，只在手动启动或推送 `v*` 标签时运行。

## 从源码打开

已安装 CLI 的电脑：

```sh
email-town-labeler desktop --background
```

首次安装可使用 `uv tool install 'email-town-labeler[onnx,desktop]'`；使用本分支源码时运行
`uv tool install --editable '.[onnx,desktop]'`。`desktop` extra 为 IMAP 提供系统钥匙串支持。
分类使用 `config.toml` 中的模型；没有配置时使用 `town-modernbert-int8`（首次下载 263 MB）。分类内容不上传。桌面工作台的收发功能
与只负责标签的 CLI 服务分开。

命令会打开浏览器里的本机界面。重复执行会打开同一个运行实例；关闭浏览器不会停止后台进程。

macOS 源码检出可生成双击入口：

```sh
python3 scripts/install_macos_workbench.py
```

默认生成 `~/Applications/Email Town.app`，指向已安装的 CLI；不安装登录启动项。
更新源码后，先停止工作台再重新打开，才能加载新的 Python 后端。

```sh
email-town-labeler desktop --stop
# 需要前台运行/排查时：
email-town-labeler desktop --no-browser
```

界面只绑定 `127.0.0.1` 的随机端口。终端显示的裸地址没有登录权限，使用启动器打开。
不要把本机私密启动链接发给别人。

想从源码使用原生窗口而不是浏览器：安装 `app` extra 后运行 `email-town`
（`uv tool install --editable '.[app]'`；Linux 另需 `pywebview[gtk]` 或发行版的 PyGObject）。

## 连接邮箱

已有默认 Gmail 授权会自动纳入，并以邮箱地址显示。其他账户从侧栏“Accounts → + Add”（添加账户）连接；显示名称可不填，留空时以邮箱地址显示。
应用不会读取 Outlook 或 Apple Mail 保存的登录凭据。

| 邮箱 | 连接方式 | 需要准备 |
|---|---|---|
| Google / Gmail | 浏览器登录 Google，Gmail modify 读信、发信和管理权限 | 软件自带的 Google 登录客户端，或在对话框中导入自己的 Desktop OAuth 客户端文件（[Google 设置](google-cloud-setup.md)）；两者都没有时，可在“Other email”中用应用专用密码添加 Gmail |
| Microsoft / Outlook | Microsoft 设备代码登录 | 软件自带 Microsoft 应用 ID 时无需准备；否则在“Use your own Microsoft app registration”中填写自己的公共客户端应用 ID；租户可能要求管理员同意 |
| 其他邮箱（iCloud、Yahoo、QQ…） | TLS IMAP/SMTP + 应用专用密码 | 输入邮箱地址后，常见服务商会自动填好服务器设置并给出生成应用专用密码的链接；其他服务商需填写服务器 |
| 已有 Email Town 目录 | 填写该账户的 `--home` 绝对目录 | 该目录包含自己的有效 Gmail 授权 |

已内置 Gmail、iCloud、Yahoo、AOL、Fastmail、Zoho、QQ 邮箱的建议设置，可在“Server settings”中修改。Outlook.com、Hotmail、Live
地址会引导到 Microsoft 登录，因为 Outlook.com 已不再接受应用专用密码。网易 163/126 需要 IMAP ID 握手，目前尚未支持。

新建 Microsoft 账户需支持你的账户类型，使用 delegated `Mail.ReadWrite` / `Mail.Send` / `User.Read` 与
`offline_access`；已有只读授权需重新授权，绝不静默扩大权限。参考 [Microsoft 设备授权文档](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)。
不能把 Gmail 授权拿来读取 Microsoft 账户。每个邮箱都需要它自己的登录授权。
Exchange 本地部署不等于 Microsoft Graph 邮箱；需管理员允许的 IMAP 或其他适配。

账户旁的“⋯”提供暂停、恢复和重新授权。暂停只是停止自动同步，已载入邮件仍能查看。
重新授权保留原账户的整理状态，并检查邮箱身份；登录其他邮箱请使用侧栏“+ Add”（添加账户）。

## 统一处理

- 左侧选择所有账户或单个账户，再按“今天”、历史待处理、分类和完成状态筛选。
- 中间显示账户、主题、发件人、分类；搜索只查当前已载入的主题和发件人。
- 打开邮件可预览纯文本，在本机更改分类或标记完成。支持批量操作和撤销。
- 完成不会删除、移动、归档或标记原邮件已读，也不会移除原来的分类标签。
- Gmail 已有项目分类标签会直接显示；未分类邮件、Microsoft 和 IMAP 邮件使用共享的本机模型；自己的已发送邮件不分类。
- 本机手动分类优先于自动分类，并与完成状态独立。它目前不写回服务端，也不加入旧 CLI 的学习反馈。
- HTML 正文引用的图片显示在纯文本正文下方，不运行发件人的 HTML 布局、脚本或样式。CID 内嵌的 PNG、JPEG、GIF、WebP、AVIF、BMP 自动从邮箱加载（每张最多 8 MiB，当前预览合计最多 32 MiB）。HTTPS 外链图片默认不加载，点击 **Load external images** 后仅对当前预览启用；它可能让发件人知道你打开了邮件。不支持的来源、SVG 或过大的内嵌图片可通过原邮件或下载附件查看。Gmail/Outlook 支持跳回原邮件页面；也可直接在工作台回复、转发、添加及下载附件。

默认每账户载入最近 100 封收件箱邮件；“载入更多”每次增加 100，最高每账户 1000。
列表会明确提示截断，计数只代表已载入邮件。最多 32 个账户；每次最多处理 500 封。
Inbox、Sent、Archive、Trash 是独立视图；归档后本机整理状态仍保留。
批量选择只包括当前已载入且符合筛选的邮件，不代表邮箱里的全部邮件。

写信、回复、回复全部和转发支持 To/Cc/Bcc、纯文本正文及附件。草稿保存在本机；
发送失败、结果不确定或部分收件人失败时保留草稿，不会自动重发。转发不自动复制附件，需自行重新添加。
Gmail/SMTP 附件合计上限为 20 MiB，Graph 为 2 MiB；下载上限分别为 20 MiB 和 8 MiB。
IMAP 发信需要额外填写 SMTP 服务器，并使用 TLS；不支持安全移动的服务器会禁用移动操作。

打开邮件会像 Gmail 一样把它标为已读：列表中的加粗和蓝点立即消失，写回邮箱在后台完成（失败会恢复原状）。点 **Mark unread** 后，
它会保持未读，直到再次打开。没有管理邮件权限或已暂停的账户不做改动。当前文件夹的标签上显示未读数。

已读、星标、归档、删除和恢复会修改所选账户的原邮件，其他客户端同步后也能看到。
删除是移入服务商垃圾箱；恢复返回收件箱。手动分类和完成状态仍只在本机生效。

自动同步间隔 60 秒。界面保持一个最长 10 秒的等待请求，本机状态一变就更新。Gmail 冷启动时邮件按每批 25 封陆续显示；
切回已打开过的文件夹会先显示上次的列表再校验。Microsoft/IMAP 账户最新 10 封的正文会预先读取，打开时无需等待。一个账户断线不会清空其他账户；断线账户保留
最后一次列表并显示错误。完成记录重启后仍存在；撤销令牌仅在当前进程中保存。

顶部的 **Compose** 用于写信，**Drafts** 用于本机草稿。列表每封邮件依次显示发件人和时间、主题、分类与账户。
选择邮件后才显示批量操作；**More** 中区分原邮箱操作与本机完成/分类操作。
预览顶部保留归档、垃圾箱和 More，回复/转发位于发件人信息下方。
窗口宽度不足 1200 CSS 像素时，打开邮件会替换列表，点击 **Back** 返回原位置；
不足 768 像素时，通过左上角菜单打开账户和筛选。写信字段可以滚动，**Send / Save draft** 固定在窗口底部。

## 键盘快捷键

在应用中按 <kbd>?</kbd>（或点右下角 **Keyboard shortcuts**）查看列表。单键沿用 Gmail 的习惯，只在没有在输入框里打字、
也没有在用输入法拼写时生效；⌘ 组合键沿用 Apple Mail（Windows 和 Linux 上为 Ctrl）。

| 按键 | 操作 |
|---|---|
| <kbd>j</kbd> / <kbd>k</kbd>（或 <kbd>↓</kbd> / <kbd>↑</kbd>） | 下一封（更早）/ 上一封（更新） |
| <kbd>/</kbd> 或 <kbd>⌘F</kbd> | 搜索 |
| <kbd>g</kbd> 再按 <kbd>i</kbd> / <kbd>s</kbd> / <kbd>a</kbd> / <kbd>t</kbd> | 收件箱、已发送、归档、废纸篓 |
| <kbd>x</kbd> | 选中或取消选中打开的邮件 |
| <kbd>Shift</kbd> 点击 · <kbd>⌘</kbd> 点击 | 选中一个范围（行或复选框）· 单独加选或取消一封且不打开它 |
| <kbd>Shift j</kbd> / <kbd>Shift k</kbd> | 向下 / 向上扩展选择 |
| <kbd>*</kbd> 再按 <kbd>a</kbd> / <kbd>n</kbd> / <kbd>r</kbd> / <kbd>u</kbd> | 全选、全不选、选已读、选未读（“Select all loaded”旁的 ▾ 菜单里也有，另含有星标、无星标） |
| <kbd>e</kbd> | 归档（有选中时作用于选中项，否则作用于打开的邮件） |
| <kbd>#</kbd> 或 <kbd>Delete</kbd> | 移到废纸篓（和按钮一样会先确认） |
| <kbd>s</kbd> · <kbd>Shift U</kbd> · <kbd>Shift I</kbd> · <kbd>d</kbd> | 加/去星标 · 标为未读 · 标为已读 · 完成或重新打开 |
| <kbd>c</kbd> 或 <kbd>⌘N</kbd> | 写新邮件 |
| <kbd>r</kbd> / <kbd>a</kbd> / <kbd>f</kbd> 或 <kbd>⌘R</kbd> / <kbd>⌘⇧R</kbd> / <kbd>⌘⇧F</kbd> | 回复 / 全部回复 / 转发 |
| <kbd>⌘Enter</kbd> | 发送正在写的邮件 |
| <kbd>Esc</kbd> | 返回列表；关闭菜单或对话框 |

macOS 应用的菜单另外提供 <kbd>⌘Z</kbd> / <kbd>⇧⌘Z</kbd>（撤销、重做）、<kbd>⌘X</kbd> <kbd>⌘C</kbd> <kbd>⌘V</kbd> <kbd>⌘A</kbd>、
<kbd>⌘M</kbd>（最小化）、<kbd>⌘H</kbd>（隐藏）和 <kbd>⌘Q</kbd>（退出）。窗口没有右键菜单，请用键盘复制粘贴。

## 语言与颜色

工作台默认使用英文，包括账户设置、同步提示和错误信息。邮件内容、用户设置的账户名称、
分类 ID 和已有服务端标签保持原样；日期使用英文格式和本机时区。

同一分类在侧栏、列表和预览中使用相同颜色：待处理为橙色、新发件人为蓝色、订阅为绿色、
订单为紫色、推广为玫红、旅行为青蓝、更新为金色、未分类为灰色。明暗主题分别调整亮度；
选中状态同时显示勾号和边线，文字名称始终保留。颜色修改只影响工作台显示。

新的信封小屋 Logo 同时用于页面、浏览器图标、Mac 启动器和[桌面应用](#下载应用)（启动器未签名；macOS 桌面应用的发布版已签名并公证）。可在 macOS 从矢量源文件重建图标：

```sh
uv run --with playwright python scripts/build_desktop_brand.py
python3 scripts/install_macos_workbench.py
```

浏览器测试会检查八种分类的明暗主题对比度、列表与侧栏颜色一致性，以及更改分类后的预览颜色。

## 数据与隐私

这是对旧 CLI“只输出 ID 和计数”的明确扩展：工作台会在**本机浏览器内存**中显示主题、
发件人和正文。正文在进程中最多缓存 128 封（含每个 Microsoft/IMAP 账户预读的最新 10 封），每封最多 256 KiB 文本及 50 条图片引用；退出后不保留正文缓存。
切换邮件或会话失效会释放图片 Blob。外链图片由浏览器直接请求，不发送 Referer 或工作台令牌；图片服务器能看到浏览器 IP 和图片 URL，并适用该服务器的 Cookie 与缓存策略。
正文从不写入磁盘。邮件列表会缓存到磁盘：`cache.sqlite` 保存每个文件夹已加载范围内的主题、发件人、日期、已读/星标、分类和 ID
（Gmail 另存邮件头元数据和同步位置），重启后立即显示，Gmail 只需读取变化部分；删除该文件即可清空。主动保存或发送前保存的草稿及附件会留在本机。没有遥测或远程分类。操作系统交换区、浏览器扩展及截屏仍属于本机环境。

`data_dir/workbench/` 中保存：

- `accounts.json`：账户名称、服务商、连接设置和邮箱身份哈希；IMAP 用户名属于个人数据。
- `workflow.sqlite`：账户隔离的消息哈希、手动分类、完成状态、操作版本；没有邮件内容。
- `cache.sqlite`：邮件列表缓存（主题、发件人、日期、已读/星标、分类、ID 及 Gmail 同步位置），不含正文、摘要或附件；仅当前用户可读写，未额外加密。
- `mail/mail.sqlite`：草稿正文、附件和操作回执，仅当前用户可访问，未额外加密；部分 SMTP 发送回执可能保留收件人地址。
- `accounts/`：OAuth 令牌，文件权限为仅当前用户读写；IMAP 密码存于系统凭据服务。
- `launch.json`：私密启动能力链接；`desktop.log`：不包含邮件内容的运行信息。

读取邮件仍需要连接原邮箱服务商；“on device”指分类与整理在本机运行，不代表断网后能下载邮件。
现有 Gmail 标签后台服务可继续独立运行。工作台不会自动使用一个账户的联系人关系来推断另一个账户。
详细威胁模型见 [隐私说明](privacy.md)。

## 恢复与验证

登录过期：账户旁“⋯”→“Reauthorize this mailbox”（重新授权）。网络故障：恢复网络后刷新，或等待下一轮同步。
系统钥匙串不可用时 IMAP 连接会停止，不降级为明文密码文件。模型失败时仍可查看邮件并手动分类。

本机测试命令：

```sh
uv run pytest tests/test_desktop_*.py
uv run --with playwright playwright install chromium
uv run --with playwright python scripts/check_desktop_ui.py
uv run --with playwright python scripts/check_mail_ui.py
uv run --with playwright python scripts/check_layout_ui.py
```

浏览器测试使用明确标注的合成双账户数据，检查真实本地 HTTP 操作、筛选、预览、完成/撤销、
分类持久化、键盘和 390/768/1280/1440 四种宽度的明暗主题。它不能证明未授权邮箱的真实连接。

真实多服务商验收还需各自授权的账户，测试新增邮件、授权过期、重新授权和客户端标签反馈。
邮件操作测试使用模拟服务商，覆盖写信、附件、草稿、回复、批量删除与恢复，以及发送结果不确定等情况。
真实发送和移动需使用获准的测试邮件另行验证，模拟测试不能证明服务商的真实往返结果。

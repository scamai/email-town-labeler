简体中文 · [English](desktop-workbench.en.md)

[返回 README](../README.md)

# 本机统一邮件工作台

[打开工作台](#打开工作台) · [连接邮箱](#连接邮箱) · [统一处理](#统一处理) · [数据与隐私](#数据与隐私) · [恢复与验证](#恢复与验证)

工作台把已授权账户的**收件箱**放进同一列表，使用同一套分类、搜索和完成状态。
它通过 Gmail API、Microsoft Graph 或 IMAP 读取邮件，在本机分类；原邮箱仍能在
Outlook、Apple Mail 或 Gmail 中照常使用。工作台的手动分类与完成状态目前只保存在本机。

这是本地运行的软件源码和浏览器界面，尚不是已签名、公证或在应用商店发布的产品。

## 打开工作台

已安装 CLI 的电脑：

```sh
email-town-labeler desktop --background
```

首次安装可使用 `uv tool install 'email-town-labeler[onnx,desktop]'`；使用本分支源码时运行
`uv tool install --editable '.[onnx,desktop]'`。`desktop` extra 为 IMAP 提供系统钥匙串支持。
本地模型使用现有默认配置；未安装的模型仍需下载模型文件，分类内容不上传。

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

## 连接邮箱

已有默认 Gmail 授权会自动纳入。其他账户从右上角“添加账户”连接，并设置可辨认的名称。
应用不会读取 Outlook 或 Apple Mail 保存的登录凭据。

| 邮箱 | 连接方式 | 前提 |
|---|---|---|
| Gmail / Google Workspace | Google 浏览器授权，只读 Gmail 权限 | 默认配置目录中已有 Google Desktop OAuth 客户端文件；首次配置见 [Google 设置](google-cloud-setup.md) |
| 已用 CLI 配置的其他 Gmail | 填写该账户的 `--home` 绝对目录 | 该目录包含自己的有效 Gmail 授权 |
| Microsoft 365 / Outlook.com | Microsoft 设备代码登录，读取邮件和用户身份 | 自己注册的公共客户端应用 ID，启用 device flow；租户可能要求管理员同意 |
| iCloud / 其他 IMAP | TLS IMAP 服务器、用户名、应用专用密码 | 服务商支持 IMAP；系统钥匙串可用 |

Microsoft 应用需支持你的账户类型，使用 delegated `Mail.Read` / `User.Read` 与
`offline_access`；参考 [Microsoft 设备授权文档](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)。
不能把 Gmail 授权拿来读取 Microsoft 账户。每个邮箱都需要它自己的登录授权。
Exchange 本地部署不等于 Microsoft Graph 邮箱；需管理员允许的 IMAP 或其他适配。

账户旁的“⋯”提供暂停、恢复和重新授权。暂停只是停止自动同步，已载入邮件仍能查看。
重新授权保留原账户的整理状态，并检查邮箱身份；登录其他邮箱请使用“添加账户”。

## 统一处理

- 左侧选择所有账户或单个账户，再按“今天”、历史待处理、分类和完成状态筛选。
- 中间显示账户、主题、发件人、分类；搜索只查当前已载入的主题和发件人。
- 打开邮件可预览纯文本，在本机更改分类或标记完成。支持批量操作和撤销。
- 完成不会删除、移动、归档或标记原邮件已读，也不会移除原来的分类标签。
- Gmail 已有项目分类标签会直接显示；未分类邮件、Microsoft 和 IMAP 邮件使用共享的本机模型。
- 本机手动分类优先于自动分类，并与完成状态独立。它目前不写回服务端，也不加入旧 CLI 的学习反馈。
- 正文中的图片、附件和 HTML 不执行；Gmail/Outlook 支持跳回原邮件页面。回复和附件操作仍在原邮件客户端完成。

默认每账户载入最近 100 封收件箱邮件；“载入更多”每次增加 100，最高每账户 1000。
列表会明确提示截断，计数只代表已载入邮件。最多 32 个账户；每次最多处理 500 封。
已归档/移出收件箱的邮件不在这个列表中，本机整理状态仍保留。

自动同步间隔 60 秒，界面每 5 秒读取本机状态。一个账户断线不会清空其他账户；断线账户保留
最后一次列表并显示错误。完成记录重启后仍存在；撤销令牌仅在当前进程中保存。

## 数据与隐私

这是对旧 CLI“只输出 ID 和计数”的明确扩展：工作台会在**本机浏览器内存**中显示主题、
发件人和正文。正文在进程中最多缓存 64 封，每封最多 256 KiB；退出后不保留正文缓存。
没有应用层的邮件内容磁盘缓存、遥测或远程分类。操作系统交换区、浏览器扩展及截屏仍属于本机环境。

`data_dir/workbench/` 中保存：

- `accounts.json`：账户名称、服务商、连接设置和邮箱身份哈希；IMAP 用户名属于个人数据。
- `workflow.sqlite`：账户隔离的消息哈希、手动分类、完成状态、操作版本；没有邮件内容。
- `accounts/`：OAuth 令牌，文件权限为仅当前用户读写；IMAP 密码存于系统凭据服务。
- `launch.json`：私密启动能力链接；`desktop.log`：不包含邮件内容的运行信息。

读取邮件仍需要连接原邮箱服务商；“on device”指分类与整理在本机运行，不代表断网后能下载邮件。
现有 Gmail 标签后台服务可继续独立运行。工作台不会自动使用一个账户的联系人关系来推断另一个账户。
详细威胁模型见 [隐私说明](privacy.md)。

## 恢复与验证

登录过期：账户旁“⋯”→“重新授权此邮箱”。网络故障：恢复网络后刷新，或等待下一轮同步。
系统钥匙串不可用时 IMAP 连接会停止，不降级为明文密码文件。模型失败时仍可查看邮件并手动分类。

本机测试命令：

```sh
uv run pytest tests/test_desktop_*.py
uv run --with playwright playwright install chromium
uv run --with playwright python scripts/check_desktop_ui.py
```

浏览器测试使用明确标注的合成双账户数据，检查真实本地 HTTP 操作、筛选、预览、完成/撤销、
分类持久化、键盘和 390/768/1280/1440 四种宽度的明暗主题。它不能证明未授权邮箱的真实连接。

真实多服务商验收还需各自授权的账户，测试新增邮件、授权过期、重新授权和客户端标签反馈。
不要用任意一封真实业务邮件测试服务端修改；工作台当前的整理操作只写本机状态。

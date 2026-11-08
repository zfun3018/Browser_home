# 浏览器首页部署（Windows 原生服务）

首页和本地备份服务由 WinSW 注册为 Windows 服务，监听本机地址：

- 首页：`http://127.0.0.1:3011`
- 备份服务：`http://127.0.0.1:8010`（仅供首页调用）

运行时使用 Python、SQLite 和本机文件目录。前端构建产物已经包含在项目中，日常启动不需要 Node.js；只有修改前端后重新构建时才需要 Node.js 和 pnpm。

## 首次安装

在管理员 PowerShell 中执行：

```powershell
Set-Location 'F:\浏览器导航页'
Set-ExecutionPolicy -Scope Process Bypass
.\native\install.ps1
```

安装脚本会使用现有的 `homepage-data` 和 Obsidian 笔记，不会重新导入或改写当前书签。安装后检查：

```powershell
Get-Service BrowserHomepage, BrowserHomepageBackup
Invoke-WebRequest http://127.0.0.1:3011/health
```

## 服务管理

```powershell
.\native\start.ps1
.\native\stop.ps1
.\native\restart.ps1
.\native\uninstall.ps1
```

`uninstall.ps1` 只移除服务，不删除数据。日志位于 `logs`。

## 数据和备份

- `homepage-data/bookmarks.db`：书签和分组。
- `homepage-data/state.db`：布局、搜索设置和备份状态。
- `homepage-data/site-icons`：网站图标缓存。
- `homepage-data/note-conflicts`：笔记冲突副本。
- `backups`：新格式 SQLite 备份 ZIP。
- `homepage-secrets/capture_token.txt`：浏览器扩展的一键收藏令牌。

备份服务默认每周自动生成一次备份，也可以在首页手动创建。每次备份成功后，`backups/browser-homepage-backup-latest.zip` 都会原子替换为最新内容，旧的同名规则历史文件会清理；导入前生成的回滚包会单独保留。新服务只接受 `formatVersion: 2` 的 SQLite 归档。

迁移完成后留下的 `data`、`pgdata`、`meili_data` 和旧 ZIP 是历史数据，不参与当前服务运行。确认不再需要回滚后，可由管理员自行归档或删除。

## 开发检查

后端测试：

```powershell
Set-Location 'F:\浏览器导航页\custom-homepage'
..\.venv\Scripts\python.exe -m pytest backend/tests -q
```

前端构建和测试：

```powershell
Set-Location 'F:\浏览器导航页\custom-homepage\frontend'
pnpm test --run
pnpm run build
```

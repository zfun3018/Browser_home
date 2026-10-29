# 浏览器首页部署（Windows + Docker Desktop）

首页通过 Docker Compose 部署在 `F:\浏览器导航页`，只监听本机 `127.0.0.1:3011`。Linkwarden 仍在 `http://localhost:3010`，现有数据库和归档数据目录保持原路径。

## 首次配置

1. 确保 Docker Desktop 正在运行，并且 Docker Desktop 可以访问 F: 盘。
2. 在 Linkwarden 中创建 API Token。不要把 Token 发到聊天或放进仓库。
3. 将 Token 作为纯文本写入 `F:\浏览器导航页\homepage-secrets\linkwarden_api_token.txt`。该文件通过 Docker secret 只提供给首页后端；Compose 文件型 secret 在磁盘上仍是明文，请在 Windows 文件属性的“安全”页限制为当前 Windows 用户和管理员可读。不要把 Token 放入 `.env`。
4. 确认这个 Obsidian 文件存在：

   `F:\文件\Documents\Obsidian仓库\Obsidian-note\0.01A-Day or Day\浏览器首页笔记.md`

   Compose 只挂载这一个 Markdown 文件；不会挂载整个仓库。

## 启动和停止

在 PowerShell 中运行：

```powershell
Set-Location 'F:\浏览器导航页'
docker compose up -d --build
docker compose ps
```

打开 `http://localhost:3011`。把该地址设置为浏览器启动页或新标签页。需要首次创建或调整收藏时，仍可打开 `http://localhost:3010`。

停止容器但保留数据：

```powershell
Set-Location 'F:\浏览器导航页'
docker compose stop
```

Docker Desktop 的开机启动和 Compose 服务的 `restart: always` 会让服务在 Docker 引擎启动后自动恢复。

## 数据和备份

- `homepage-data`：首页布局、搜索引擎设置、笔记冲突副本。
- `data`、`pgdata`、`meili_data`：原有 Linkwarden 上传/归档、PostgreSQL 和 Meilisearch 数据，路径不变。
- Obsidian Markdown 文件仍由 Obsidian 仓库保存；建议同时用 Obsidian 仓库自己的备份方式保护它。
- `homepage-secrets\linkwarden_api_token.txt`：API Token 明文文件，备份时按密码文件保护。

首页的“备份”按钮会在浏览器下载完整 ZIP，包含 Linkwarden PostgreSQL 数据库导出、附件、首页状态库与冲突副本、Obsidian 笔记，以及部署 Compose、`.env` 和 API Token 文件。下载成功后首页记录最近备份时间；超过 7 天或没有备份记录时，首页会显示提醒。下载目录由浏览器设置决定。

**备份 ZIP 中的 `.env` 和 `homepage-secrets/linkwarden_api_token.txt` 是未加密明文凭据。** 请像保管密码文件一样保护 ZIP；不要上传到公开网盘、发给他人或放进代码仓库。完成异机迁移后，确认需要的文件已安全保存，再删除不需要的副本。

升级镜像后执行 `docker compose up -d --build`。不要删除上述数据目录。不要把运行中的 `pgdata` 文件夹副本当作唯一数据库备份。

### 在首页导入备份

1. 点击“导入备份”并选择首页下载的 ZIP。系统会先验证归档清单、哈希、数据库和文件路径，不合格时不会改动当前数据。
2. 验证通过后，首页会先生成并下载当前电脑的完整回滚备份。必须等下载完成，页面才允许继续。
3. 核对备份日期和替换范围，然后勾选确认并执行导入。导入会替换 Linkwarden 数据库与附件、首页设置、Obsidian 笔记和笔记冲突副本；过程中书签和笔记编辑会短暂暂停。
4. 当前电脑正在使用的 `docker-compose.yml`、`.env` 和 API Token 不会被导入包覆盖。导入失败时服务会用恢复日志回滚原数据；如果首页报告自动回滚需要人工处理，请保留回滚 ZIP 和恢复日志目录 `homepage-backup-staging`，并停止继续写入数据。

### 在另一台电脑恢复

1. 安装 Docker Desktop，创建 `F:\浏览器导航页` 部署目录，并从 ZIP 中取出 `deployment/.env` 和 `deployment/homepage-secrets/linkwarden_api_token.txt`。这两个文件含明文凭据，请先限制为当前 Windows 用户及管理员可读，不要粘贴到聊天或命令行。
2. 将 `deployment/docker-compose.yml` 放入部署目录，并检查其中 Obsidian 笔记挂载路径是否符合新电脑实际位置。保留 ZIP 中的原始版本，按需调整工作副本的绝对路径。
3. 在部署目录创建空的 `data`、`homepage-data`、`homepage-secrets`、`homepage-backup-staging` 和 `pgdata` 目录，将 Token 放到 Compose 引用的位置。不要恢复旧机器的运行中 `pgdata` 目录。
4. 执行 `docker compose up -d --build`，等待 PostgreSQL、Linkwarden 和首页服务启动；然后在首页使用“导入备份”导入 ZIP。导入会恢复数据库、附件、首页状态、Obsidian 笔记与冲突副本，同时保留这台电脑的 Compose、`.env` 和 Token。
5. 打开首页和 Linkwarden 检查数据。Meilisearch 索引不在 ZIP 中，Linkwarden 会根据 PostgreSQL 数据重新建立索引；索引完成前搜索结果可能不完整。

如果首页暂时不可用，可从 ZIP 的 `linkwarden/database.dump` 用 PostgreSQL 16 的 `pg_restore` 恢复数据库，将 `linkwarden/data/` 下的内容还原到部署目录 `data/`，将 `homepage/state.db` 和 `homepage/note-conflicts/` 还原到 `homepage-data/`，并把 `homepage/浏览器首页笔记.md` 放回 Compose 挂载的 Obsidian 文件位置。手动恢复前先停止 Compose 服务并复制一份当前目录作为回滚点；不要覆盖当前 `.env` 或 Token，除非你正在明确执行凭据迁移。

## 同步说明

- Linkwarden 是收藏名称、网址、简介、图标字段和分类的来源；首页布局及显示方式保存在 `homepage-data`。
- 首页会直接访问收藏站点的 `/favicon.ico` 显示网站图标。某些使用自定义 favicon 路径的网站会使用 Linkwarden 图标作为回退。
- 网页笔记保存到指定 Obsidian Markdown 文件。文件被 Obsidian 修改时，打开的首页会检测变化；同时编辑时会提示冲突并保留两个版本。
- 若 API Token 文件为空或无效，首页仍能打开，Linkwarden 收藏同步会显示离线/配置状态。Token 轮换后重启首页容器：

  ```powershell
  docker compose restart homepage
  ```

首次应用双 Docker 网络时，Compose 会重建现有 Linkwarden、PostgreSQL 和 Meilisearch 容器，可能造成短暂中断；数据库及归档目录仍使用原来的 bind mount。之后只改首页代码时通常只需重建首页服务。

## 本机开发和测试

运行容器只安装服务所需的 Python 包。需要本机运行后端测试时，在项目目录安装开发依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```


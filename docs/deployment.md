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

升级镜像后执行 `docker compose up -d --build`。不要删除上述数据目录。若需要完整备份 Linkwarden，先按 Linkwarden/PostgreSQL 的方式导出数据库，再备份 `data`、`homepage-data` 和 Obsidian 文件；不要把运行中的 `pgdata` 文件夹副本当作唯一数据库备份。

## 同步说明

- Linkwarden 是收藏名称、网址、简介、图标字段和分类的来源；首页布局及显示方式保存在 `homepage-data`。
- 首页会直接访问收藏站点的 `/favicon.ico` 显示网站图标。某些使用自定义 favicon 路径的网站会使用 Linkwarden 图标作为回退。
- 网页笔记保存到指定 Obsidian Markdown 文件。文件被 Obsidian 修改时，打开的首页会检测变化；同时编辑时会提示冲突并保留两个版本。
- 若 API Token 文件为空或无效，首页仍能打开，Linkwarden 收藏同步会显示离线/配置状态。Token 轮换后重启首页容器：

  ```powershell
  docker compose restart homepage
  ```

首次应用双 Docker 网络时，Compose 会重建现有 Linkwarden、PostgreSQL 和 Meilisearch 容器，可能造成短暂中断；数据库及归档目录仍使用原来的 bind mount。之后只改首页代码时通常只需重建首页服务。


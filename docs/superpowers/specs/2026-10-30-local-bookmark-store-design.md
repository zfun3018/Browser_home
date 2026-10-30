# 本地书签库与浏览器一键收藏设计

## 目标

让浏览器首页全天运行时只依赖一个轻量的本地 SQLite 书签库，保留当前首页的分组、排序、折叠、编辑、描述抓取、图标缓存、搜索引擎设置、Markdown 笔记和备份恢复功能，并提供 Chrome/Edge 浏览器一键收藏当前页面。

## 方案

首页后端新增 `SqliteBookmarkStore`，实现当前 `LinkwardenClient` 被首页使用的四个行为：读取全部链接、读取分组、读取单个链接、带乐观并发校验地更新链接。书签数据库独立保存为 `homepage-data/bookmarks.db`，保留 Linkwarden 的整数 ID，以避免现有布局设置失效；迁移时保留原分类和书签字段，缺失分类时创建“未分类”。

`POST /api/capture` 接收扩展发送的当前网址、标题和可选分组。后端只允许 HTTP(S) 且拒绝用户名密码，使用本地 capture token 校验请求；同一网址在同一分组重复提交时返回现有书签，避免工具栏按钮重复点击产生垃圾数据。

备份服务改为 profile 启动，并在本地模式下备份 SQLite 书签库、首页状态、冲突副本和 Obsidian 笔记。旧 PostgreSQL/Linkwarden 备份格式在迁移阶段保留，迁移完成后默认 Compose 不启动旧服务。

浏览器扩展使用 Manifest V3，声明当前标签页读取和本机首页地址权限，提供工具栏按钮、右键菜单和可选快捷键。扩展保存首页地址与 capture token，成功收藏后显示浏览器通知。

## 保留的行为

- 首页分组、拖拽排序、折叠和每个链接的显示设置继续使用现有 `StateStore`。
- 书签编辑继续使用 `/api/links/{id}`，保留 expected 字段冲突检测。
- 网站简介和图标抓取继续复用现有后端服务。
- 搜索引擎、日历、Obsidian 笔记和备份导入导出继续可用。
- Linkwarden 数据先导入并校验，旧容器和旧目录在确认迁移成功前不删除。

## 内存目标

移除 Linkwarden、PostgreSQL 和 Meilisearch 后，常驻容器只包含首页服务；备份服务默认停止，仅在备份或恢复时启动。Docker/WSL 的固定开销仍存在，但应用容器工作集应从当前约 599 MiB 降到首页约 72 MiB。

## 回滚

旧服务保留为 `legacy` profile；迁移失败时重新启动旧 profile，并将首页的 `BOOKMARK_BACKEND` 改回 `linkwarden`。迁移脚本不删除源数据。

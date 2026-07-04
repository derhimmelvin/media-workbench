# AGENTS.md

本文件面向 Codex、其他 agent 和技术协作者，说明当前项目事实、不可破坏的约束、常用命令和协作规则。

## 项目事实

项目根目录：`/Users/yifan/Desktop/projects/mvp`

当前产品是本地 Web 版 B站下载器 v2.2：

- 后端：FastAPI、SQLite、yt-dlp、FFmpeg、keyring。
- 前端：Vue 3、Vite、TypeScript、Element Plus、Pinia。
- 任务执行：SQLite 持久化任务，本地后台 worker 池按设置执行任务级并发。
- 资源类型：视频、音频、封面、字幕。
- 当前视频容器：`mp4`、`mkv`。
- 当前音频输出：`m4a`、`mp3`。
- 当前字幕输出：`srt`、`txt`。
- 阶段二 2.1 已支持批量文本解析和自定义文件名；阶段二 2.2 已支持字幕下载最小闭环。

## 阅读入口

优先阅读顺序：

1. [README.md](README.md)
2. [docs/文档索引.md](docs/文档索引.md)
3. [docs/开发启动说明.md](docs/开发启动说明.md)
4. [docs/API接口说明.md](docs/API接口说明.md)
5. [docs/测试验收清单.md](docs/测试验收清单.md)
6. [哔哩哔哩下载场景功能清单、架构设计、技术选型.md](哔哩哔哩下载场景功能清单、架构设计、技术选型.md)

## 关键文件

后端：

- `backend/app/main.py`：FastAPI 路由、启动生命周期、健康检查、任务 API。
- `backend/app/schemas.py`：API 请求/响应模型和枚举约束。
- `backend/app/db.py`：SQLite schema 和基础数据访问。
- `backend/app/task_executor.py`：任务队列、状态流转、WebSocket 广播。
- `backend/app/extractors/base.py`：解析器抽象接口。
- `backend/app/extractors/yt_dlp_bilibili.py`：B站解析、下载、合并、封面下载。
- `backend/app/auth.py`：Cookie 钥匙串保存与脱敏摘要。
- `backend/app/media.py`：封面代理下载。
- `backend/app/utils.py`：路径、时间、FFmpeg 检查、打开目录、文件名清洗等工具函数。

前端：

- `frontend/src/App.vue`：主工作台界面和交互状态。
- `frontend/src/api.ts`：前端 API 类型和请求封装。
- `frontend/src/batch.ts`：批量文本提取、去重、逐行任务选项和 payload 辅助函数。
- `frontend/src/styles.css`：全局样式。
- `frontend/src/api.test.ts`：前端 API 辅助函数测试。

脚本：

- `scripts/check-env.sh`：环境检查。
- `scripts/dev.sh`：启动后端和前端开发服务。

## 不可破坏的产品约束

- 合规弹窗进入页面后必须持续显示，直到用户点击同意。
- 未同意合规声明前，不允许解析或提交下载任务。
- Cookie 明文不得写入 SQLite、日志或普通文件。
- 系统钥匙串不可用时，后端必须拒绝保存 Cookie，并返回可见错误。
- 下载视频时必须同时选择音频流，避免输出无声音视频。
- 封面是可选资源，不应默认强制下载。
- 字幕是可选资源，不应默认强制下载；弹幕不属于当前字幕下载范围。
- 仅音频下载时，不应展示或要求视频容器。
- `flv` 不属于当前支持的容器选项。
- 设置中的同时下载任务数已生效，用于控制任务级并发下载数量；默认 1，上限 4。
- 清空任务列表只能清除已完成、失败、取消的任务记录，不删除本地下载文件，不影响运行中任务。
- 完成任务的打开目录按钮只能用于已完成任务。

## 常用命令

```bash
bash scripts/check-env.sh
bash scripts/dev.sh

backend/.venv/bin/python -m pytest backend/tests

cd frontend
npm test
npm run build
```

如果本机默认 `python3` 低于 3.10，但已安装 Python 3.11：

```bash
PYTHON_BIN=python3.11 bash scripts/check-env.sh
```

## 技术协作规则

- 修改实现前先核对 [docs/API接口说明.md](docs/API接口说明.md) 和 [docs/测试验收清单.md](docs/测试验收清单.md)。
- 新增或变更 API 时，同步更新 `backend/app/schemas.py`、`frontend/src/api.ts` 和 API 文档。
- 新增资源类型、任务状态、容器格式或输出格式时，同步更新用户指南、测试验收清单和产品架构文档。
- 修改下载行为时，必须覆盖至少一类后端测试或说明无法自动化的原因。
- 修改前端交互时，检查桌面和移动宽度下文本是否溢出。
- 需要查询库、框架、SDK、CLI 或云服务的当前官方用法时，优先使用 Context7 MCP 获取文档。

## 阶段状态

阶段二 2.2 已完成：

- 单链接解析。
- 批量文本解析、多行输入、自动去重和逐行资源设置。
- 视频/音频/封面/字幕下载。
- 音视频合并。
- 音频 `m4a`/`mp3` 输出。
- 字幕 `srt` 时间轴输出和 `txt` 纯文本输出。
- 自定义文件名和冲突文件名处理。
- 任务进度、时间戳、清空记录、打开目录。
- 合规声明和 Cookie 手动导入。
- 基础设置。

后续候选方向：

- 字幕内嵌、字幕翻译和更细粒度字幕管理。
- 封面嵌入、元数据写入和打包。
- 更细粒度的单任务内部资源并发。
- Cookie/会员资源和 412/403 场景增强。
- 桌面端封装。

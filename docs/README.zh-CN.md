# GitHub Star Index

让编程 AI 在开发任务中快速了解你收藏过的仓库，并依据功能、前提、限制和固定 commit 的源码证据，判断是否值得复用。AI 遇到有意义的选型时可以先查组件、动效库、工具或已有实现，再按需读取少量候选的功能与证据。网页用于查看索引覆盖、检索表现和证据，是维护与排查界面。

[需求与验收](REQUIREMENTS.md) · [AI 接手文档](HANDOFF.md) · [开发计划](PLAN.md) · [竞品](COMPETITORS.md) · [盈利与采用](BUSINESS_AND_ADOPTION.md) · [接口约定](INTERFACES.md)

## 给 AI 接入

stdio MCP 提供渐进读取的工具：

- `find_reusable_repositories`：根据具体任务找最多 5 个候选。
- `inspect_repository`：读取候选的功能、使用前提、限制和卡片状态。
- `read_repository_evidence`：只取少量固定 commit 的源码证据。
- `index_status`、`search_repositories`、`get_repository` 和 `compare_repositories` 继续提供索引状态、常规搜索、完整单仓详情和对比。

在支持 stdio MCP 的客户端中配置 `mcp_server.py` 的绝对路径：

```powershell
python mcp_server.py
python mcp_server.py --root <索引数据目录> --public-only
```

默认使用项目目录作为索引根目录，不依赖当前工作目录。搭配可移植的 [repository-reuse Skill](../skills/repository-reuse/SKILL.md)，让 AI 在开发任务涉及组件、库、工具或已有实现的选型时考虑调用。Skill 提供路由指引，不等于所有客户端都已配置 MCP，也不保证每次都能自动触发。MCP 互操作尚未在真实客户端验收。工具参数与输出见[接口约定](INTERFACES.md)。

如果当前环境没有 MCP、但能访问本地索引，可使用同样按需读取的命令行。命令使用本仓库 checkout 中脚本的绝对路径，因此 AI 当前工作目录可以是其他项目；脚本目录与索引数据目录可以不同：

```powershell
python "C:\path\to\github-star-index\agent_context.py" --root "C:\path\to\my-index" find "实现一个带动效的 React 页面" --terms animation component --limit 5
python "C:\path\to\github-star-index\agent_context.py" --root "C:\path\to\my-index" inspect owner/repo --features "检索结果中的精确功能名"
python "C:\path\to\github-star-index\agent_context.py" --root "C:\path\to\my-index" evidence owner/repo --ids ev1 ev2
```

检索只负责召回候选；AI 还要根据功能证据和实际约束判断能否复用。索引本身不调用模型。

## 建立自己的索引

需要 Python 3.10+ 和 SQLite FTS5，无额外 Python 依赖。同步当前已登录的 GitHub 账号：

```powershell
gh auth login
python catalog_sync.py
python source_sync.py --all
python search.py build
```

首次同步会采集仓库元数据、文件树和可用的 README，不会自动生成深入功能卡。针对要深入了解的仓库，选择能证明关键行为的文件：

```powershell
python source_sync.py --repo owner/repo --paths src/main.py docs/usage.md
python analysis.py pack --repo owner/repo
```

资料包有文件、字符和行数上限，含行号、哈希和截断信息。把它交给已在使用的 AI 助手分析；审阅卡片草稿及其来源后再导入：

```powershell
python analysis.py ingest draft.json --repo owner/repo
python search.py build
```

覆盖已有卡片需要 `--replace`，旧卡会归档。导入会校验仓库、commit、文件哈希、行号和功能引用，但不会自动证明功能解释正确。详见[分析说明](../ANALYSIS_GUIDE.md)。

`python refresh.py` 更新清单与源码快照；commit 变化后旧卡标为过期，需复核后再更新。独立数据目录请先复制 `schema.json`，并在同步、分析、建索引和服务命令中使用同一个 `--root <目录>`。

## 本地网页预览

网页用于检查索引覆盖、搜索表现、卡片新鲜度和候选证据，不负责 AI 选型或自动推荐。使用隔离的合成数据体验：

```powershell
python demo.py
python webapp.py --demo
```

打开 http://127.0.0.1:8765。演示包含四个虚构仓库，数据在独立的 `data/demo/`，不覆盖个人收藏。查看个人索引时执行 `python webapp.py`。

## 检索与证据边界

当前检索基于 SQLite FTS5 中文/英文词法匹配，没有向量召回或模型重排。候选顺序不是适用性评分；没命中某个词表示未知，不代表不支持。源码分析是代表性抽样，不等于完整审计或运行验证。引用行与原文相符也不能单独证明语义结论准确。

仓库采集文本是待核查资料，不是代理指令。不要因为索引找到仓库就执行、安装其中的代码。

## 隐私与开源

个人清单、源码快照、卡片、数据库和生成目录留在本地忽略路径。HTTP 服务只监听本机；`--public-only` 会在检索和分页前排除私有及可见性未知的仓库，不提供公网托管或认证。项目代码采用 [MIT](../LICENSE)；采集的第三方文本仍保留原作者权利。详见[贡献指南](../CONTRIBUTING.md)。

当前尚未完成浏览器完整验收、真实客户端 MCP 互操作、跨平台安装检查和同条件推荐质量对比。仓库附带 `tests/` 单元测试（`python -m pytest tests/`），覆盖索引构建与检索、固定快照采集；MCP 互操作、网页和推荐质量仍待验收。

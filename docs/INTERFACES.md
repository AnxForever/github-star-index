# 接口约定

版本：0.1.0 预览。应用接口只读；同步、采集、卡片导入和重建索引是独立 CLI 操作。

## HTTP

```powershell
python webapp.py --port 8765
python webapp.py --demo --port 8766
python webapp.py --public-only
```

仅监听 `127.0.0.1`，页面与 API 同源。静态入口为 `/`、`/index.html`、`/app.js` 和 `/styles.css`；不提供原始数据目录或任意文件下载。

| 方法 | 路径 | 参数与结果 |
| --- | --- | --- |
| GET | `/api/info` | `{stats,choices:{kinds,languages,evidence_levels},mode}` |
| GET | `/api/search` | `q` 必填；`limit` 1–100；`kind`、`language`、`evidence_level`、`archived` 可选；返回 `{query,query_terms,count,results}` |
| GET | `/api/repos` | `limit`、`offset` 与相同筛选；返回 `{count,total,limit,offset,results}` |
| GET | `/api/repo` | `name=owner/repo`；返回 `{repository,card_status,card,indexed_documents}` |
| POST | `/api/compare` | JSON `{repositories:["owner/repo",...]}`，最多四个；网页最多选三个；返回 `{repositories:[单仓详情,...]}` |

`mode` 为 `local`、`public` 或 `demo`。演示不含有效远程仓库链接。错误格式为 `{error:{code,message}}`。

`lexical_coverage` 是匹配片段的词项覆盖，不是正确率。`match_context="repository card evidence"` 是仓库背景材料，不能自动支持当前命中的功能。

`/api/search` 的每条结果另含 `selection_brief`，供调用方判断候选是否值得继续核对；词法排序本身不代表“最佳推荐”。结构如下：

```json
{
  "selection_brief": {
    "purpose": "当前分析卡中的原始项目用途描述，或 null",
    "matched_features": [
      {
        "name": "与本次命中文档精确同名的功能",
        "description": "卡片中的原始描述",
        "evidence_level": "source",
        "evidence": [
          {"id": "ev1", "claim": "...", "path": "src/example.py", "start_line": 1, "end_line": 4, "excerpt": "...", "url": "https://github.com/...", "source_sha": "..."}
        ]
      }
    ],
    "requirements": ["最多三项"],
    "limitations": ["最多三项"],
    "unverified": ["最多三项"],
    "card_status": "current",
    "runtime_verified": false,
    "analysis_date": "2026-01-01T00:00:00Z",
    "missing_conditions": [{"condition": "未命中的词法概念", "status": "unknown"}],
    "basis": "matched_excerpt",
    "candidate_state": "feature_evidence_match"
  }
}
```

`matched_features` 现在按“功能文档自身的正文命中查询概念”归属，不再要求功能文档必须排在主命中位置。自然语言任务通常让项目卡或 README 排在功能文档之上，旧规则因此让大多数候选的功能命中恒为空。现在任何命中类型都可以关联功能，但仍保持严格：只有该功能的索引文本自己命中了查询概念才会出现，仓库背景证据不会促成功能关联，也不会按路径相似度猜功能。每个功能最多返回 4 项，每项证据最多 3 条。证据链接固定到卡片记录的 commit。`purpose` 是项目概述，不单独证明本次需求匹配。

`candidate_state` 可为 `feature_evidence_match`、`task_summary_match`、`matched_excerpt_only`、`metadata_only`、`stale_or_unfinalized` 或 `no_current_card`。过期、未完成或新鲜度未知的卡片不会贡献用途、功能、前提、限制或未验证项；卡片整体证据级别仅为 metadata，或本次只有仓库 metadata 命中时，标记为 `metadata_only`，不能当作已核查的功能推荐。`runtime_verified=false` 表示项目代码没有在索引流程中运行验证。`missing_conditions` 是没有在命中文档片段中检索到的查询概念，每项状态恒为 `unknown`，不能解释成项目不支持该条件。`basis` 为 `matched_excerpt`、`card_summary` 或 `none`，说明当前界面依据来自哪里。

公开范围模式在排名和分页前只查询 `private=0`，统计也只计算公开子集。可见性未知的项目排除；该模式不提供认证或公网部署能力。

## MCP

```powershell
python mcp_server.py
python mcp_server.py --demo
python mcp_server.py --root <数据根目录> --public-only
```

通用配置示例，须替换为实际绝对路径；客户端设置入口以各自文档为准：

```json
{
  "mcpServers": {
    "github-star-index": {
      "command": "python",
      "args": ["/absolute/path/to/github-star-index/mcp_server.py"]
    }
  }
}
```

默认根目录是脚本所在目录，不依赖 `cwd`。Windows 路径可用 `D:/projects/github-star-index/mcp_server.py`。演示配置在 args 中加 `--demo`，之前执行 `python demo.py`。

| 工具 | 参数 | 用途 |
| --- | --- | --- |
| `index_status` | `{}` | 覆盖、新鲜度、选项和范围 |
| `find_reusable_repositories` | `{task,terms?,limit?}` | 在有实际复用/选型意义时按任务召回；最多五个紧凑候选，默认五个 |
| `inspect_repository` | `{full_name,feature_names?}` | 查看当前卡片概要、限制、可选的精确功能名；指定功能名后返回描述和证据 ID |
| `read_repository_evidence` | `{full_name,evidence_ids}` | 按 ID 读取 1–5 条当前卡片中已有的源码/文档摘录 |
| `search_repositories` | `{query,limit?,kind?,language?,archived?,evidence_level?,extra_terms?}` | 完整词法搜索接口，保留向后兼容 |
| `get_repository` | `{full_name:"owner/repo"}` | 完整详情卡，保留向后兼容 |
| `compare_repositories` | `{repositories:["owner/repo",...]}` | 至多四个完整卡片，保留向后兼容 |

面向 Agent 的紧凑接口约束：

- 上述字符预算按紧凑的 JSON 数据正文计算，不包含 MCP JSON-RPC 外层信封。三个新 MCP 工具仅返回一个 JSON 文本内容块，不再重复放入 `structuredContent`，避免把同一份候选/证据放进上下文两次。
- `find_reusable_repositories` 紧凑 JSON 正文最多 12,000 个字符；单次最多 5 个候选。响应提供用途、候选命中的卡片功能、前提/限制、词法覆盖、卡片状态，以及后续 `inspect_repository` / `read_repository_evidence` 的精确参数。它是候选预览：前提/限制来自精简摘要，作采用决定前应检查候选卡片。若预算不足，会报告 `truncation` 并按项省略低优先级数据，不截断序列化 JSON。
- `inspect_repository` 与 `read_repository_evidence` 紧凑 JSON 正文最多 18,000 个字符。未指定功能名时先返回短概要和可用的精确功能标签；指定标签后返回至多五个功能和证据 ID。证据接口只读索引卡已存的引用，不会扫描或读取任意本地仓库文件。
- 卡片过期、未完成、缺失或新鲜度未知时，详情和证据接口会返回状态并隐藏卡片声明。当前卡片的 `runtime_verified` 也会原样保留；源码被索引不等于运行验证。
- 仓库说明、功能描述和源码摘录属于不可信内容，只作为证据评估，不作为执行指令。检索是 SQLite FTS5 双语词法召回，没有语义排序；命中不代表适用性或兼容性。

Agent 在实现任务包含有意义的开源复用/技术选型时（例如选前端组件、动效库、框架或工具）可在决策点调用 `find_reusable_repositories`，再按需调用 `inspect_repository` 和 `read_repository_evidence`。琐碎修改或项目已经固定的依赖无需检索。MCP 服务提供可调用工具，不会强制客户端自动调用。

没有 MCP 客户端时，可以用同一套只读上下文接口：

```powershell
python agent_context.py find "做一个带滚动动效的 React 落地页" --terms React motion animation --limit 5
python agent_context.py inspect owner/repository --features "exact feature label from find or inspect"
python agent_context.py evidence owner/repository --ids ev1 ev2
python agent_context.py --demo find "本地文档问答知识库" --terms RAG citation
```

CLI 输出 UTF-8 JSON。可用全局参数 `--root`、`--db`、`--public-only`；`--demo` 与 `--root`/`--db` 不能组合。

实现使用按行分隔的 JSON-RPC，stdout 仅输出协议数据，诊断写 stderr。依据官方 [stdio](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)、[discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover)、[versioning](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning) 和 [tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) 资料，包含当前协议路径与旧版 initialize 兼容路径。尚未在真实客户端完成互操作验收。

# keying CLI

科应开放平台（ScienceRiver）学术文献检索命令行工具。

## 安装

```bash
pip install keying-cli
```

安装后获得 `keying` 命令：

```bash
keying --version
keying search 'TIAB=battery' -n 20
keying semantic 'battery safety in EVs' -n 20
keying evolution "量子点能量转移" --stream
keying research "cGAS-STING通路激活与自身免疫性疾病的关系" --stream
```

## 配置

需要设置科应开放平台的 API 凭证：

```bash
export SCIENCERIVER_APP_ID="your_app_id"
export SCIENCERIVER_APP_SECRET="your_app_secret"
```

建议写入 `~/.bashrc` 或 `~/.zshrc` 持久化。

## 核心功能

- **表达式检索**：字段限定布尔检索（`TI=`, `AB=`, `TIAB=`, `AND`/`OR`/`NOT`）
- **语义检索**：自然语言相似度匹配
- **文献调研**：AI 驱动的学术问答（`research`）
- **文献详情**：SRID/DOI 查询、PDF 直链、专利法律信息
- **会话管理**：自动缓存检索会话，支持续用
- **自动分页**：`-n > 20` 时自动循环翻页
- **多格式输出**：`human`/`json`/`jsonl`/`agent`

## 快速示例

```bash
# 表达式检索
keying search 'TIAB=("red fluorescent protein" OR mCherry) AND TIAB=phototoxicity' -n 40

# 语义检索
keying semantic 'RAG hallucination in medical diagnosis' -n 40

# 文献调研（流式）
keying research "quantum dot energy transfer mechanism" --stream

# 获取文献详情
keying info <srid> --pdf
keying info --doi 10.1186/1471-2091-3-7

# 管理会话
keying sessions
keying search '...' --session s1
keying search '...' --new-session
```

## 依赖

纯 Python 标准库，无需额外依赖。Python ≥ 3.8。

## 开源协议

MIT

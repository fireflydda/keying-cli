#!/usr/bin/env python3
"""
keying: keyingAI爱好者制作的科应开放平台文献检索 CLI
支持论文、专利检索,迭代探索模式,去重排序,JSON/人类可读输出
"""

__version__ = "1.1.0"

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Optional

# ============================================================================
# 配置 - 从环境变量读取凭证(客户需自行配置)
# ============================================================================

BASE_URL = os.environ.get("KEYING_BASE_URL", "https://open.scienceriver.com")
APP_ID = os.environ.get("SCIENCERIVER_APP_ID")
APP_SECRET = os.environ.get("SCIENCERIVER_APP_SECRET")
TIMEOUT = 60

# 启动校验:凭证必须配置
if not APP_ID or not APP_SECRET:
    print("Error: SCIENCERIVER_APP_ID and SCIENCERIVER_APP_SECRET must be set.", file=sys.stderr)
    print("       e.g.  export SCIENCERIVER_APP_ID=your_app_id", file=sys.stderr)
    print("              export SCIENCERIVER_APP_SECRET=your_app_secret", file=sys.stderr)
    sys.exit(1)

# Token 缓存文件(~/.cache/keying/token.json)
TOKEN_CACHE_DIR = os.path.expanduser("~/.cache/keying")
TOKEN_CACHE_FILE = os.path.join(TOKEN_CACHE_DIR, "token.json")

# 文献数据模型字段定义(与科应官方 API fieldSet 一致)
DOC_FIELDS = {
    "srid": {"type": "string", "description": "科应文献平台内部的唯一记录标识"},
    "doi": {"type": "string|null", "description": "论文或专利的数字对象唯一标识符"},
    "originalTitle": {"type": "string", "description": "论文或专利的原始语种标题"},
    "chineseTitle": {"type": "string", "description": "文献的中文翻译标题,可能为空"},
    "originalAbstract": {"type": "string|null", "description": "原始语种撰写的摘要全文"},
    "chineseAbstract": {"type": "string|null", "description": "中文翻译的摘要内容"},
    "pubMedId": {"type": "string|null", "description": "在PubMed数据库中的唯一收录编号"},
    "featuredTags": {"type": "array[string]", "description": "属性标签集合:综述、1区、2区、3区、4区、A类期刊、临床试验等"},
    "issue": {"type": "integer|null", "description": "期刊在该卷内的期号"},
    "volume": {"type": "integer|null", "description": "期刊的卷号,通常按年递增"},
    "startPage": {"type": "integer|null", "description": "文献在来源期刊或会议录中的起始页"},
    "endPage": {"type": "integer|null", "description": "文献的结束页"},
    "originalAuthors": {"type": "array[string]", "description": "文献作者姓名列表(原文形式)"},
    "orcids": {"type": "array[string]", "description": "作者的ORCID标识符,可能包含多位作者的ID"},
    "orcidNames": {"type": "array[string]", "description": "ORCID关联的作者姓名"},
    "authorAffiliations": {"type": "array[string]", "description": "每位作者对应的机构名称列表"},
    "institution": {"type": "string|null", "description": "文献中标注的机构原文"},
    "originalAuthorAffiliation": {"type": "string|null", "description": "作者所属机构的原始署名写法"},
    "affiliationCountry": {"type": "string|null", "description": "机构所在国家或地区"},
    "dataSource": {"type": "string", "description": "记录来源数据库以及刊载的期刊或会议名称"},
    "issn": {"type": "array[string]", "description": "国际标准连续出版物号,可能包含印刷版和电子版"},
    "publisher": {"type": "string|null", "description": "负责出版该文献的出版社或组织"},
    "publicationDate": {"type": "string", "description": "具体发表日期,格式通常为 yyyyMMdd"},
    "publicationYear": {"type": "integer|null", "description": "文献公开发表的年份"},
    "applicationDate": {"type": "string|null", "description": "专利的申请日期"},
    "applicationNumber": {"type": "string|null", "description": "专利的申请编号"},
    "publicationNumber": {"type": "string|null", "description": "专利的公开或授权公告号"},
    "inventors": {"type": "array[string]", "description": "专利发明人姓名"},
    "assignees": {"type": "string|null", "description": "专利权利人,即专利权持有者"},
    "hasPdf": {"type": "boolean", "description": "是否可提供PDF"},
    "docType": {"type": "string", "description": "文献类型标识:paper(论文)、patent(专利)、standard(标准)"},
    "isOpenAccess": {"type": "boolean", "description": "该文献是否为开放获取模式"},
    "hasAiQa": {"type": "boolean", "description": "是否支持基于全文的AI问答功能"},
    "refIds": {"type": "array[string]", "description": "该文献所引用的参考文献的科应SRID列表"},
    # CLI 内部便利字段(非官方 API 字段)
    "pdfUrl": {"type": "string|null", "description": "PDF 链接(科应暂无直链,标记是否有 PDF)"},
    "url": {"type": "string", "description": "DOI 链接或专利公开号链接"},
    "_query": {"type": "string", "description": "检索来源查询词(内部追踪用)"},
}


# ============================================================================
# 统一数据模型
# ============================================================================

def make_doc(
    srid: str,
    originalTitle: str,
    chineseTitle: str = "",
    originalAuthors: list[str] = None,
    inventors: list[str] = None,
    originalAbstract: str = "",
    chineseAbstract: str = "",
    publicationYear: Optional[int] = None,
    publicationDate: str = "",
    pdfUrl: Optional[str] = None,
    url: str = "",
    dataSource: str = "科应",
    docType: str = "paper",
    doi: Optional[str] = None,
    institution: Optional[str] = None,
    affiliationCountry: Optional[str] = None,
    featuredTags: Optional[list[str]] = None,
    hasPdf: bool = False,
    isOpenAccess: bool = False,
    hasAiQa: bool = False,
    applicationDate: Optional[str] = None,
    applicationNumber: Optional[str] = None,
    publicationNumber: Optional[str] = None,
    assignees: Optional[str] = None,
    pubMedId: Optional[str] = None,
    issue: Optional[int] = None,
    volume: Optional[int] = None,
    startPage: Optional[int] = None,
    endPage: Optional[int] = None,
    orcids: Optional[list[str]] = None,
    orcidNames: Optional[list[str]] = None,
    authorAffiliations: Optional[list[str]] = None,
    originalAuthorAffiliation: Optional[str] = None,
    issn: Optional[list[str]] = None,
    publisher: Optional[str] = None,
    refIds: Optional[list[str]] = None,
    query: str = "",
) -> dict:
    return {
        "srid": srid,
        "originalTitle": originalTitle,
        "chineseTitle": chineseTitle,
        "originalAuthors": originalAuthors or [],
        "inventors": inventors or [],
        "originalAbstract": originalAbstract,
        "chineseAbstract": chineseAbstract,
        "publicationYear": publicationYear,
        "publicationDate": publicationDate,
        "pdfUrl": pdfUrl,
        "url": url,
        "dataSource": dataSource,
        "docType": docType,
        "doi": doi,
        "institution": institution,
        "affiliationCountry": affiliationCountry,
        "featuredTags": featuredTags or [],
        "hasPdf": hasPdf,
        "isOpenAccess": isOpenAccess,
        "hasAiQa": hasAiQa,
        "applicationDate": applicationDate,
        "applicationNumber": applicationNumber,
        "publicationNumber": publicationNumber,
        "assignees": assignees,
        "pubMedId": pubMedId,
        "issue": issue,
        "volume": volume,
        "startPage": startPage,
        "endPage": endPage,
        "orcids": orcids or [],
        "orcidNames": orcidNames or [],
        "authorAffiliations": authorAffiliations or [],
        "originalAuthorAffiliation": originalAuthorAffiliation,
        "issn": issn or [],
        "publisher": publisher,
        "refIds": refIds or [],
        "_query": query,
    }


# ============================================================================
# Session 管理 - 跨 CLI 调用保持认知上下文
# ============================================================================

class SessionManager:
    """
    Session 是 Agent 的"外置短期记忆"。
    存储位置: ~/.keying/sessions/{session_id}.json
    """

    def __init__(self):
        self.dir = Path.home() / ".keying" / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def create(self, topic: str = "") -> str:
        session_id = str(uuid.uuid4())[:8]
        now = int(time.time())
        data = {
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "topic": topic,
            "query_chain": [],
            "seen_doc_ids": {},
            "seen_queries": {},
            "exploration_depth": 0,
            "max_depth": 5,
            "cumulative_docs": 0,
            "synthesis_hints": [],
        }
        self._save(data)
        return session_id

    def load(self, session_id: str) -> Optional[dict]:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            updated = data.get("updated_at", 0)
            if time.time() - updated > 24 * 3600:
                path.unlink()
                return None
            return data
        except Exception:
            return None

    def _save(self, data: dict) -> None:
        data["updated_at"] = int(time.time())
        path = self._path(data["session_id"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def append_query(self, session_id: str, query: str, rationale: str, results_count: int) -> None:
        data = self.load(session_id)
        if not data:
            return
        step = len(data["query_chain"]) + 1
        data["query_chain"].append({
            "step": step,
            "query": query,
            "rationale": rationale,
            "results_count": results_count,
            "timestamp": int(time.time()),
        })
        data["seen_queries"][query] = True
        data["exploration_depth"] = step
        self._save(data)

    def mark_docs_seen(self, session_id: str, doc_ids: list) -> None:
        data = self.load(session_id)
        if not data:
            return
        for did in doc_ids:
            data["seen_doc_ids"][did] = True
        data["cumulative_docs"] = len(data["seen_doc_ids"])
        self._save(data)

    def get_recommendations(self, session_id: str, current_docs: list, topic: str) -> list:
        data = self.load(session_id)
        if not data:
            return []
        recs = []
        seen_queries = set(data.get("seen_queries", {}).keys())
        query_chain = data.get("query_chain", [])

        # 1. 扩展查询变体 — explore 模式已移除，保留空位
        pass

        # 2. 基于高频分类细分

        # 2. 基于高频分类细分
        cat_counts = {}
        for d in current_docs:
            for c in d.get("categories", []):
                cat_counts[c] = cat_counts.get(c, 0) + 1
        if cat_counts:
            top_cat = max(cat_counts, key=cat_counts.get)
            q = f"{topic} {top_cat}"
            if q not in seen_queries:
                recs.append({
                    "action": "search",
                    "target": q,
                    "rationale": f"基于高频分类 '{top_cat}' 细分搜索",
                })

        # 3. 语义搜索建议
        if len(query_chain) >= 2 and not any(q.get("query", "").startswith("semantic:") for q in query_chain):
            recs.append({
                "action": "semantic",
                "target": topic,
                "rationale": "尝试语义检索获取不同视角的文献",
            })

        # 4. 深度检查
        if len(query_chain) >= data.get("max_depth", 5):
            recs.append({
                "action": "stop",
                "target": "",
                "rationale": "已达到最大探索深度,建议用 LLM 做综合推理",
            })

        return recs[:5]

    def cleanup_old(self) -> int:
        cutoff = time.time() - 24 * 3600
        count = 0
        for f in self.dir.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    count += 1
            except Exception:
                pass
        return count


# ============================================================================
# Token 管理:获取 + 缓存 + 自动刷新
# ============================================================================

class TokenManager:
    """管理 accessToken:获取、本地缓存、过期前自动刷新"""

    def __init__(self):
        os.makedirs(TOKEN_CACHE_DIR, exist_ok=True)

    def _fetch_token(self) -> dict:
        """从科应服务器获取新 token

        认证方式:
        - open.scienceriver.com(生产):appSecret 直接传原始值
        - test-open.scienceriver.com(测试):appSecret 需传 MD5(secret)
        """
        import hashlib
        if "test-open" in BASE_URL:
            secret = hashlib.md5(APP_SECRET.encode()).hexdigest()
        else:
            secret = APP_SECRET

        payload = json.dumps({
            "appId": APP_ID,
            "appSecret": secret,
            "grantType": "client_credential",
        }).encode()

        req = urllib.request.Request(
            f"{BASE_URL}/open/auth/getAccessToken",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            if data.get("code") != 0:
                raise RuntimeError(f"Token error: {data}")
            return data["data"]

    def _load_cached(self) -> Optional[dict]:
        """从本地缓存读取 token"""
        try:
            with open(TOKEN_CACHE_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save_cached(self, token_data: dict) -> None:
        """保存 token 到本地缓存"""
        with open(TOKEN_CACHE_FILE, "w") as f:
            json.dump(token_data, f)

    def get_token(self) -> str:
        """
        获取有效 token。
        优先用缓存,如果过期或不存在则重新获取。
        提前 5 分钟刷新,利用平台 5 分钟双 token 过渡期。
        """
        cached = self._load_cached()

        if cached:
            expires_at = cached.get("_expires_at", 0)
            # 提前 300 秒(5 分钟)刷新
            if time.time() < expires_at - 300:
                return cached["accessToken"]

        # 重新获取
        data = self._fetch_token()
        expires_in = data.get("expiresIn", 86400)
        data["_expires_at"] = time.time() + expires_in
        self._save_cached(data)

        return data["accessToken"]


# ============================================================================
# 科应 API 客户端
# ============================================================================

class ScienceRiverClient:
    def __init__(self):
        self._token_mgr = TokenManager()

    def _request(self, endpoint: str, payload: dict = None, method: str = "POST", timeout: int = None) -> dict:
        """通用请求,自动携带 Bearer token。支持 POST 和 GET。timeout 覆盖默认 TIMEOUT。"""
        token = self._token_mgr.get_token()
        url = f"{BASE_URL}{endpoint}"
        actual_timeout = timeout or TIMEOUT

        if method == "GET":
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {token}"},
                method="GET",
            )
        else:
            body = json.dumps(payload or {}).encode()
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                method="POST",
            )

        with urllib.request.urlopen(req, timeout=actual_timeout) as resp:
            return json.loads(resp.read().decode())

    def _stream_ndjson(self, endpoint: str, payload: dict, timeout: int) -> dict:
        """通用 NDJSON 流式请求。逐行解析 chunks,边收边打印,最后返回合并结果。"""
        token = self._token_mgr.get_token()
        url = f"{BASE_URL}{endpoint}"
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )

        full_content = ""
        full_reasoning = ""
        refs = []
        trace_id = ""

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buffer = b""
            for chunk in iter(lambda: resp.read(4096), b""):
                buffer += chunk
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        part = json.loads(line.decode())
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    part_data = part.get("data", {})
                    if part_data.get("content"):
                        new_content = part_data["content"]
                        if len(new_content) >= len(full_content) and new_content.startswith(full_content):
                            added = new_content[len(full_content):]
                            full_content = new_content
                            if added:
                                print(added, end="", flush=True)
                        else:
                            full_content += new_content
                            print(new_content, end="", flush=True)
                    if part_data.get("reasoningContent"):
                        full_reasoning += part_data["reasoningContent"]
                    if part.get("traceId"):
                        trace_id = part["traceId"]
                    if part_data.get("finish") and part_data.get("refSRids"):
                        refs = part_data["refSRids"]

            if buffer.strip():
                try:
                    part = json.loads(buffer.decode().strip())
                    part_data = part.get("data", {})
                    if part_data.get("content"):
                        new_content = part_data["content"]
                        if len(new_content) >= len(full_content) and new_content.startswith(full_content):
                            added = new_content[len(full_content):]
                            full_content = new_content
                            if added:
                                print(added, end="", flush=True)
                        else:
                            full_content += new_content
                            print(new_content, end="", flush=True)
                    if part_data.get("reasoningContent"):
                        full_reasoning += part_data["reasoningContent"]
                    if part.get("traceId"):
                        trace_id = part["traceId"]
                    if part_data.get("finish") and part_data.get("refSRids"):
                        refs = part_data["refSRids"]
                except Exception:
                    pass

        return {
            "code": 0,
            "message": "OK",
            "traceId": trace_id,
            "data": {
                "content": full_content,
                "finish": True,
                "reasoningContent": full_reasoning,
                "refSRids": refs,
            },
        }

    def search(
        self,
        query: str,
        scope: str = "all",
        field_set: str = "standard",
        page: int = 1,
        per_page: int = 10,
        sort_by: str = "relevance",
        sort_order: str = "desc",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> tuple[list[dict], int]:
        """单次搜索,返回统一数据模型的文献列表"""
        payload = {
            "query": query,
            "scope": scope,
            "fieldSet": field_set,
            "pageNo": page,
            "pageSize": per_page,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
        if date_from:
            payload["dateFrom"] = date_from
        if date_to:
            payload["dateTo"] = date_to

        data = self._request("/open/api/search/general", payload)

        if data.get("code") != 0:
            msg = data.get("message", "Unknown error")
            trace = data.get("traceId", "")
            raise RuntimeError(f"Search error ({msg}, traceId={trace})")

        records = data.get("data", {}).get("records", [])
        total = data.get("data", {}).get("total", 0)

        docs = []
        for item in records:
            doc = self._parse_record(item, query)
            if doc:
                docs.append(doc)

        return docs, total

    def semantic_search(
        self,
        description: str,
        scope: str = "all",
        field_set: str = "standard",
        page: int = 1,
        per_page: int = 10,
        relevance_threshold: float = 0.65,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> tuple[list[dict], int]:
        """语义检索:基于自然语言描述的相似度匹配"""
        payload = {
            "description": description,
            "scope": scope,
            "fieldSet": field_set,
            "pageNo": page,
            "pageSize": per_page,
            "relevanceThreshold": relevance_threshold,
        }
        if date_from:
            payload["dateFrom"] = date_from
        if date_to:
            payload["dateTo"] = date_to

        data = self._request("/open/api/search/semantic", payload)

        if data.get("code") != 0:
            msg = data.get("message", "Unknown error")
            trace = data.get("traceId", "")
            raise RuntimeError(f"Semantic search error ({msg}, traceId={trace})")

        records = data.get("data", {}).get("records", [])
        total = data.get("data", {}).get("total", 0)

        docs = []
        for item in records:
            doc = self._parse_record(item, description)
            if doc:
                docs.append(doc)

        return docs, total

    # ------------------------------------------------------------------
    # 基础信息查询(SRID / DOI 维度)
    # ------------------------------------------------------------------

    def get_basic_info(self, srid: Optional[str] = None, doi: Optional[str] = None) -> dict:
        """获取论文/文献基本信息。srid 和 doi 至少提供一个;同时存在时优先 doi。"""
        if not srid and not doi:
            raise ValueError("get_basic_info: srid or doi required")

        payload = {}
        if srid:
            payload["srid"] = srid
        if doi:
            payload["doi"] = doi

        data = self._request("/open/api/search/basicInfo", payload, method="POST")

        if data.get("code") != 0:
            msg = data.get("message", "Unknown error")
            trace = data.get("traceId", "")
            raise RuntimeError(f"Basic info error ({msg}, traceId={trace})")

        return data.get("data", {})

    def get_pdf_url(self, srid: str) -> str:
        """获取原文 PDF 直链。"""
        if not srid:
            raise ValueError("get_pdf_url: srid required")

        data = self._request(f"/open/api/search/pdfUrl?srid={srid}", method="GET")

        if data.get("code") != 0:
            msg = data.get("message", "Unknown error")
            trace = data.get("traceId", "")
            raise RuntimeError(f"PDF URL error ({msg}, traceId={trace})")

        # data 字段直接是 URL 字符串
        return data.get("data", "")

    def get_patent_legal(self, srid: str) -> dict:
        """获取专利法律信息。"""
        if not srid:
            raise ValueError("get_patent_legal: srid required")

        data = self._request(f"/open/api/search/patent/legalInfo?srid={srid}", method="GET")

        if data.get("code") != 0:
            msg = data.get("message", "Unknown error")
            trace = data.get("traceId", "")
            raise RuntimeError(f"Patent legal info error ({msg}, traceId={trace})")

        return data.get("data", {})

    def get_patent_family(self, srid: str) -> dict:
        """获取专利同族信息。"""
        if not srid:
            raise ValueError("get_patent_family: srid required")

        data = self._request(f"/open/api/search/patent/family?srid={srid}", method="GET")

        if data.get("code") != 0:
            msg = data.get("message", "Unknown error")
            trace = data.get("traceId", "")
            raise RuntimeError(f"Patent family error ({msg}, traceId={trace})")

        return data.get("data", {})

    def explore_evolution(self, doi: str = "", query: str = "", stream: bool = False) -> dict:
        """
        探索演进分析:通过 DOI 或关键词生成领域演进发展分析报告。
        返回非流式完整 JSON;流式模式下在内部逐块拼接后返回完整 content。
        """
        if not doi and not query:
            raise ValueError("explore_evolution: doi or query required")

        payload = {"doi": doi, "query": query, "stream": stream}
        timeout = 30 if stream else 360  # 非流式给 6 分钟,流式给 30 秒

        if not stream:
            data = self._request("/open/api/ai/exploreEvolution", payload, timeout=timeout)
            if data.get("code") != 0:
                msg = data.get("message", "Unknown error")
                trace = data.get("traceId", "")
                raise RuntimeError(f"Evolution analysis error ({msg}, traceId={trace})")
            return data

        return self._stream_ndjson("/open/api/ai/exploreEvolution", payload, timeout)


    def fulltext_qa(
        self,
        srid: str,
        query: str = "",
        stream: bool = False,
    ) -> dict:
        """
        全文问答:基于科应 ID 对文献/专利进行上下文问答与语义解读。
        返回非流式完整 JSON;流式模式下逐块输出后返回合并结果。
        """
        if not srid:
            raise ValueError("fulltext_qa: srid required")

        payload = {"srid": srid, "query": query, "stream": stream}
        timeout = 30 if stream else 300  # 非流式 5 分钟,流式 30 秒

        if not stream:
            data = self._request("/open/api/ai/fullTextQa", payload, timeout=timeout)
            if data.get("code") != 0:
                msg = data.get("message", "Unknown error")
                trace = data.get("traceId", "")
                raise RuntimeError(f"Fulltext QA error ({msg}, traceId={trace})")
            return data

        return self._stream_ndjson("/open/api/ai/fullTextQa", payload, timeout)

    def literature_research(self, query: str, stream: bool = False) -> dict:
        """
        文献调研:基于全球文献的科研级精准问答，给出可溯源的技术要点。
        返回非流式完整 JSON;流式模式下逐块输出后返回合并结果。
        """
        if not query:
            raise ValueError("literature_research: query required")

        payload = {"query": query, "stream": stream}
        timeout = 120 if stream else 360  # 非流式给 6 分钟,流式给 2 分钟

        if not stream:
            data = self._request("/open/api/ai/literatureResearch", payload, timeout=timeout)
            if data.get("code") != 0:
                msg = data.get("message", "Unknown error")
                trace = data.get("traceId", "")
                raise RuntimeError(f"Literature research error ({msg}, traceId={trace})")
            return data

        return self._stream_ndjson("/open/api/ai/literatureResearch", payload, timeout)

    def search_multi_page(
        self,
        query: str,
        scope: str = "all",
        max_results: int = 10,
        field_set: str = "standard",
        sort_by: str = "relevance",
        sort_order: str = "desc",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> tuple[list[dict], int, int]:
        """自动分页搜索,科应后端 pageSize 上限 20。
        返回 (docs, total_matches, pages_fetched) - pages_fetched 表示实际翻了几页。
        """
        MAX_PER_PAGE = 20

        all_docs = []
        page = 1
        remaining = max_results
        total_hint = None
        pages_fetched = 0

        while remaining > 0:
            per_page = min(remaining, MAX_PER_PAGE)
            docs, total = self.search(
                query=query,
                scope=scope,
                field_set=field_set,
                page=page,
                per_page=per_page,
                sort_by=sort_by,
                sort_order=sort_order,
                date_from=date_from,
                date_to=date_to,
            )
            pages_fetched += 1
            if total_hint is None:
                total_hint = total

            if not docs:
                break

            all_docs.extend(docs)
            remaining -= len(docs)
            page += 1

            if len(docs) < per_page:
                break

            time.sleep(0.5)  # 礼貌限速

        return all_docs[:max_results], total_hint or 0, pages_fetched

    def semantic_search_multi_page(
        self,
        description: str,
        scope: str = "all",
        max_results: int = 10,
        field_set: str = "standard",
        relevance_threshold: float = 0.65,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> tuple[list[dict], int, int]:
        """语义检索自动分页,科应后端 pageSize 上限 20。
        返回 (docs, total_matches, pages_fetched)。
        """
        MAX_PER_PAGE = 20

        all_docs = []
        page = 1
        remaining = max_results
        total_hint = None
        pages_fetched = 0

        while remaining > 0:
            per_page = min(remaining, MAX_PER_PAGE)
            docs, total = self.semantic_search(
                description=description,
                scope=scope,
                field_set=field_set,
                page=page,
                per_page=per_page,
                relevance_threshold=relevance_threshold,
                date_from=date_from,
                date_to=date_to,
            )
            pages_fetched += 1
            if total_hint is None:
                total_hint = total

            if not docs:
                break

            all_docs.extend(docs)
            remaining -= len(docs)
            page += 1

            if len(docs) < per_page:
                break

            time.sleep(0.5)

        return all_docs[:max_results], total_hint or 0, pages_fetched

    def _parse_record(self, item: dict, query: str) -> Optional[dict]:
        """将科应返回的原始记录解析为统一数据模型"""
        # 标题:优先原文,fallback 中文(兼容旧字段名)
        title = (
            item.get("originalTitle")
            or item.get("chineseTitle")
            or item.get("title", "")
        )
        if not title:
            return None

        # 摘要:优先原文,fallback 中文(兼容旧字段名)
        abstract = (
            item.get("originalAbstract")
            or item.get("chineseAbstract")
            or item.get("abstract", "")
        )

        # 作者 / 发明人(兼容新旧字段名)
        authors = (
            item.get("originalAuthors", [])
            or item.get("authors", [])
            or item.get("inventors", [])
            or []
        )

        # 年份:从 publicationDate 提取前 4 位,或取 publicationYear
        year = None
        pub_date = item.get("publicationDate", "")
        pub_year = item.get("publicationYear")
        if pub_date and len(pub_date) >= 4:
            try:
                year = int(pub_date[:4])
            except ValueError:
                pass
        if year is None and pub_year:
            try:
                year = int(pub_year)
            except ValueError:
                pass

        # URL: 有 DOI 用 DOI 链接,无则留空
        doi = item.get("doi", "")
        url = f"https://doi.org/{doi}" if doi else ""

        # 专利公开号
        pub_number = item.get("publicationNumber", "")

        # 机构信息
        institution = item.get("institution") or ""
        country = item.get("affiliationCountry") or ""

        # 标签
        categories = item.get("featuredTags", []) or item.get("categories", []) or []

        # 文献类型
        doc_type = item.get("docType", "paper")

        # PDF 和开放获取
        has_pdf = item.get("hasPdf", False)
        is_oa = item.get("isOpenAccess", False)
        has_ai_qa = item.get("hasAiQa", False)

        # 专利专属
        app_date = item.get("applicationDate", "")
        app_number = item.get("applicationNumber", "")
        assignees = item.get("assignees", "")

        return make_doc(
            srid=item.get("srid", ""),
            originalTitle=item.get("originalTitle", ""),
            chineseTitle=item.get("chineseTitle", ""),
            originalAuthors=authors,
            inventors=item.get("inventors", []),
            originalAbstract=item.get("originalAbstract", ""),
            chineseAbstract=item.get("chineseAbstract", ""),
            publicationYear=year,
            publicationDate=pub_date,
            pdfUrl=None,  # 科应暂无直链,hasPdf 标记布尔值
            url=url,
            dataSource=item.get("dataSource", "科应"),
            docType=doc_type,
            doi=item.get("doi") or None,
            institution=institution or None,
            affiliationCountry=country or None,
            featuredTags=categories,
            hasPdf=has_pdf,
            isOpenAccess=is_oa,
            hasAiQa=has_ai_qa,
            applicationDate=app_date or None,
            applicationNumber=app_number or None,
            publicationNumber=pub_number or None,
            assignees=assignees or None,
            pubMedId=item.get("pubMedId") or None,
            issue=item.get("issue"),
            volume=item.get("volume"),
            startPage=item.get("startPage"),
            endPage=item.get("endPage"),
            orcids=item.get("orcids", []),
            orcidNames=item.get("orcidNames", []),
            authorAffiliations=item.get("authorAffiliations", []),
            originalAuthorAffiliation=item.get("originalAuthorAffiliation") or None,
            issn=item.get("issn", []),
            publisher=item.get("publisher") or None,
            refIds=item.get("refIds", []),
            query=query,
        )


# ============================================================================
# 结果处理:去重 + 排序
# ============================================================================

def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def deduplicate(docs: list[dict], threshold: float = 0.85) -> list[dict]:
    unique = []
    seen_norms = []

    for d in docs:
        title = d.get("originalTitle") or d.get("chineseTitle", "")
        norm = normalize_title(title)
        is_dup = False
        for i, existing_norm in enumerate(seen_norms):
            similarity = difflib.SequenceMatcher(None, norm, existing_norm).ratio()
            if similarity >= threshold:
                is_dup = True
                existing = unique[i]
                if _score(d) > _score(existing):
                    unique[i] = d
                break
        if not is_dup:
            seen_norms.append(norm)
            unique.append(d)

    return unique


def _score(doc: dict) -> int:
    score = 0
    abstract = doc.get("originalAbstract") or doc.get("chineseAbstract", "")
    authors = doc.get("originalAuthors", [])
    if abstract and len(abstract) > 50:
        score += 3
    if authors and len(authors) > 0:
        score += 2
    if doc.get("publicationYear"):
        score += 2
    if doc.get("hasPdf"):
        score += 1
    if doc.get("isOpenAccess"):
        score += 1
    return score


def rank_docs(docs: list[dict]) -> list[dict]:
    def _title(doc):
        return doc.get("originalTitle") or doc.get("chineseTitle", "")
    return sorted(docs, key=lambda d: (-_score(d), -(d.get("publicationYear") or 0), _title(d)))


# ============================================================================
# 输出辅助:字段过滤 + 格式切换
# ============================================================================

def _resolve_format(args) -> str:
    fmt = getattr(args, "format", None)
    if fmt is not None:
        return fmt.strip().lower()
    if getattr(args, "json", False):
        return "json"
    return "agent"


def _parse_fields(field_str: Optional[str]) -> Optional[list[str]]:
    if not field_str:
        return None
    return [f.strip() for f in field_str.split(",") if f.strip()]


# 字段分类:按 docType 自动排除不相关字段
PATENT_ONLY_FIELDS = {
    "inventors", "assignees", "applicationDate", "applicationNumber",
    "publicationNumber", "infrist", "infirst", "agent", "examiner",
    "apadd", "inadd", "patentOffice", "patType", "pubType", "loc",
}
PAPER_ONLY_FIELDS = {
    "originalAuthors", "orcids", "orcidNames", "authorAffiliations",
    "originalAuthorAffiliation", "pubMedId", "issue", "volume",
    "startPage", "endPage", "issn", "publisher", "refIds",
}

def _filter_doc(doc: dict, fields: Optional[list[str]]) -> dict:
    """过滤字段并剔除空值(None、空串、空列表)。

    用户要求:返回的文献中空值字段就不要显示了。
    """
    def _is_empty(v) -> bool:
        if v is None:
            return True
        if isinstance(v, str) and v == "":
            return True
        if isinstance(v, list) and len(v) == 0:
            return True
        return False

    def _should_exclude(k: str, doc_type: str) -> bool:
        """按 docType 排除不相关字段。"""
        dt = doc_type.lower()
        if dt in ("paper", "papers") and k in PATENT_ONLY_FIELDS:
            return True
        if dt in ("patent", "patents") and k in PAPER_ONLY_FIELDS:
            return True
        return False

    doc_type = doc.get("docType", "paper")

    if fields is None:
        # 默认:保留所有非内部字段,值非空,且符合 docType
        return {k: v for k, v in doc.items()
                if not k.startswith("_") and not _is_empty(v) and not _should_exclude(k, doc_type)}

    result = {}
    for f in fields:
        if f in doc and not _is_empty(doc[f]) and not _should_exclude(f, doc_type):
            result[f] = doc[f]
    return result


def _output_docs(docs: list[dict], fields: Optional[list[str]], fmt: str, stream: bool = False,
                  total: Optional[int] = None, page: Optional[int] = None, per_page: Optional[int] = None,
                  pagination: dict = None) -> None:
    """统一输出管道:过滤字段 → 格式化

    当 total/page/per_page/pagination 提供时,各格式均输出分页元数据:
    - json:  {"data": {"total": ..., "pageNo": ..., "pageSize": ..., "list": [...]}}
    - jsonl: 首行为 {"_type": "pagination", ...},随后每行一个文档
    - human: 列表前输出 "Results: X total | page Y | Z/page"
    - stream: 先输出 type=pagination 的 chunk,再输出 doc chunks
    """
    filtered = [_filter_doc(d, fields) for d in docs]

    if stream:
        if pagination:
            _output_stream("pagination", pagination)
        for d in filtered:
            _output_stream("doc", {"doc": d})
        return

    if fmt == "jsonl":
        if total is not None:
            meta = {
                "_type": "pagination",
                "total": total,
                "pageNo": page or 1,
                "pageSize": per_page or len(docs),
                "returned": len(docs),
            }
            if pagination:
                meta.update(pagination)
            print(json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
        for d in filtered:
            print(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    elif fmt == "json":
        meta = {}
        if pagination:
            meta_keys = ("mode", "format", "scope", "field_set", "session_id", "topic",
                         "requested", "returned", "total_matches", "pages_fetched",
                         "has_more", "sort_by", "sort_order", "relevance_threshold")
            meta = {k: pagination[k] for k in meta_keys if k in pagination}
        if total is not None:
            output = {
                "meta": meta,
                "data": {
                    "total": total,
                    "pageNo": page or 1,
                    "pageSize": per_page or len(docs),
                    "list": filtered,
                }
            }
        else:
            output = {
                "meta": meta,
                "data": filtered,
            }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    elif fmt == "agent":
        # agent 格式由上层 output_agent_pack 组装,此处不应直接调用
        # 若被误调用,输出空认知包占位,避免落入 human 分支污染管道
        print(json.dumps({"type": "error", "message": "agent format should be handled by output_agent_pack"}, ensure_ascii=False))
    else:
        if total is not None:
            scope_hint = f" [{pagination.get('scope')}]" if pagination and pagination.get('scope') and pagination.get('scope') != "all" else ""
            page_no = page or 1
            page_size = per_page or len(docs)
            sort_hint = ""
            if pagination:
                if "sort_by" in pagination:
                    sort_hint = f" sorted by {pagination['sort_by']} ({pagination.get('sort_order', 'desc')})"
                elif "relevance_threshold" in pagination:
                    sort_hint = f" threshold={pagination['relevance_threshold']}"
            print(f"\n🔍 Results: {len(docs)} returned / {total} total (page {page_no}, {page_size} per page){scope_hint}{sort_hint}")
            if pagination:
                if pagination.get("pages_fetched", 1) > 1:
                    print(f"   (fetched {pagination['pages_fetched']} pages, backend limit 20/page)")
                if pagination.get("has_more"):
                    print(f"   ⚠️  More results available: {total - len(docs)} not fetched.")
                elif not pagination.get("has_more") and total > len(docs):
                    print(f"   ✅ All requested results fetched.")
            print()
        for i, d in enumerate(filtered, 1):
            print(_format_doc_human(d, i))


def _format_doc_human(doc: dict, index: int = 0) -> str:
    lines = []
    prefix = f"[{index}] " if index else ""
    doc_type = doc.get("docType", "paper")
    doc_type_emoji = {"paper": "📄", "patent": "📋", "standard": "📑"}.get(doc_type, "📄")

    title = doc.get("originalTitle") or doc.get("chineseTitle", "")
    if title:
        lines.append(f"{prefix}{doc_type_emoji} {title}")

    # 类型标签行
    info_parts = []
    info_parts.append(f"Type: {doc_type}")
    authors = doc.get("originalAuthors", [])
    if authors:
        authors_str = ", ".join(authors[:5])
        if len(authors) > 5:
            authors_str += "..."
        label = "Inventors" if doc_type == "patent" else "Authors"
        info_parts.append(f"{label}: {authors_str}")
    pub_year = doc.get("publicationYear")
    if pub_year is not None:
        info_parts.append(f"Year: {pub_year}")
    data_source = doc.get("dataSource")
    if data_source:
        info_parts.append(f"Source: {data_source}")
    if info_parts:
        lines.append(f"    {' | '.join(info_parts)}")

    pub_date = doc.get("publicationDate")
    if pub_date:
        lines.append(f"    Published: {pub_date}")

    # 专利专属信息
    if doc_type == "patent":
        patent_parts = []
        pub_num = doc.get("publicationNumber")
        if pub_num:
            patent_parts.append(f"Pub No: {pub_num}")
        app_num = doc.get("applicationNumber")
        if app_num:
            patent_parts.append(f"App No: {app_num}")
        assignees = doc.get("assignees")
        if assignees:
            patent_parts.append(f"Assignee: {assignees}")
        if patent_parts:
            lines.append(f"    {' | '.join(patent_parts)}")

    institution = doc.get("institution")
    if institution:
        country = f" ({doc.get('affiliationCountry')})" if doc.get("affiliationCountry") else ""
        lines.append(f"    Institution: {institution}{country}")

    tags = doc.get("featuredTags", [])
    if tags:
        lines.append(f"    Tags: {', '.join(tags[:5])}")

    doi = doc.get("doi")
    if doi:
        lines.append(f"    DOI: {doi}")

    url = doc.get("url")
    if url:
        lines.append(f"    URL: {url}")

    abstract = doc.get("originalAbstract") or doc.get("chineseAbstract", "")
    if abstract:
        abstr = abstract[:250]
        if len(abstract) > 250:
            abstr += "..."
        lines.append(f"    Abstract: {abstr}")

    # 标记
    flags = []
    if doc.get("hasPdf"):
        flags.append("📥 PDF")
    if doc.get("isOpenAccess"):
        flags.append("🔓 OA")
    if doc.get("hasAiQa"):
        flags.append("🤖 AI-QA")
    if flags:
        lines.append(f"    [{' | '.join(flags)}]")

    if "_query" in doc:
        lines.append(f"    [Matched by: '{doc['_query']}']")

    lines.append("")
    return "\n".join(lines)


# ============================================================================
# 输出引擎:人类可读 / JSON / JSONL / Agent 认知包 / 流式
# ============================================================================

def _output_stream(chunk_type: str, payload: dict) -> None:
    """NDJSON 流式输出 - Agent 可以边读边处理"""
    chunk = {"type": chunk_type, **payload}
    print(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.flush()


def output_agent_pack(
    topic: str,
    docs: list[dict],
    session_id: str,
    query_chain: list,
    recommendations: list,
    fmt: str = "json",
    stream: bool = False,
    pagination: dict = None,
) -> None:
    """
    组装并输出认知包。
    """
    years = [d.get("publicationYear") for d in docs if d.get("publicationYear")]
    featured_tags = {}
    for d in docs:
        for c in d.get("featuredTags", []):
            featured_tags[c] = featured_tags.get(c, 0) + 1

    # docType 分布
    doc_types = {}
    for d in docs:
        dt = d.get("docType", "unknown")
        doc_types[dt] = doc_types.get(dt, 0) + 1

    pack = {
        "topic": topic,
        "session_id": session_id,
        "mode": (pagination or {}).get("mode", "unknown"),
        "format": fmt,
        "scope": (pagination or {}).get("scope", "all"),
        "field_set": (pagination or {}).get("field_set", "standard"),
        "query_chain": query_chain,
        "doc_count": len(docs),
        "pagination": pagination or {},
        "docs": docs,
        "synthesis": {
            "year_range": {"min": min(years) if years else None, "max": max(years) if years else None},
            "top_categories": sorted(featured_tags.items(), key=lambda x: -x[1])[:5],
            "doc_type_distribution": doc_types,
            "summary": f"共检索到 {len(docs)} 篇文献,包含 {', '.join(f'{k}:{v}' for k, v in doc_types.items())}",
        },
        "recommendations": recommendations,
        "confidence": round(min(1.0, 0.5 + len(docs) * 0.02), 2),
    }

    if stream:
        _output_stream("metadata", {
            "topic": pack["topic"],
            "session_id": pack["session_id"],
            "mode": pack["mode"],
            "format": pack["format"],
            "scope": pack["scope"],
            "field_set": pack["field_set"],
            "doc_count": pack["doc_count"],
            "confidence": pack["confidence"],
            "pagination": pack["pagination"],
        })
        for qc in pack["query_chain"]:
            _output_stream("query_step", qc)
        for d in pack["docs"]:
            _output_stream("doc", {"doc": d})
        _output_stream("synthesis", pack["synthesis"])
        for r in pack["recommendations"]:
            _output_stream("recommendation", r)
        _output_stream("done", {"message": "cognitive pack complete"})
    else:
        print(json.dumps(pack, indent=2, ensure_ascii=False))


# ============================================================================
# CLI
# ============================================================================

def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keying",
        description="Search academic papers & patents via ScienceRiver (科应开放平台). AI-native output with --fields, --format, and schema.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Query Syntax (Expression Search):
  Multi-word Phrase Rules:
    • Double quotes "..." for exact-phrase match:  TIAB="fluorescent quantum dots"
    • Parentheses (...) for implicit AND:           TIAB=(fluorescent quantum dots)
    Both are correct; quotes enforce adjacency, parentheses only require co-occurrence.
    Single-word values and Chinese text need no wrapping.

  Keywords:       keying search "quantum dot"
  Boolean:        keying search "battery AND solar"
                  keying search "battery OR solar NOT lithium"
  Parentheses:    keying search "TIAB=(quantum dot) AND CREATOR=(Zhang)"
                  (multi-word values MUST be wrapped in parentheses or quotes)
  Field Limits:   keying search "TI=transformer AND AB=(deep learning)"
  Patent Fields:  keying search "PN=CN113711419A"
                  keying search "ORG=宁德时代"
                  keying search "CREATOR=张三"
                  keying search "IPC=H01M10/44"
                  keying search "APPDATE=[20190101 TO 20231231]"
  Date Range:     keying search "transformer" --date-from 20200101 --date-to 20231231

Examples:
  keying search "transformer" -n 40
  keying search "LLM" --scope papers --format jsonl --fields srid,originalTitle,publicationYear
  keying search "TIAB=(lithium battery)" --scope patents -n 40
  keying info <srid>
  keying info <srid> --pdf
  keying info --doi 10.xxxx/xxxxx
  keying schema
  keying semantic "deep learning for drug design" --scope papers
  keying sources
  keying evolution --doi 10.xxxx/xxxxx
  keying evolution "quantum dot energy transfer"
  keying research "cGAS-STING通路激活与自身免疫性疾病的关系" --stream
  keying fulltext-qa <srid> "这篇论文的主要结论是什么？"
  keying sessions
        """,
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # search
    search_parser = subparsers.add_parser("search", help="Expression search: field-qualified boolean queries (TI=, AB=, AND/OR/NOT)")
    search_parser.add_argument("query", help="Search expression. Field qualifiers: TI=/AB=/TIAB=/CREATOR=. Boolean: AND/OR/NOT. Multi-word values MUST use parentheses or quotes: TIAB=(quantum dot) or TIAB=\"quantum dot\"")
    search_parser.add_argument("-n", "--max-results", type=int, default=10, help="Max results (default: 10)")
    search_parser.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    search_parser.add_argument("--scope", choices=["all", "papers", "patents"], default="all",
                              help="Search scope: all / papers / patents (default: all)")
    search_parser.add_argument("--field-set", choices=["brief", "standard", "comprehensive"], default="standard",
                              help="Field set: brief / standard / comprehensive (default: standard)")
    search_parser.add_argument("--sort-by", choices=["relevance", "date"], default="relevance",
                              help="Sort by relevance or date (default: relevance)")
    search_parser.add_argument("--sort-order", choices=["desc", "asc"], default="desc",
                              help="Sort order (default: desc)")
    search_parser.add_argument("--date-from", default="", help="Start date, yyyyMMdd, e.g. 20250101")
    search_parser.add_argument("--date-to", default="", help="End date, yyyyMMdd, e.g. 20251231")
    search_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    search_parser.add_argument("--format", choices=["json", "jsonl", "human", "agent"], default="agent",
                              help="Output format (default: agent)")
    search_parser.add_argument("--fields", default="",
                              help="Comma-separated field list, e.g. 'srid,originalTitle,publicationYear' (default: all)")
    search_parser.add_argument("--session", default="", help="Session ID to continue from")
    search_parser.add_argument("--new-session", action="store_true", help="Force create new session")
    search_parser.add_argument("--stream", action="store_true", help="Stream NDJSON output")

    # semantic
    semantic_parser = subparsers.add_parser("semantic", help="Semantic search: natural language similarity matching with relevance threshold")
    semantic_parser.add_argument("description", help="Natural language description of what you're looking for")
    semantic_parser.add_argument("-n", "--max-results", type=int, default=10, help="Max results (default: 10)")
    semantic_parser.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    semantic_parser.add_argument("--scope", choices=["all", "papers", "patents"], default="all",
                              help="Search scope (default: all)")
    semantic_parser.add_argument("--field-set", choices=["brief", "standard", "comprehensive"], default="standard",
                              help="Field set (default: standard)")
    semantic_parser.add_argument("--relevance-threshold", type=float, default=0.65,
                              help="Relevance threshold [0,1], higher=more precise (default: 0.65)")
    semantic_parser.add_argument("--date-from", default="", help="Start date, yyyyMMdd")
    semantic_parser.add_argument("--date-to", default="", help="End date, yyyyMMdd")
    semantic_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    semantic_parser.add_argument("--format", choices=["json", "jsonl", "human", "agent"], default="agent",
                              help="Output format (default: agent)")
    semantic_parser.add_argument("--fields", default="",
                              help="Comma-separated field list, e.g. 'srid,originalTitle,publicationYear' (default: all)")
    semantic_parser.add_argument("--session", default="", help="Session ID to continue from")
    semantic_parser.add_argument("--new-session", action="store_true", help="Force create new session")
    semantic_parser.add_argument("--stream", action="store_true", help="Stream NDJSON output")

    # info
    info_parser = subparsers.add_parser("info", help="Document lookup: metadata, PDF URL, patent legal/family by SRID or DOI")
    info_parser.add_argument("id", nargs="?", default="", help="ScienceRiver SRID")
    info_parser.add_argument("--doi", default="", help="Query by DOI instead of SRID")
    info_parser.add_argument("--pdf", action="store_true", help="Get PDF download URL (requires SRID; use --doi to resolve SRID first)")
    info_parser.add_argument("--legal", action="store_true", help="Get patent legal info (patent only)")
    info_parser.add_argument("--family", action="store_true", help="Get patent family info (patent only)")
    info_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    info_parser.add_argument("--format", choices=["json", "jsonl", "human", "agent"], default="agent",
                            help="Output format (default: agent)")
    info_parser.add_argument("--fields", default="", help="Comma-separated field list (default: all)")

    # schema
    schema_parser = subparsers.add_parser("schema", help="Print the unified document schema (paper + patent fields)")
    schema_parser.add_argument("--format", choices=["json", "jsonl"], default="json", help="Output format (default: json)")

    # evolution
    evolution_parser = subparsers.add_parser("evolution", help="Research evolution analysis: generate a markdown survey tracing the development history of a field from a target DOI or query")
    evolution_parser.add_argument("query", nargs="?", default="", help="Research topic or question (used when --doi not provided)")
    evolution_parser.add_argument("--doi", default="", help="Target paper DOI. Takes precedence over query when both provided")
    evolution_parser.add_argument("--stream", action="store_true", help="Stream output in real-time (default: wait for complete report)")
    evolution_parser.add_argument("--format", choices=["human", "json", "jsonl", "agent"], default="human", help="Output format (default: human)")
    evolution_parser.add_argument("--output", "-o", default="", help="Save report to file path")

    # fulltext-qa
    qa_parser = subparsers.add_parser("fulltext-qa", help="Full-text Q&A: ask questions about a paper/patent via its ScienceRiver SRID")
    qa_parser.add_argument("srid", help="ScienceRiver SRID of the target paper or patent")
    qa_parser.add_argument("query", nargs="?", default="", help="Question to ask about the document")
    qa_parser.add_argument("--stream", action="store_true", help="Stream output in real-time")
    qa_parser.add_argument("--format", choices=["human", "json", "jsonl", "agent"], default="human", help="Output format (default: human)")
    qa_parser.add_argument("--output", "-o", default="", help="Save answer to file path")

    # research
    research_parser = subparsers.add_parser("research", help="Literature research: AI-powered academic survey based on global literature, with traceable technical insights")
    research_parser.add_argument("query", help="Research topic or question to investigate")
    research_parser.add_argument("--stream", action="store_true", help="Stream output in real-time (default: wait for complete report)")
    research_parser.add_argument("--format", choices=["human", "json", "jsonl", "agent"], default="human", help="Output format (default: human)")
    research_parser.add_argument("--output", "-o", default="", help="Save report to file path")

    # sources
    sources_parser = subparsers.add_parser("sources", help="List configured data sources and coverage info")

    # sessions
    sessions_parser = subparsers.add_parser("sessions", help="Session management: list active sessions, cleanup expired")
    sessions_parser.add_argument("--cleanup", action="store_true", help="Remove expired sessions")
    sessions_parser.add_argument("--format", choices=["json", "human"], default="human")

    return parser


def handle_search(args) -> None:
    client = ScienceRiverClient()
    session_mgr = SessionManager()
    date_from = args.date_from or None
    date_to = args.date_to or None

    # session 管理
    if args.new_session or not args.session:
        args.session = session_mgr.create(topic=args.query)
        if not args.stream:
            print(f"🆕 New session: {args.session}", file=sys.stderr)

    try:
        if args.max_results <= 20:
            docs, total = client.search(
                query=args.query,
                scope=args.scope,
                field_set=args.field_set,
                page=args.page,
                per_page=args.max_results,
                sort_by=args.sort_by,
                sort_order=args.sort_order,
                date_from=date_from,
                date_to=date_to,
            )
            pages_fetched = 1
        else:
            if args.max_results <= 100 and not args.stream:
                print(f"⚠️  Fetching {args.max_results} results across multiple pages (backend limit: 20/page)...", file=sys.stderr)
            docs, total, pages_fetched = client.search_multi_page(
                query=args.query,
                scope=args.scope,
                max_results=args.max_results,
                field_set=args.field_set,
                sort_by=args.sort_by,
                sort_order=args.sort_order,
                date_from=date_from,
                date_to=date_to,
            )
    except RuntimeError as e:
        if not args.stream:
            print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not docs:
        if not args.stream:
            print("No results found.", file=sys.stderr)
        return

    # 更新 session
    session_mgr.append_query(args.session, args.query, "用户直接搜索", len(docs))
    session_mgr.mark_docs_seen(args.session, [d["srid"] for d in docs])

    fmt = _resolve_format(args)
    fields = _parse_fields(args.fields)

    # 组装分页元数据（agent 和人类都能用）
    pagination = {
        "requested": args.max_results,
        "returned": len(docs),
        "total_matches": total,
        "pages_fetched": pages_fetched,
        "has_more": len(docs) < total,
        "scope": args.scope,
        "sort_by": args.sort_by,
        "sort_order": args.sort_order,
        "mode": "search",
        "format": fmt,
        "field_set": args.field_set,
        "session_id": args.session,
        "topic": args.query,
    }

    if fmt == "agent":
        session_data = session_mgr.load(args.session)
        query_chain = session_data.get("query_chain", []) if session_data else []
        recommendations = session_mgr.get_recommendations(args.session, docs, args.query)
        output_agent_pack(
            topic=args.query,
            docs=[_filter_doc(d, fields) for d in docs],
            session_id=args.session,
            query_chain=query_chain,
            recommendations=recommendations,
            fmt=fmt,
            stream=args.stream,
            pagination=pagination,
        )
    else:
        _output_docs(docs, fields, fmt, stream=args.stream, total=total, page=args.page, per_page=args.max_results, pagination=pagination)


def handle_semantic(args) -> None:
    client = ScienceRiverClient()
    session_mgr = SessionManager()
    date_from = args.date_from or None
    date_to = args.date_to or None

    # session 管理
    if args.new_session or not args.session:
        args.session = session_mgr.create(topic=args.description)
        if not args.stream:
            print(f"🆕 New session: {args.session}", file=sys.stderr)

    try:
        if args.max_results <= 20:
            docs, total = client.semantic_search(
                description=args.description,
                scope=args.scope,
                field_set=args.field_set,
                page=args.page,
                per_page=args.max_results,
                relevance_threshold=args.relevance_threshold,
                date_from=date_from,
                date_to=date_to,
            )
            pages_fetched = 1
        else:
            if args.max_results <= 100 and not args.stream:
                print(f"⚠️  Fetching {args.max_results} semantic results across multiple pages (backend limit: 20/page)...", file=sys.stderr)
            docs, total, pages_fetched = client.semantic_search_multi_page(
                description=args.description,
                scope=args.scope,
                max_results=args.max_results,
                field_set=args.field_set,
                relevance_threshold=args.relevance_threshold,
                date_from=date_from,
                date_to=date_to,
            )
    except RuntimeError as e:
        if not args.stream:
            print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not docs:
        if not args.stream:
            print("No results found.", file=sys.stderr)
        return

    # 更新 session
    session_mgr.append_query(args.session, f"semantic:{args.description}", "语义检索", len(docs))
    session_mgr.mark_docs_seen(args.session, [d["srid"] for d in docs])

    fmt = _resolve_format(args)
    fields = _parse_fields(args.fields)

    # 组装分页元数据
    pagination = {
        "requested": args.max_results,
        "returned": len(docs),
        "total_matches": total,
        "pages_fetched": pages_fetched,
        "has_more": len(docs) < total,
        "scope": args.scope,
        "relevance_threshold": args.relevance_threshold,
        "mode": "semantic",
        "format": fmt,
        "field_set": args.field_set,
        "session_id": args.session,
        "topic": args.description,
    }

    if fmt == "agent":
        session_data = session_mgr.load(args.session)
        query_chain = session_data.get("query_chain", []) if session_data else []
        recommendations = session_mgr.get_recommendations(args.session, docs, args.description)
        output_agent_pack(
            topic=args.description,
            docs=[_filter_doc(d, fields) for d in docs],
            session_id=args.session,
            query_chain=query_chain,
            recommendations=recommendations,
            fmt=fmt,
            stream=args.stream,
            pagination=pagination,
        )
    else:
        _output_docs(docs, fields, fmt, stream=args.stream, total=total, page=args.page, per_page=args.max_results, pagination=pagination)


def handle_info(args) -> None:
    """通过 SRID 或 DOI 查询文献详情、PDF 地址、专利法律/同族信息。"""
    client = ScienceRiverClient()
    srid = args.id.strip() if args.id else ""
    doi = args.doi.strip() if args.doi else ""

    if not srid and not doi:
        print("❌ Error: SRID or DOI required. Use: keying info <srid>  or  keying info --doi <doi>", file=sys.stderr)
        sys.exit(1)

    # 判断查询模式
    query_mode = "basic"  # 默认查 comprehensive 基本信息
    if args.pdf:
        query_mode = "pdf"
    elif args.legal:
        query_mode = "legal"
    elif args.family:
        query_mode = "family"

    try:
        if query_mode == "basic":
            data = client.get_basic_info(srid=srid or None, doi=doi or None)
            fmt = _resolve_format(args)

            if fmt in ("json", "jsonl"):
                meta = {
                    "_type": "meta",
                    "mode": "info",
                    "format": fmt,
                }
                out = json.dumps({"meta": meta, "data": data}, ensure_ascii=False, separators=(",", ":")) if fmt == "jsonl" else json.dumps({"meta": meta, "data": data}, indent=2, ensure_ascii=False)
                print(out)
                return

            # human readable
            _print_basic_info_human(data)

        elif query_mode == "pdf":
            if not srid:
                print("❌ Error: --pdf requires SRID (not DOI).", file=sys.stderr)
                sys.exit(1)
            url = client.get_pdf_url(srid)
            if url:
                print(f"📄 PDF URL: {url}")
            else:
                print("⚠️  No PDF available for this document.", file=sys.stderr)

        elif query_mode == "legal":
            if not srid:
                print("❌ Error: --legal requires SRID (not DOI).", file=sys.stderr)
                sys.exit(1)
            data = client.get_patent_legal(srid)
            print(json.dumps(data, indent=2, ensure_ascii=False))

        elif query_mode == "family":
            if not srid:
                print("❌ Error: --family requires SRID (not DOI).", file=sys.stderr)
                sys.exit(1)
            data = client.get_patent_family(srid)
            print(json.dumps(data, indent=2, ensure_ascii=False))

    except RuntimeError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


def _print_basic_info_human(data: dict) -> None:
    """以人类可读格式打印 comprehensive 基本信息。"""
    if not data:
        print("No data returned.", file=sys.stderr)
        return

    # 标题
    title = data.get("originalTitle") or data.get("chineseTitle") or data.get("title", "")
    print(f"\n📄 {title}\n")

    # 类型标识
    doc_type = data.get("docType", "unknown")
    type_label = {"paper": "论文", "patent": "专利", "standard": "标准"}.get(doc_type, doc_type)
    print(f"  类型: {type_label}")

    # SRID / DOI
    srid = data.get("srid", "")
    doi = data.get("doi", "")
    if srid:
        print(f"  SRID: {srid}")
    if doi:
        print(f"  DOI:  {doi}")

    # 作者 / 发明人
    authors = data.get("originalAuthors", [])
    if authors:
        label = "发明人" if doc_type == "patent" else "作者"
        print(f"  {label}: {', '.join(authors[:8])}{'...' if len(authors) > 8 else ''}")

    # 机构
    inst = data.get("institution", "")
    country = data.get("affiliationCountry", "")
    if inst:
        country_hint = f" ({country})" if country else ""
        print(f"  机构: {inst}{country_hint}")

    # 来源
    source = data.get("dataSource", "")
    if source:
        print(f"  来源: {source}")

    # 日期
    pub_date = data.get("publicationDate", "")
    pub_year = data.get("publicationYear", "")
    app_date = data.get("applicationDate", "")
    if pub_date:
        print(f"  发表/公开日期: {pub_date}")
    if pub_year:
        print(f"  年份: {pub_year}")
    if app_date:
        print(f"  申请日期: {app_date}")

    # 专利专属
    pub_no = data.get("publicationNumber", "")
    app_no = data.get("applicationNumber", "")
    assignees = data.get("assignees", "")
    if pub_no:
        print(f"  公开号: {pub_no}")
    if app_no:
        print(f"  申请号: {app_no}")
    if assignees:
        print(f"  权利人: {assignees}")

    # 摘要
    abstract = data.get("originalAbstract") or data.get("chineseAbstract") or ""
    if abstract:
        print(f"\n  摘要:\n    {abstract[:400]}{'...' if len(abstract) > 400 else ''}")

    # 标记
    flags = []
    if data.get("hasPdf"):
        flags.append("📥 有PDF")
    if data.get("isOpenAccess"):
        flags.append("🔓 开放获取")
    if data.get("hasAiQa"):
        flags.append("🤖 支持AI问答")
    if flags:
        print(f"\n  [{' | '.join(flags)}]")

    # 标签
    tags = data.get("featuredTags", [])
    if tags:
        print(f"\n  标签: {', '.join(tags)}")

    print()


def handle_schema(args) -> None:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Document",
        "description": "Academic document object returned by keying CLI (ScienceRiver / 科应开放平台)",
        "type": "object",
        "properties": {},
        "required": ["srid", "originalTitle", "originalAuthors", "originalAbstract", "publicationYear", "publicationDate", "url", "dataSource", "docType"],
    }

    type_map = {
        "string": {"type": "string"},
        "integer|null": {"type": ["integer", "null"]},
        "string|null": {"type": ["string", "null"]},
        "array[string]": {"type": "array", "items": {"type": "string"}},
        "boolean": {"type": "boolean"},
    }

    for name, meta in DOC_FIELDS.items():
        schema["properties"][name] = {
            **type_map.get(meta["type"], {"type": "string"}),
            "description": meta["description"],
        }

    if args.format == "jsonl":
        print(json.dumps(schema, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(schema, indent=2, ensure_ascii=False))


def handle_evolution(args) -> None:
    """探索演进分析:DOI 或关键词 → Markdown 综述报告。"""
    client = ScienceRiverClient()
    doi = args.doi.strip() if args.doi else ""
    query = args.query.strip() if args.query else ""

    if not doi and not query:
        print("❌ Error: --doi or query required.", file=sys.stderr)
        print("   keying evolution --doi 10.xxxx/xxxxx", file=sys.stderr)
        print("   keying evolution \"quantum dot energy transfer\"", file=sys.stderr)
        sys.exit(1)

    try:
        if not args.stream:
            print("⏳ Generating evolution report, this may take a few minutes...", file=sys.stderr)
        data = client.explore_evolution(doi=doi, query=query, stream=args.stream)
    except RuntimeError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

    report_data = data.get("data", {})
    content = report_data.get("content", "")
    refs = report_data.get("refSRids", [])
    trace_id = data.get("traceId", "")

    if not content:
        print("⚠️  No report content returned.", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif args.format == "jsonl":
        for line in json.dumps(data, ensure_ascii=False).splitlines():
            print(line)
    elif args.format == "agent":
        pack = output_agent_pack("evolution", [{"topic": query, "data": data}], fmt="agent")
        print(json.dumps(pack, indent=2, ensure_ascii=False))
    else:
        # 非流式模式下 content 还没打印,这里统一输出
        if not args.stream:
            print(content)
        if trace_id:
            print(f"\n\n[traceId: {trace_id}]", file=sys.stderr)
        if refs:
            print(f"[refSRids: {', '.join(refs)}]", file=sys.stderr)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                if args.format in ("json", "jsonl", "agent"):
                    f.write(json.dumps(data, indent=2, ensure_ascii=False))
                else:
                    f.write(content)
            print(f"\n💾 Saved to {args.output}", file=sys.stderr)
        except OSError as e:
            print(f"⚠️  Failed to save file: {e}", file=sys.stderr)


def handle_fulltext_qa(args) -> None:
    """全文问答:通过 SRID 对文献进行上下文问答。"""
    client = ScienceRiverClient()
    srid = args.srid.strip() if args.srid else ""
    query = args.query.strip() if args.query else ""

    if not srid:
        print("❌ Error: srid required.", file=sys.stderr)
        print("   keying fulltext-qa SR1010037010001063619371217142211182421370200000437000437000007 '这篇论文的主要结论是什么?'", file=sys.stderr)
        sys.exit(1)

    if not query:
        print("❌ Error: query required.", file=sys.stderr)
        sys.exit(1)

    try:
        if not args.stream:
            print("⏳ Analyzing document, this may take a minute...", file=sys.stderr)
        data = client.fulltext_qa(srid=srid, query=query, stream=args.stream)
    except RuntimeError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

    qa_data = data.get("data", {})
    content = qa_data.get("content", "")
    refs = qa_data.get("refSRids", [])
    trace_id = data.get("traceId", "")

    if not content:
        print("⚠️  No answer content returned.", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif args.format == "jsonl":
        for line in json.dumps(data, ensure_ascii=False).splitlines():
            print(line)
    elif args.format == "agent":
        pack = output_agent_pack("fulltext_qa", [{"srid": srid, "query": query, "data": data}], fmt="agent")
        print(json.dumps(pack, indent=2, ensure_ascii=False))
    else:
        if not args.stream:
            print(content)
        if trace_id:
            print(f"\n\n[traceId: {trace_id}]", file=sys.stderr)
        if refs:
            print(f"[refSRids: {', '.join(refs)}]", file=sys.stderr)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                if args.format in ("json", "jsonl", "agent"):
                    f.write(json.dumps(data, indent=2, ensure_ascii=False))
                else:
                    f.write(content)
            print(f"\n💾 Saved to {args.output}", file=sys.stderr)
        except OSError as e:
            print(f"⚠️  Failed to save file: {e}", file=sys.stderr)


def handle_research(args) -> None:
    client = ScienceRiverClient()
    stream = args.stream
    result = client.literature_research(query=args.query, stream=stream)

    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.format == "jsonl":
        for line in json.dumps(result, ensure_ascii=False).splitlines():
            print(line)
    elif args.format == "agent":
        pack = output_agent_pack("literature_research", [result], fmt="agent")
        print(json.dumps(pack, indent=2, ensure_ascii=False))
    else:
        data = result.get("data", {})
        content = data.get("content", "")
        refs = data.get("refSRids", [])
        trace = result.get("traceId", "")
        if stream:
            # 流式模式下 content 已在 _stream_ndjson 中实时打印,这里只补元数据
            print(f"\n📖 Literature Research: {args.query}")
        else:
            print(f"\n📖 Literature Research: {args.query}")
            print(f"\n{content}")
        if refs:
            print(f"\n📚 References: {len(refs)} papers")
        if trace:
            print(f"\n🔍 Trace ID: {trace}")

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(json.dumps(result, indent=2, ensure_ascii=False))
            print(f"\n💾 Saved to {args.output}", file=sys.stderr)
        except OSError as e:
            print(f"⚠️  Failed to save file: {e}", file=sys.stderr)


def handle_sources(args) -> None:
    print("📚 Data source: ScienceRiver (科应开放平台)\n")
    print(f"  • Base URL: {BASE_URL}")
    print("  • Search API:")
    print("    - POST /open/api/search/general        (表达式检索)")
    print("    - POST /open/api/search/semantic       (语义检索)")
    print("  • Document Info API:")
    print("    - POST /open/api/search/basicInfo      (基本信息, 需 srid 或 doi)")
    print("    - GET  /open/api/search/pdfUrl         (PDF 直链, 需 srid)")
    print("    - GET  /open/api/search/patent/legalInfo (专利法律信息, 需 srid)")
    print("    - GET  /open/api/search/patent/family    (专利同族信息, 需 srid)")
    print("  • Auth: Bearer accessToken (OAuth2 client_credentials)")
    print("  • Token TTL: 86400s (24h), auto-refresh with 5min grace period")
    print("  • Supports: papers, patents, standards")
    print("  • Max pageSize: 20")
    print("  • Query syntax: keywords, field limits (TI=, PY=), boolean (AND/OR/NOT)")


def handle_sessions(args) -> None:
    mgr = SessionManager()
    if args.cleanup:
        n = mgr.cleanup_old()
        print(f"🧹 Cleaned up {n} expired session(s)")
        return

    sessions = []
    for f in mgr.dir.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            sessions.append({
                "session_id": data.get("session_id"),
                "topic": data.get("topic", ""),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "queries": len(data.get("query_chain", [])),
                "docs_seen": len(data.get("seen_doc_ids", {})),
                "depth": data.get("exploration_depth", 0),
            })
        except Exception:
            pass

    sessions.sort(key=lambda x: x["updated_at"], reverse=True)

    if args.format == "json":
        print(json.dumps(sessions, indent=2, ensure_ascii=False))
    else:
        if not sessions:
            print("No active sessions.")
            return
        print(f"{'Session ID':<12} {'Topic':<25} {'Queries':>8} {'Docs':>8} {'Depth':>6} {'Updated':>12}")
        print("-" * 70)
        for s in sessions:
            updated = time.strftime("%m-%d %H:%M", time.localtime(s["updated_at"]))
            topic = s["topic"][:24] + "..." if len(s["topic"]) > 25 else s["topic"]
            print(f"{s['session_id']:<12} {topic:<25} {s['queries']:>8} {s['docs_seen']:>8} {s['depth']:>6} {updated:>12}")


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "search": handle_search,
        "semantic": handle_semantic,
        "info": handle_info,
        "evolution": handle_evolution,
        "fulltext-qa": handle_fulltext_qa,
        "research": handle_research,
        "schema": handle_schema,
        "sources": handle_sources,
        "sessions": handle_sessions,
    }

    handler = handlers.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

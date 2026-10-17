#!/usr/bin/env python3
"""扫描 OWNER 及 EXTRA_OWNERS 名下所有开启了 GitHub Pages 的仓库，生成 sites.json 索引数据。

数据来源（合并去重，尽量保证全量覆盖）：
1. 带令牌的 search API 与 /users/{OWNER}/repos（受令牌范围限制，但可覆盖可访问的私有仓库）
2. 匿名 search API（完整公开仓库索引，不受令牌范围限制）
3. 上一次生成的 sites.json（兜底合并，避免扫描接口偶发失败导致站点丢失）
"""

import datetime
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

OWNER = "pengyuyan29"
# 友情账号：其公开的 GitHub Pages 仓库也会被一并收录（与 OWNER 自己的仓库同等处理，增删只需改这一行）
EXTRA_OWNERS = ["xiyan122", "Qianshuy99"]
# 手动补充站点：自动扫描发现不了的（如仓库已删除/改名、仅剩 Pages 自定义域名跳转）在这里登记
EXTRA_SITES = [
    {"repo": "musicplayer", "owner": "xiyan122", "url": "http://linxiz.loc.cc/",
     "description": "音乐播放器（原 xiyan122/musicplayer，已绑定自定义域名）"},
]
SCAN_OWNERS = [OWNER] + [o for o in EXTRA_OWNERS if o and o.lower() != OWNER.lower()]
SELF_REPO = f"{OWNER}.github.io"  # 本索引站自身，不收录
CUSTOM_DOMAIN = "88.202991001.xyz"  # 导航站域名，HTTPS 由 Cloudflare 边缘证书提供，始终可用
TOKEN = os.environ.get("GH_TOKEN", "")
UA = "pages-index-sync"


def api(path: str, auth: bool = True, timeout: int = 30):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": UA,
        },
    )
    if auth and TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except Exception as e:
        print(f"  api {path} failed: {e}")
        return None


def list_repos() -> dict:
    by_key = {}

    def merge(items):
        for r in items or []:
            full = (r.get("full_name") or "").strip()
            if not full:
                continue
            # 私有仓库信息优先（匿名接口看不到私有仓库，不会互相覆盖）
            if full in by_key and by_key[full].get("visibility") == "public" \
                    and r.get("visibility") == "private":
                by_key[full] = r
            else:
                by_key.setdefault(full, r)

    for owner in SCAN_OWNERS:
        # 1) 带令牌的 search（可能被令牌范围过滤）
        d = api(f"/search/repositories?q=user:{owner}&per_page=100", auth=True)
        merge(d.get("items") if d else None)

        # 2) 带令牌的仓库列表（可覆盖令牌可访问的私有仓库）
        page = 1
        while True:
            batch = api(f"/users/{owner}/repos?per_page=100&page={page}", auth=True)
            if not batch:
                break
            merge(batch)
            if len(batch) < 100:
                break
            page += 1

        # 3) 匿名 search：完整公开仓库索引，不受令牌范围影响
        d = api(f"/search/repositories?q=user:{owner}&per_page=100", auth=False)
        merge(d.get("items") if d else None)

        # 4) 匿名仓库列表：完整公开仓库清单（搜索接口被限流时仍能覆盖全部公开仓库）
        page = 1
        while True:
            batch = api(f"/users/{owner}/repos?per_page=100&page={page}", auth=False)
            if not isinstance(batch, list) or not batch:
                break
            merge(batch)
            if len(batch) < 100:
                break
            page += 1

    return by_key


def load_previous() -> dict:
    try:
        with open("sites.json", encoding="utf-8") as f:
            return {s["repo"]: s for s in json.load(f).get("sites", [])}
    except Exception:
        return {}


def head_check(url: str):
    """请求检测，返回 (ok, final_url)。跟随重定向。"""
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status < 400, resp.geturl()
    except urllib.error.HTTPError as e:
        return e.code < 400, getattr(e, "url", url)
    except Exception:
        return False, url


def try_https(url: str) -> str:
    """把 http:// 链接升级为 https://。自定义域名直接强制升级；
    其他域名重试探测，确认可用才升级（页面密码加密依赖 HTTPS）。"""
    if not url.startswith("http://"):
        return url
    https_url = "https://" + url[len("http://"):]
    if urlparse(url).netloc.lower() == CUSTOM_DOMAIN:
        return https_url
    for _ in range(3):
        ok, _ = head_check(https_url)
        if ok:
            return https_url
    return url


def build_site(repo: dict, previous: dict) -> dict:
    name = repo["name"]
    owner = (repo.get("owner") or {}).get("login") or OWNER
    is_user_site = name.lower() == f"{owner.lower()}.github.io"

    # 地址优先级：Pages API 规范地址 > 默认地址（用户站挂根路径，其余为项目子路径）
    url = f"https://{owner}.github.io/" if is_user_site else f"https://{owner}.github.io/{name}/"
    pages = api(f"/repos/{owner}/{name}/pages", auth=True)
    if pages and pages.get("html_url"):
        url = pages["html_url"]

    # 若 Pages API 不可用，通过重定向识别自定义域名（仅当重定向目标确实可访问时才采用）
    if not pages:
        ok, final = head_check(url)
        github_host = f"{owner}.github.io"
        if ok and final and urlparse(final).netloc.lower() != github_host:
            ok3, _ = head_check(url)
            if not ok3:
                url = f"https://{github_host}/" if is_user_site else f"https://{github_host}/{name}/"

    # 统一升级为 HTTPS（未开启 Enforce HTTPS 时 Pages API 返回 http://，
    # 而页面密码加密依赖 HTTPS 下的 crypto.subtle）
    url = try_https(url)

    ok, _ = head_check(url)

    return {
        "repo": name,
        "owner": owner,
        "url": url,
        "description": repo.get("description") or previous.get(name, {}).get("description", ""),
        "language": repo.get("language"),
        "topics": repo.get("topics") or [],
        "pushed_at": repo.get("pushed_at"),
        "status": "online" if ok else "offline",
    }


def load_issue_links() -> list:
    """读取本仓库的开放 issue 作为自定义链接，分为两类：

    标题以【收藏】开头 → 收藏夹（cat=collect，默认）
    标题以【自建】开头 → 自建站（cat=self）
    正文第一行 = URL，其余行 = 描述（可选）。关闭 issue 即从导航站移除。
    """
    items, page = [], 1
    while True:
        d = api(f"/repos/{OWNER}/{SELF_REPO}/issues?state=open&per_page=100&page={page}", auth=True)
        if not d:
            break
        items.extend(d)
        if len(d) < 100:
            break
        page += 1

    markers = {"【收藏】": "collect", "【自建】": "self"}
    out = []
    for it in items:
        if "pull_request" in it:
            continue
        title = (it.get("title") or "").strip()
        cat = next((c for p, c in markers.items() if title.startswith(p)), None)
        if not cat:
            continue
        name = title[title.index("】") + 1:].strip()
        lines = [l.strip() for l in (it.get("body") or "").splitlines() if l.strip()]
        url = next((l for l in lines if l.lower().startswith(("http://", "https://"))), "")
        if not name or not url:
            print(f"  skip invalid issue #{it.get('number')}: {title!r}")
            continue
        desc = " ".join(l for l in lines if l != url)[:200]
        out.append({
            "repo": name,
            "url": url,
            "description": desc,
            "language": None,
            "topics": [],
            "pushed_at": None,
            "status": "custom",
            "cat": cat,
        })
    print(f"Loaded {len(out)} custom link(s) from issues")
    return out


def main() -> None:
    previous = load_previous()
    repos = list_repos()
    print(f"Discovered {len(repos)} repos")

    sites = []
    seen = set()
    for repo in repos.values():
        if not repo.get("has_pages"):
            continue
        name = repo["name"]
        if name == SELF_REPO:
            continue
        seen.add(name)
        sites.append(build_site(repo, previous))

    # 兜底：上次存在但这次没扫到的站点，保留并复查状态（自定义收藏不兜底，随 issue 开关增删）
    for name, old in previous.items():
        if name not in seen and name != SELF_REPO and old.get("status") != "custom":
            old = dict(old)
            host = urlparse(old.get("url", "")).netloc.lower()
            if host.endswith(".github.io"):
                old.setdefault("owner", host.split(".")[0])
            old.setdefault("owner", OWNER)  # 自定义域名等无法从 URL 推断时，默认归主账号
            old["url"] = try_https(old.get("url", ""))
            ok, _ = head_check(old["url"])
            old["status"] = "online" if ok else "offline"
            sites.append(old)
            print(f"  kept from previous data: {name}")

    # 合并手动补充站点（自动扫描发现不了的），URL 去重后照常探测在线状态
    known_urls = {s["url"].rstrip("/") for s in sites}
    for e in EXTRA_SITES:
        url = try_https(e.get("url", ""))
        if url.rstrip("/") in known_urls:
            continue
        ok, _ = head_check(url)
        sites.append({
            "repo": e["repo"],
            "owner": e.get("owner", OWNER),
            "url": url,
            "description": e.get("description", ""),
            "language": None,
            "topics": [],
            "pushed_at": None,
            "status": "online" if ok else "offline",
        })
        print(f"  extra site: {e['repo']} -> {url} ({'online' if ok else 'offline'})")

    # 合并自定义收藏（issue 驱动），去重：同一 URL 以自动收录为准
    issue_links = load_issue_links()
    known_urls = {s["url"].rstrip("/") for s in sites}
    for c in issue_links:
        if c["url"].rstrip("/") not in known_urls:
            sites.append(c)

    # 两次稳定排序：收藏（issue/links）始终在前，其余按最近更新倒序
    sites.sort(key=lambda s: s["pushed_at"] or "", reverse=True)
    sites.sort(key=lambda s: s["status"] != "custom")
    data = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sites": sites,
    }

    with open("sites.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Wrote sites.json with {len(sites)} sites")


if __name__ == "__main__":
    main()

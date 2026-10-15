#!/usr/bin/env python3
"""扫描 OWNER 名下所有开启了 GitHub Pages 的仓库，生成 sites.json 索引数据。

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
SELF_REPO = f"{OWNER}.github.io"  # 本索引站自身，不收录
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
    by_name = {}

    def merge(items):
        for r in items or []:
            name = r.get("name")
            if not name:
                continue
            # 私有仓库信息优先（匿名接口看不到私有仓库，不会互相覆盖）
            if name in by_name and by_name[name].get("visibility") == "public" \
                    and r.get("visibility") == "private":
                by_name[name] = r
            else:
                by_name.setdefault(name, r)

    # 1) 带令牌的 search（可能被令牌范围过滤）
    d = api(f"/search/repositories?q=user:{OWNER}&per_page=100", auth=True)
    merge(d.get("items") if d else None)

    # 2) 带令牌的仓库列表（可覆盖令牌可访问的私有仓库）
    page = 1
    while True:
        batch = api(f"/users/{OWNER}/repos?per_page=100&page={page}", auth=True)
        if not batch:
            break
        merge(batch)
        if len(batch) < 100:
            break
        page += 1

    # 3) 匿名 search：完整公开仓库索引，不受令牌范围影响
    d = api(f"/search/repositories?q=user:{OWNER}&per_page=100", auth=False)
    merge(d.get("items") if d else None)

    return by_name


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


def build_site(repo: dict, previous: dict) -> dict:
    name = repo["name"]

    # 地址优先级：Pages API 规范地址 > 默认地址
    url = f"https://{OWNER}.github.io/{name}/"
    pages = api(f"/repos/{OWNER}/{name}/pages", auth=True)
    if pages and pages.get("html_url"):
        url = pages["html_url"]

    # 若 Pages API 不可用，通过重定向识别自定义域名（仅当重定向目标确实可访问时才采用）
    if not pages:
        ok, final = head_check(url)
        github_host = f"{OWNER}.github.io"
        if ok and final and urlparse(final).netloc.lower() != github_host:
            if url.startswith("http://"):
                https_url = "https://" + url[len("http://"):]
                ok2, _ = head_check(https_url)
                url = https_url if ok2 else url
            ok3, _ = head_check(url)
            if not ok3:
                url = f"https://{github_host}/{name}/"

    ok, _ = head_check(url)

    return {
        "repo": name,
        "url": url,
        "description": repo.get("description") or previous.get(name, {}).get("description", ""),
        "language": repo.get("language"),
        "topics": repo.get("topics") or [],
        "pushed_at": repo.get("pushed_at"),
        "status": "online" if ok else "offline",
    }


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

    # 兜底：上次存在但这次没扫到的站点，保留并复查状态
    for name, old in previous.items():
        if name not in seen and name != SELF_REPO:
            old = dict(old)
            ok, _ = head_check(old.get("url", ""))
            old["status"] = "online" if ok else "offline"
            sites.append(old)
            print(f"  kept from previous data: {name}")

    sites.sort(key=lambda s: s["pushed_at"] or "", reverse=True)
    data = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sites": sites,
    }

    with open("sites.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Wrote sites.json with {len(sites)} sites")


if __name__ == "__main__":
    main()

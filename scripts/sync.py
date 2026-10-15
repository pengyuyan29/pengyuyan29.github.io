#!/usr/bin/env python3
"""扫描 OWNER 名下所有开启了 GitHub Pages 的仓库，生成 sites.json 索引数据。

数据来源合并两个接口，保证覆盖面：
1. search/repositories —— 返回全部公开仓库（不受令牌安装范围限制）
2. /users/{OWNER}/repos —— 可补充令牌可访问的私有仓库
"""

import datetime
import json
import os
import urllib.error
import urllib.request

OWNER = "pengyuyan29"
SELF_REPO = f"{OWNER}.github.io"  # 本索引站自身，不收录
TOKEN = os.environ.get("GH_TOKEN", "")
UA = "pages-index-sync"


def api(path: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": UA,
        },
    )
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def list_repos() -> list:
    by_name = {}

    # 1) 全量公开仓库（search 不受令牌安装范围影响）
    try:
        page = 1
        while True:
            d = api(f"/search/repositories?q=user:{OWNER}&per_page=100&page={page}")
            items = d.get("items", [])
            for r in items:
                by_name[r["name"]] = r
            if len(items) < 100:
                break
            page += 1
    except Exception as e:
        print(f"search listing failed: {e}")

    # 2) 令牌可访问的仓库（可能包含私有仓库）
    try:
        page = 1
        while True:
            batch = api(f"/users/{OWNER}/repos?per_page=100&page={page}")
            for r in batch:
                by_name.setdefault(r["name"], r)
            if len(batch) < 100:
                break
            page += 1
    except Exception as e:
        print(f"repos listing failed: {e}")

    return list(by_name.values())


def head_check(url: str):
    """HEAD 检测，返回 (ok, final_url)。跟随重定向以识别自定义域名。"""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status < 400, resp.geturl()
    except urllib.error.HTTPError as e:
        return e.code < 400, getattr(e, "url", url)
    except Exception:
        return False, url


def main() -> None:
    sites = []
    for repo in list_repos():
        if not repo.get("has_pages"):
            continue
        name = repo["name"]
        if name == SELF_REPO:
            continue

        # Pages API 可拿到规范地址（含自定义域名），无权限时回退默认地址
        url = f"https://{OWNER}.github.io/{name}/"
        try:
            pages = api(f"/repos/{OWNER}/{name}/pages")
            if pages.get("html_url"):
                url = pages["html_url"]
        except Exception:
            pass

        ok, final = head_check(url)
        if ok and final:
            url = final
            # 自定义域名若是 http，尝试升级为 https
            if url.startswith("http://"):
                https_url = "https://" + url[len("http://"):]
                ok2, _ = head_check(https_url)
                if ok2:
                    url = https_url
            final_ok, _ = head_check(url)
        else:
            final_ok = ok

        sites.append({
            "repo": name,
            "url": url,
            "description": repo.get("description") or "",
            "language": repo.get("language"),
            "topics": repo.get("topics") or [],
            "pushed_at": repo.get("pushed_at"),
            "status": "online" if final_ok else "offline",
        })

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

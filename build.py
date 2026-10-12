#!/usr/bin/env python3
"""毎朝のニュースダッシュボード生成スクリプト。
Googleニュース RSS をカテゴリ別に取得し、index.html を出力する。
カテゴリ/キーワードは下の CATEGORIES を編集すれば変更できる。
"""
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

JST = timezone(timedelta(hours=9))
OUT_DIR = Path(__file__).parent
MAX_PER_CATEGORY = 15
MAX_AGE_DAYS = 3  # これより古い記事は除外
LIMIT_OVERRIDE = {"競合チャンネル": 40}  # カテゴリ別の表示件数

# (カテゴリ名, 説明, [検索クエリ...])
CATEGORIES = [
    ("CS放送・スカパー！", "自社と直接関わる動き", [
        "スカパー", "CS放送", "スカパーJSAT 放送", "スカパー 新チャンネル OR 新番組 OR 料金",
    ]),
    ("競合チャンネル", "他社CS・BS専門チャンネルの動き", [
        '"チャンネル銀河"', '"チャンネルNECO"', "日本映画専門チャンネル", "ホームドラマチャンネル",
        '"LaLa TV"', "衛星劇場", "WOWOW", "歌謡ポップスチャンネル", "時代劇専門チャンネル", "ファミリー劇場",
    ]),
    ("ケーブルテレビ・放送業界","CATV・衛星・放送制度・配信との競合", [
        "ケーブルテレビ", "JCOM 放送", "BS放送 4K", "放送業界 配信 視聴", "NHK 民放 ネット配信",
    ]),
    ("中国ドラマ・時代劇", "自社の主力ジャンル。新作・配信・話題作・俳優", [
        '"中国ドラマ"', '"中国時代劇"', '"華流"', '"華流ドラマ"', '"中華ドラマ"', '"古装劇"',
        '"中国ドラマ" 日本初放送 OR 日本初上陸', '"中国ドラマ" 配信 OR 放送',
    ]),
    ("ドラマ・音楽コンテンツ","競合・トレンド・ヒット作", [
        "ドラマ 視聴率", "韓国ドラマ 日本 放送", "音楽番組 新番組", "音楽ライブ 配信 放送", "動画配信 ドラマ 独占配信",
    ]),
    ("マーケティング・広告", "広告市況・SNS・Web施策", [
        "広告 マーケティング 最新", "テレビCM 広告費", "SNS運用 X YouTube 施策", "デジタル広告 動向", "電通 博報堂 広告",
    ]),
    ("SNS・プラットフォーム動向", "X・YouTubeの仕様変更やトレンド", [
        "X 旧Twitter 新機能 仕様変更", "YouTube 新機能 アルゴリズム", "YouTube Shorts 広告",
    ]),
    ("PR・メディア対応", "プレスリリース・広報のトピック", [
        "プレスリリース 配信 トレンド", "広報 PR メディアリレーション",
    ]),
]


def fetch(query: str):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{query} when:3d", "hl": "ja", "gl": "JP", "ceid": "JP:ja"}
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            root = ET.fromstring(r.read())
    except Exception as e:  # 1クエリの失敗で全体を止めない
        print(f"WARN fetch failed: {query}: {e}")
        return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        source = (src_el.text if src_el is not None else "") or ""
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(JST)
        except Exception:
            continue
        # 「タイトル - 媒体名」の媒体名部分を除去
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        items.append({"title": title, "link": link, "source": source, "dt": dt, "query": query})
    return items


def norm(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", t)[:30]


def collect():
    now = datetime.now(JST)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    seen = set()
    result = []
    with ThreadPoolExecutor(8) as ex:
        for name, desc, queries in CATEGORIES:
            batches = list(ex.map(fetch, queries))
            items = []
            for b in batches:
                for it in b:
                    key = norm(it["title"])
                    if it["dt"] < cutoff or key in seen:
                        continue
                    seen.add(key)
                    items.append(it)
            items.sort(key=lambda x: x["dt"], reverse=True)
            result.append((name, desc, items[:LIMIT_OVERRIDE.get(name, MAX_PER_CATEGORY)], len(items)))
    return now, result


def rel(dt, now):
    m = int((now - dt).total_seconds() // 60)
    if m < 60:
        return f"{max(m, 1)}分前"
    if m < 60 * 24:
        return f"{m // 60}時間前"
    return f"{m // (60 * 24)}日前"


def tag(it, cat):
    if cat != "競合チャンネル":
        return ""
    return f'<i class="tag">{html.escape(it["query"].strip(chr(34)))}</i> '


def render(now, result):
    sections = []
    nav = []
    for i, (name, desc, items, total) in enumerate(result):
        nav.append(f'<a href="#c{i}">{html.escape(name)}<span>{len(items)}</span></a>')
        lis = []
        for it in items:
            is_new = (now - it["dt"]) < timedelta(hours=24)
            badge = '<b class="new">NEW</b>' if is_new else ""
            lis.append(
                f'<li><a href="{html.escape(it["link"])}" target="_blank" rel="noopener">'
                f'{badge}{html.escape(it["title"])}</a>'
                f'<div class="meta">{tag(it, name)}{html.escape(it["source"])} ・ {it["dt"]:%m/%d %H:%M}（{rel(it["dt"], now)}）</div></li>'
            )
        body = "\n".join(lis) or '<li class="empty">該当ニュースなし</li>'
        sections.append(
            f'<section id="c{i}"><h2>{html.escape(name)}</h2><p class="desc">{html.escape(desc)}</p><ul>{body}</ul></section>'
        )
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>朝のニュースダッシュボード {now:%Y/%m/%d}</title>
<style>
:root{{--bg:#f5f6f8;--card:#fff;--fg:#1c2330;--sub:#667085;--line:#e4e7ec;--acc:#1f5fd1;--new:#e5484d}}
@media(prefers-color-scheme:dark){{:root{{--bg:#12151b;--card:#1b2029;--fg:#e8ebf0;--sub:#98a2b3;--line:#2a313d;--acc:#7aa7ff;--new:#ff6b70}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Hiragino Sans","Yu Gothic",sans-serif}}
header{{padding:20px 16px 8px;max-width:1280px;margin:auto}}
h1{{margin:0;font-size:22px}}
.updated{{color:var(--sub);font-size:13px}}
nav{{position:sticky;top:0;background:var(--bg);padding:8px 16px;border-bottom:1px solid var(--line);z-index:5;display:flex;gap:8px;flex-wrap:wrap;max-width:1280px;margin:auto}}
nav a{{padding:4px 12px;border:1px solid var(--line);border-radius:99px;color:var(--fg);text-decoration:none;font-size:13px;background:var(--card)}}
nav a span{{margin-left:6px;color:var(--sub)}}
#q{{margin-left:auto;padding:5px 12px;border:1px solid var(--line);border-radius:99px;background:var(--card);color:var(--fg);min-width:180px}}
main{{max-width:1280px;margin:auto;padding:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr));gap:16px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}
h2{{margin:0;font-size:17px}}
.desc{{margin:2px 0 8px;color:var(--sub);font-size:12px}}
ul{{list-style:none;margin:0;padding:0}}
li{{padding:9px 0;border-top:1px solid var(--line)}}
li a{{color:var(--fg);text-decoration:none;font-weight:500}}
li a:hover{{color:var(--acc);text-decoration:underline}}
.meta{{color:var(--sub);font-size:12px}}
.new{{background:var(--new);color:#fff;font-size:10px;padding:1px 6px;border-radius:4px;margin-right:6px;vertical-align:1px}}
.tag{{font-style:normal;background:var(--line);padding:0 7px;border-radius:4px;margin-right:6px}}
.empty{{color:var(--sub)}}
li.hide{{display:none}}
</style></head><body>
<header><h1>📺 朝のニュースダッシュボード</h1>
<div class="updated">最終更新: {now:%Y/%m/%d %H:%M}（毎朝5:00に自動更新）・直近{MAX_AGE_DAYS}日分・NEW＝24時間以内</div></header>
<nav>{"".join(nav)}<input id="q" type="search" placeholder="キーワードで絞り込み"></nav>
<main>{"".join(sections)}</main>
<script>
document.getElementById('q').addEventListener('input',e=>{{
  const v=e.target.value.trim().toLowerCase();
  document.querySelectorAll('li').forEach(li=>li.classList.toggle('hide',!!v&&!li.textContent.toLowerCase().includes(v)));
}});
</script></body></html>"""


def main():
    now, result = collect()
    (OUT_DIR / "index.html").write_text(render(now, result), encoding="utf-8")
    archive = OUT_DIR / "archive"
    archive.mkdir(exist_ok=True)
    (archive / f"{now:%Y-%m-%d}.json").write_text(
        json.dumps(
            [{"category": n, "items": [{**i, "dt": i["dt"].isoformat()} for i in its]} for n, _, its, _ in result],
            ensure_ascii=False, indent=1),
        encoding="utf-8")
    for n, _, its, total in result:
        print(f"{n}: {len(its)}件表示 / {total}件")


if __name__ == "__main__":
    main()

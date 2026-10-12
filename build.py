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
LIMIT_OVERRIDE = {"競合チャンネル": 40, "注目俳優（中国）": 30}
AGE_OVERRIDE = {"注目俳優（中国）": 14}  # カテゴリ別の対象日数
# タイトルにこれらを含む記事は除外（スポーツ系）。不要な語は消し、必要な語は足してください
EXCLUDE_WORDS = [
    "スポーツ", "野球", "プロ野球", "MLB", "ドジャース", "大谷翔平", "侍ジャパン", "甲子園", "サッカー", "Jリーグ",
    "J1", "J2", "J3", "明治安田", "ハイライト動画", "Jリーグ", "WEリーグ", "W杯", "ワールドカップ", "日本代表", "なでしこ", "五輪", "オリンピック", "パラリンピック", "バレーボール",
    "バスケ", "Bリーグ", "ラグビー", "テニス", "ゴルフ", "競馬", "競輪", "競艇", "相撲", "場所", "マラソン", "駅伝",
    "陸上", "水泳", "体操", "柔道", "ボクシング", "格闘技", "RIZIN", "UFC", "F1", "モータースポーツ", "ハードル",
    "試合", "リーグ戦", "ドラフト", "卓球", "フィギュア", "スキー", "スケート", "ボウリング",
]
# ハイライトに載せるカテゴリと、1カテゴリあたりの最大件数 / 各カテゴリの初期表示件数
PRIORITY = ["CS放送・スカパー！", "競合チャンネル", "中国ドラマ・時代劇", "注目俳優（中国）"]
HIGHLIGHT_PER_CATEGORY = 4
INITIAL_SHOWN = 8
TAG_CATEGORIES = {"競合チャンネル", "注目俳優（中国）"}  # 検索語タグを付けるカテゴリ  # カテゴリ別の表示件数

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
    ("注目俳優（中国）", "チェック対象の中国人俳優の最新ニュース", [
        '"ワン・イーボー" OR 王一博', '"シャオ・ジャン" OR 肖戦', '"シュー・カイ" OR 許凱 OR "徐開"',
        '"リウ・ユーニン" OR 劉宇寧', '"チャオ・ルースー" OR 趙露思', '"チャン・ルオユン" OR 張若昀',
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


def fetch(query: str, days: int = 3):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{query} when:{days}d", "hl": "ja", "gl": "JP", "ceid": "JP:ja"}
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


# フォトギャラリー・プロフィールなど「記事ではないページ」を除外するパターン
# （例: 「画像・写真 〜」「＜画像1 / 11＞〜」「[画像・動画ギャラリー 8/10]」「〜のプロフィール・画像・写真」）
OLD_PAGE_PATTERN = re.compile(
    r"^\s*画像・写真|^\s*[（(]画像[）)]|[＜<]?\s*画像\s*\d+\s*[/／]\s*\d+\s*[＞>]"
    r"|画像・動画ギャラリー|プロフィール・画像|フォトギャラリー|写真・図版|【写真・画像|写真特集|フォト[:：]|の写真一覧|\d+\s*[/／]\s*\d+\s*[\]］）)]?\s*$"
)


def norm(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", t)[:30]


def collect():
    now = datetime.now(JST)
    seen = set()
    result = []
    with ThreadPoolExecutor(8) as ex:
        for name, desc, queries in CATEGORIES:
            days = AGE_OVERRIDE.get(name, MAX_AGE_DAYS)
            batches = list(ex.map(lambda q: fetch(q, days), queries))
            cutoff = now - timedelta(days=days)  # カテゴリごとに算出
            items = []
            for b in batches:
                for it in b:
                    key = norm(it["title"])
                    if it["dt"] < cutoff or key in seen or any(w in it["title"] for w in EXCLUDE_WORDS) or OLD_PAGE_PATTERN.search(it["title"]):
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
    if cat not in TAG_CATEGORIES:
        return ""
    return f'<i class="tag">{html.escape(it["query"].split(" OR ")[0].strip(chr(34)))}</i>'


def is_new(it, now):
    return (now - it["dt"]) < timedelta(hours=24)


def row(it, cat, now, show_cat=False):
    new = is_new(it, now)
    cat_label = f'<i class="cat">{html.escape(cat)}</i>' if show_cat else ""
    return (
        f'<li class="item{" is-new" if new else ""}" data-search="{html.escape(it["title"] + " " + it["source"] + " " + cat + " " + it["query"])}">'
        f'<a class="t" href="{html.escape(it["link"])}" target="_blank" rel="noopener">{html.escape(it["title"])}</a>'
        f'<div class="meta">{"<b class=new>NEW</b>" if new else ""}{cat_label}{tag(it, cat)}'
        f'<span>{html.escape(it["source"])}</span><span>{it["dt"]:%m/%d %H:%M}（{rel(it["dt"], now)}）</span></div></li>'
    )


def render(now, result):
    # ハイライト: 重要カテゴリの直近24時間（足りなければ最新2件）
    hl = []
    for name, _, items, _ in result:
        if name not in PRIORITY:
            continue
        pick = [i for i in items if is_new(i, now)][:HIGHLIGHT_PER_CATEGORY]
        hl += [(name, i) for i in (pick or items[:2])]
    hl_html = "".join(row(i, n, now, True) for n, i in hl) or '<li class="empty">該当なし</li>'

    nav = [f'<a href="#hl" class="on" data-p="hl">★ 今朝のハイライト<span class="n">{len(hl)}</span></a>']
    panels = [
        f'<section class="panel" id="p-hl"><h2>★ 今朝のハイライト</h2>'
        f'<p class="desc">自社・競合・中国ドラマ・注目俳優の、24時間以内の新着から抜粋</p><ul>{hl_html}</ul></section>'
    ]
    for i, (name, desc, items, total) in enumerate(result):
        n_new = sum(is_new(x, now) for x in items)
        badge = f'<span class="n new">{n_new}</span>' if n_new else f'<span class="n">{len(items)}</span>'
        nav.append(f'<a href="#c{i}" data-p="c{i}">{html.escape(name)}{badge}</a>')
        rows = [row(x, name, now) for x in items]
        head, rest = rows[:INITIAL_SHOWN], rows[INITIAL_SHOWN:]
        more = (f'<button class="more" type="button">さらに表示（{len(rest)}件）</button>' if rest else "")
        body = "".join(head) + (f'<span class="rest">{"".join(rest)}</span>' if rest else "") or '<li class="empty">該当ニュースなし</li>'
        panels.append(
            f'<section class="panel" id="p-c{i}" hidden><h2>{html.escape(name)}</h2>'
            f'<p class="desc">{html.escape(desc)}（新着{n_new}件 / 全{len(items)}件）</p><ul>{body}</ul>{more}</section>'
        )
    panels.append('<section class="panel" id="p-search" hidden><h2>検索結果</h2><p class="desc" id="sdesc"></p><ul id="sres"></ul></section>')

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>朝のニュース {now:%m/%d}</title>
<style>
:root{{--bg:#f4f5f7;--card:#fff;--fg:#1c2330;--sub:#667085;--line:#e4e7ec;--acc:#1f5fd1;--accbg:#e8effc;--new:#e5484d}}
@media(prefers-color-scheme:dark){{:root{{--bg:#12151b;--card:#1b2029;--fg:#e8ebf0;--sub:#98a2b3;--line:#2a313d;--acc:#7aa7ff;--accbg:#233049;--new:#ff6b70}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,"Hiragino Sans","Yu Gothic",sans-serif}}
.wrap{{display:grid;grid-template-columns:260px 1fr;max-width:1200px;margin:auto;min-height:100vh}}
aside{{padding:20px 12px;position:sticky;top:0;align-self:start;max-height:100vh;overflow:auto}}
h1{{font-size:17px;margin:0 8px 2px}}
.updated{{color:var(--sub);font-size:12px;margin:0 8px 12px}}
#q{{width:100%;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);margin-bottom:10px;font-size:14px}}
nav a{{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;color:var(--fg);text-decoration:none;font-size:14px}}
nav a:hover{{background:var(--line)}}
nav a.on{{background:var(--accbg);color:var(--acc);font-weight:600}}
.n{{font-size:12px;color:var(--sub);min-width:22px;text-align:center}}
.n.new{{background:var(--new);color:#fff;border-radius:99px;padding:0 7px}}
.opts{{margin:12px 8px;font-size:13px;color:var(--sub)}}
main{{padding:20px 16px 60px;min-width:0}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}}
.panel[hidden]{{display:none}}
h2{{margin:0;font-size:19px}}
.desc{{margin:2px 0 10px;color:var(--sub);font-size:13px}}
ul{{list-style:none;margin:0;padding:0}}
li.item{{padding:11px 0;border-top:1px solid var(--line)}}
.t{{color:var(--fg);text-decoration:none;font-weight:600;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.t:hover{{color:var(--acc)}}
.t:visited,li.read .t{{color:var(--sub);font-weight:400}}
.meta{{color:var(--sub);font-size:12px;display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center;margin-top:2px}}
.new{{background:var(--new);color:#fff;font-size:10px;padding:1px 6px;border-radius:4px;font-weight:700}}
.tag,.cat{{font-style:normal;background:var(--line);padding:0 7px;border-radius:4px}}
.cat{{background:var(--accbg);color:var(--acc)}}
.more{{margin-top:10px;width:100%;padding:10px;border:1px solid var(--line);background:none;border-radius:8px;color:var(--acc);font-size:14px;cursor:pointer}}
.rest{{display:none}} .panel.open .rest{{display:contents}} .panel.open .more{{display:none}}
.only-new li.item:not(.is-new){{display:none}}
.empty{{color:var(--sub);padding:12px 0}}
@media(max-width:800px){{
  .wrap{{display:block}}
  aside{{position:static;max-height:none;padding:14px 12px 0}}
  nav{{display:flex;gap:6px;overflow-x:auto;padding-bottom:8px;-webkit-overflow-scrolling:touch}}
  nav a{{white-space:nowrap;border:1px solid var(--line);background:var(--card)}}
  nav a.on{{border-color:var(--acc)}}
  main{{padding:8px 12px 40px}}
}}
</style></head><body>
<div class="wrap">
<aside>
<h1>📺 朝のニュース</h1>
<div class="updated">更新 {now:%Y/%m/%d %H:%M}・毎朝5:00自動</div>
<input id="q" type="search" placeholder="全カテゴリから検索">
<nav>{"".join(nav)}</nav>
<label class="opts"><input type="checkbox" id="onlynew"> 24時間以内の新着だけ表示</label>
</aside>
<main id="main">{"".join(panels)}</main>
</div>
<script>
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
let cur='hl';
function show(id){{
  cur=id;
  $$('.panel').forEach(p=>p.hidden=p.id!=='p-'+id);
  $$('nav a').forEach(a=>a.classList.toggle('on',a.dataset.p===id));
  window.scrollTo(0,0);
}}
$$('nav a').forEach(a=>a.addEventListener('click',e=>{{e.preventDefault();$('#q').value='';show(a.dataset.p);history.replaceState(null,'','#'+a.dataset.p)}}));
$$('.more').forEach(b=>b.addEventListener('click',()=>b.closest('.panel').classList.add('open')));
$('#onlynew').addEventListener('change',e=>$('#main').classList.toggle('only-new',e.target.checked));
// 既読管理（このブラウザ内だけに保存）
let read=new Set();
try{{read=new Set(JSON.parse(localStorage.getItem('read')||'[]'))}}catch(e){{}}
function mark(){{$$('li.item').forEach(li=>li.classList.toggle('read',read.has(li.querySelector('a').href)))}}
document.addEventListener('click',e=>{{const a=e.target.closest('li.item .t');if(!a)return;
  read.add(a.href);try{{localStorage.setItem('read',JSON.stringify([...read].slice(-3000)))}}catch(e){{}}mark()}});
mark();
// 検索
$('#q').addEventListener('input',e=>{{
  const v=e.target.value.trim().toLowerCase();
  if(!v){{show(location.hash.slice(1)||'hl');return}}
  const seen=new Set(),out=[];
  $$('.panel:not(#p-search):not(#p-hl) li.item').forEach(li=>{{
    const k=li.querySelector('a').href;
    if(!seen.has(k)&&li.dataset.search.toLowerCase().includes(v)){{seen.add(k);out.push(li.cloneNode(true))}}}});
  $('#sres').replaceChildren(...out);
  $('#sdesc').textContent=out.length+'件ヒット';
  $$('.panel').forEach(p=>p.hidden=p.id!=='p-search');
  mark();
}});
const h=location.hash.slice(1);show($('#p-'+h)?h:'hl');
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

import ast
import html
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ORIGIN = "https://epson-site.vercel.app"
ORIGIN = os.environ.get("SITE_ORIGIN", DEFAULT_ORIGIN).rstrip("/")


def parse_js_literal(source):
    token_pattern = re.compile(
        r"\s+|(?P<string>'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")"
        r"|(?P<number>-?\d+(?:\.\d+)?)"
        r"|(?P<identifier>[A-Za-z_$][\w$]*)"
        r"|(?P<punct>[{}\[\]:,])"
    )
    tokens = []
    position = 0
    while position < len(source):
        match = token_pattern.match(source, position)
        if not match:
            raise ValueError(f"Unsupported JavaScript blog-data syntax at offset {position}.")
        position = match.end()
        if match.lastgroup == "string":
            raw = match.group("string")
            value = json.loads(raw) if raw.startswith('"') else ast.literal_eval(raw)
            tokens.append(("value", value))
        elif match.lastgroup == "number":
            raw = match.group("number")
            tokens.append(("value", float(raw) if "." in raw else int(raw)))
        elif match.lastgroup == "identifier":
            tokens.append(("identifier", match.group("identifier")))
        elif match.lastgroup == "punct":
            tokens.append((match.group("punct"), match.group("punct")))

    index = 0

    def take(kind):
        nonlocal index
        if index >= len(tokens) or tokens[index][0] != kind:
            found = tokens[index][0] if index < len(tokens) else "end of data"
            raise ValueError(f"Invalid blog data: expected {kind}, found {found}.")
        value = tokens[index][1]
        index += 1
        return value

    def parse_value():
        nonlocal index
        if index >= len(tokens):
            raise ValueError("Unexpected end of blog data.")
        kind, value = tokens[index]
        if kind == "value":
            index += 1
            return value
        if kind == "identifier":
            index += 1
            if value == "true":
                return True
            if value == "false":
                return False
            if value == "null":
                return None
            raise ValueError(f"Unsupported JavaScript value in blog data: {value}.")
        if kind == "[":
            take("[")
            result = []
            while index < len(tokens) and tokens[index][0] != "]":
                result.append(parse_value())
                if index < len(tokens) and tokens[index][0] == ",":
                    take(",")
                elif index < len(tokens) and tokens[index][0] != "]":
                    raise ValueError("Expected a comma in a blog-data array.")
            take("]")
            return result
        if kind == "{":
            take("{")
            result = {}
            while index < len(tokens) and tokens[index][0] != "}":
                key_kind, key = tokens[index]
                if key_kind == "identifier":
                    index += 1
                elif key_kind == "value" and isinstance(key, str):
                    index += 1
                else:
                    raise ValueError("Expected an object key in blog data.")
                take(":")
                result[key] = parse_value()
                if index < len(tokens) and tokens[index][0] == ",":
                    take(",")
                elif index < len(tokens) and tokens[index][0] != "}":
                    raise ValueError("Expected a comma in a blog-data object.")
            take("}")
            return result
        raise ValueError(f"Unsupported JavaScript value in blog data: {kind}.")

    value = parse_value()
    if index != len(tokens):
        raise ValueError("Unexpected extra tokens after blog data.")
    return value


def load_posts():
    source = (ROOT / "index.html").read_text(encoding="utf-8")
    match = re.search(r"const blogPosts = (\[[\s\S]*?\]);\s*\n\s*const guides =", source)
    if not match:
        raise ValueError("Could not find the blogPosts data in index.html.")
    posts = parse_js_literal(match.group(1))
    if not isinstance(posts, list) or not posts:
        raise ValueError("No blog posts were found.")
    return posts


def e(value):
    return html.escape(str(value), quote=True)


def format_date(value):
    return datetime.strptime(value, "%Y-%m-%d").strftime("%B %d, %Y").replace(" 0", " ")


def page_styles():
    return """
    :root{color-scheme:light;--ink:#101827;--muted:#5f6774;--line:#ded8d0;--paper:#fff;--background:#f7f4f1;--accent:#c4252b}
    *{box-sizing:border-box}
    body{margin:0;background:var(--background);color:var(--ink);font:16px/1.7 system-ui,-apple-system,"Segoe UI",sans-serif}
    a{color:#8f1a20}
    .site-header{background:var(--paper);border-bottom:1px solid var(--line)}
    .header-inner,main,footer{width:min(100% - 2rem,1060px);margin-inline:auto}
    .header-inner{min-height:76px;display:flex;align-items:center;justify-content:space-between;gap:1rem}
    .brand{color:var(--ink);text-decoration:none;font-size:1.15rem;font-weight:800}
    .brand span{display:block;color:var(--muted);font-size:.7rem;letter-spacing:.12em;text-transform:uppercase}
    nav{display:flex;gap:1rem;flex-wrap:wrap}
    nav a{color:var(--ink);text-decoration:none;font-weight:650}
    main{padding-block:2.25rem 4rem}
    .breadcrumbs{color:var(--muted);font-size:.92rem;margin-bottom:1.25rem}
    .breadcrumbs a{color:inherit}
    article,.post-card{background:var(--paper);border:1px solid var(--line);border-radius:20px;padding:clamp(1.25rem,4vw,2.5rem)}
    article{max-width:900px;margin-inline:auto}
    .category{color:#8f1a20;font-size:.78rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase}
    h1{margin:.5rem 0;font-size:clamp(2rem,5vw,3.2rem);line-height:1.12;letter-spacing:-.035em}
    h2{margin:1.8rem 0 .5rem;font-size:clamp(1.35rem,3vw,1.8rem);line-height:1.25}
    p{color:var(--muted)}
    .date{color:var(--muted);font-size:.9rem}
    .excerpt{font-size:1.1rem}
    ul{padding-left:1.3rem;color:var(--muted)}
    li{margin-bottom:.45rem}
    .faq{margin-top:2rem;padding:1.25rem;border:1px solid var(--line);border-radius:16px;background:#f2efe9}
    .faq h2{margin-top:0}
    .actions{display:flex;gap:.75rem;flex-wrap:wrap;margin-top:1.75rem}
    .button{display:inline-flex;align-items:center;justify-content:center;min-height:46px;padding:.65rem 1rem;border-radius:10px;background:var(--accent);color:white;font-weight:750;text-decoration:none}
    .button.secondary{border:1px solid var(--line);background:white;color:var(--ink)}
    .post-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem;margin-top:1.5rem}
    .post-card h2{margin:.35rem 0;font-size:1.35rem}
    .post-card h2 a{color:var(--ink);text-decoration-thickness:.08em;text-underline-offset:.15em}
    footer{padding-block:1.5rem 2.5rem;border-top:1px solid var(--line);color:var(--muted)}
    @media(max-width:650px){.header-inner{align-items:flex-start;flex-direction:column;padding-block:1rem}nav{gap:.6rem 1rem}.post-list{grid-template-columns:1fr}}
    """


def document(title, description, canonical, body, schema=None, content_type="website"):
    schema_html = ""
    if schema:
        schema_json = json.dumps(schema, ensure_ascii=False).replace("<", "\\u003c")
        schema_html = f'<script type="application/ld+json">{schema_json}</script>'
    rendered = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{e(description)}">
  <meta name="theme-color" content="#f8f6f3">
  <meta property="og:type" content="{e(content_type)}">
  <meta property="og:site_name" content="Epson Centre">
  <meta property="og:title" content="{e(title)}">
  <meta property="og:description" content="{e(description)}">
  <meta property="og:url" content="{e(canonical)}">
  <meta name="twitter:card" content="summary">
  <link rel="canonical" href="{e(canonical)}">
  <title>{e(title)}</title>
  <style>{page_styles()}</style>
  {schema_html}
</head>
<body>
  <header class="site-header"><div class="header-inner">
    <a class="brand" href="/">Epson Centre<span>Epson printer support</span></a>
    <nav aria-label="Main navigation">
      <a href="/">Home</a><a href="/blog/">Blog</a>
      <a href="/#/programs">Adjustment Programs</a><a href="/#/troubleshooting">Troubleshooting</a>
    </nav>
  </div></header>
  <main>{body}</main>
  <footer>© 2026 Epson Centre · Printer support and troubleshooting resources</footer>
</body>
</html>
"""
    return re.sub(r"[ \t]+(?=\n)", "", rendered)


def render_section(section):
    paragraphs = "".join(f"<p>{e(paragraph)}</p>" for paragraph in section["paragraphs"])
    bullets = ""
    if section.get("bullets"):
        bullets = "<ul>" + "".join(f"<li>{e(item)}</li>" for item in section["bullets"]) + "</ul>"
    return f"<section><h2>{e(section['heading'])}</h2>{paragraphs}{bullets}</section>"


def generate():
    parsed = urlparse(ORIGIN)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("SITE_ORIGIN must be an HTTPS origin without a path, query, or fragment.")
    posts = load_posts()
    for post in posts:
        slug = post["slug"]
        if not re.fullmatch(r"[a-z0-9-]+", slug):
            raise ValueError(f"Invalid blog slug: {slug}")
        canonical = f"{ORIGIN}/blog/{slug}/"
        title = f"{post['title']} | Epson Centre"
        schema = {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": post["title"],
            "description": post["excerpt"],
            "datePublished": post["date"],
            "dateModified": date.today().isoformat(),
            "articleSection": post["category"],
            "mainEntityOfPage": canonical,
            "author": {"@type": "Organization", "name": "Epson Centre"},
            "publisher": {"@type": "Organization", "name": "Epson Centre"},
        }
        sections = "".join(render_section(section) for section in post["sections"])
        body = f"""
        <div class="breadcrumbs"><a href="/">Home</a> / <a href="/blog/">Blog</a> / {e(post['title'])}</div>
        <article>
          <div class="category">{e(post['category'])}</div>
          <h1>{e(post['title'])}</h1>
          <time class="date" datetime="{e(post['date'])}">{format_date(post['date'])}</time>
          <p class="excerpt">{e(post['excerpt'])}</p>
          {sections}
          <section class="faq"><h2>{e(post['faq']['question'])}</h2><p>{e(post['faq']['answer'])}</p></section>
          <div class="actions">
            <a class="button" href="/#/programs">Browse Epson programs</a>
            <a class="button secondary" href="/#/troubleshooting">Troubleshooting guides</a>
          </div>
        </article>
        """
        output_directory = ROOT / "blog" / slug
        output_directory.mkdir(parents=True, exist_ok=True)
        (output_directory / "index.html").write_text(
            document(title, post["excerpt"], canonical, body, schema, "article"),
            encoding="utf-8",
        )

    sorted_posts = sorted(posts, key=lambda post: post["date"], reverse=True)
    cards = "".join(
        f"""
        <article class="post-card">
          <div class="category">{e(post['category'])}</div>
          <h2><a href="/blog/{e(post['slug'])}/">{e(post['title'])}</a></h2>
          <time class="date" datetime="{e(post['date'])}">{format_date(post['date'])}</time>
          <p>{e(post['excerpt'])}</p>
          <a href="/blog/{e(post['slug'])}/">Read the guide</a>
        </article>
        """
        for post in sorted_posts
    )
    blog_body = f"""
      <div class="breadcrumbs"><a href="/">Home</a> / Blog</div>
      <div class="category">Epson Centre blog</div>
      <h1>Epson Printer Troubleshooting and Maintenance Guides</h1>
      <p class="excerpt">Practical Epson printer setup, print-quality, connectivity, and maintenance information. Check the exact printer model and current symptoms before following any service procedure.</p>
      <div class="post-list">{cards}</div>
    """
    blog_canonical = f"{ORIGIN}/blog/"
    (ROOT / "blog").mkdir(exist_ok=True)
    (ROOT / "blog" / "index.html").write_text(
        document(
            "Epson Printer Blog: Setup, Troubleshooting & Maintenance | Epson Centre",
            "Browse practical Epson printer guides for setup, Wi-Fi, print quality, paper-feed issues, service alerts, and waste ink counter questions.",
            blog_canonical,
            blog_body,
        ),
        encoding="utf-8",
    )

    lastmod = date.today().isoformat()
    locations = [(f"{ORIGIN}/", "1.0"), (blog_canonical, "0.8")]
    locations.extend((f"{ORIGIN}/blog/{post['slug']}/", "0.7") for post in posts)
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(
            f"  <url><loc>{e(location)}</loc><lastmod>{lastmod}</lastmod><priority>{priority}</priority></url>\n"
            for location, priority in locations
        )
        + "</urlset>\n"
    )
    (ROOT / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    (ROOT / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {ORIGIN}/sitemap.xml\n",
        encoding="utf-8",
    )
    print(f"Generated {len(posts)} crawlable blog pages, blog index, sitemap.xml, and robots.txt for {ORIGIN}.")


if __name__ == "__main__":
    generate()

from flask import Flask, render_template_string, request
import os
import hashlib
import json
import requests
import praw
import markdown
import re
import time
import httpx

app = Flask(__name__)
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\">
    <title>Reddit Class Summary Tool</title>
    <style>
        body {
            font-family: 'Segoe UI', sans-serif;
            background: #0f172a;
            color: #e5e7eb;
            max-width: 800px;
            margin: auto;
            padding: 2rem;
        }
        h1 {
            text-align: center;
            font-size: 2.5rem;
            color: #c084fc;
        }
        form {
            text-align: center;
            margin: 2rem 0;
        }
        input[type=\"text\"] {
            padding: 0.5rem;
            width: 60%;
            font-size: 1rem;
            border: none;
            border-radius: 0.5rem;
            background-color: #1e293b;
            color: #f9fafb;
        }
        button {
            padding: 0.5rem 1rem;
            font-size: 1rem;
            background: #9333ea;
            color: white;
            border: none;
            border-radius: 0.5rem;
            cursor: pointer;
            margin-left: 0.5rem;
        }
        .summary-box {
            background: #1e293b;
            padding: 1.5rem;
            border-radius: 1rem;
            box-shadow: 0 0 10px rgba(168, 85, 247, 0.3);
        }
        .meta {
            margin-top: 1rem;
            font-size: 1rem;
        }
        .tags span {
            background: #4c1d95;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            margin-right: 0.5rem;
            display: inline-block;
            color: #facc15;
        }
        .sources {
            margin-top: 2rem;
        }
        .sources ul {
            padding-left: 1.2rem;
        }
        .sources li {
            margin-bottom: 0.5rem;
        }
        .sources a {
            color: #93c5fd;
        }
    </style>
</head>
<body>
    <h1>🔮 Reddit Class Summary Tool</h1>
    <form method=\"POST\">
        <input type=\"text\" name=\"query\" placeholder=\"Enter course code (e.g. CS577)\" value=\"{{ query }}\" required>
        <button type=\"submit\">Summarize</button>
    </form>

    {% if summary %}
    <div class="summary-box">
        <h2>🧠 Summary for {{ query }}</h2>
        <div>{{ summary|safe }}</div>
        <div class="meta">
            {% if tags %}
                <div class="tags">🏷️ Tags:
                    {% for tag in tags %}
                        {% if "Tags:" not in tag %}
                            <span>{{ tag }}</span>
                        {% endif %}
                    {% endfor %}
                </div>
            {% endif %}
            {% if a_chance and "Estimated A Chance" not in a_chance %}
                <p>📊 Estimated A Chance: <strong>{{ a_chance }}</strong></p>
            {% endif %}
        </div>
    </div>
{% endif %}

    {% if sources %}
        <div class=\"sources\">
            <h3>🔗 Sources (Reddit Posts)</h3>
            <ul>
                {% for link in sources %}
                    <li><a href=\"{{ link }}\" target=\"_blank\">{{ link }}</a></li>
                {% endfor %}
            </ul>
        </div>
    {% endif %}
</body>
</html>
"""

def get_cache_key(data_type, input_data):
    return f"{data_type}_{hashlib.md5(str(input_data).encode()).hexdigest()}.json"

def optimize_search_query(raw_input):
    clean = raw_input.strip().upper()
    match = re.match(r"([A-Z]+)[\s\-]?(\d+)", clean)
    if not match:
        return f"site:reddit.com/r/UWMadison {clean}"
    dept, num = match.groups()
    variations = [f"{dept}{num}", f"{dept} {num}", f"{dept}-{num}"]
    return f"site:reddit.com/r/UWMadison " + " OR ".join(f'\"{v}\"' for v in variations)

def search_serpapi(query, max_results=25):
    cache_file = os.path.join(CACHE_DIR, get_cache_key("search", query))
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            return json.load(f)
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        return []
    params = {"engine": "google", "q": query, "api_key": api_key, "num": max_results}
    try:
        res = requests.get("https://serpapi.com/search", params=params, timeout=10)
        data = res.json()
        links = [r.get("link", "").split("?")[0] for r in data.get("organic_results", []) if "reddit.com/r/UWMadison" in r.get("link", "")]
        links = list(dict.fromkeys(links))[:max_results]
        with open(cache_file, 'w') as f:
            json.dump(links, f)
        return links
    except Exception as e:
        print("SerpAPI error:", e)
        return []

def fetch_reddit_posts_data(urls):
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT")
    if not all([client_id, client_secret, user_agent]):
        return []
    reddit = praw.Reddit(client_id=client_id, client_secret=client_secret, user_agent=user_agent)
    posts = []
    for url in urls:
        cache_file = os.path.join(CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + ".json")
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                posts.append(json.load(f))
            continue
        try:
            submission = reddit.submission(url=url)
            submission.comments.replace_more(limit=0)
            data = {
                "title": submission.title,
                "body": submission.selftext,
                "comments": [c.body for c in submission.comments[:3]],
                "url": url
            }
            posts.append(data)
            with open(cache_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Reddit fetch error: {e}")
            continue
    return posts

def generate_summary(posts, query):
    cache_file = os.path.join(CACHE_DIR, get_cache_key("summary", query + str([p['url'] for p in posts])))
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            return json.load(f)

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {"summary": "❌ Missing API Key", "tags": [], "a_chance": "N/A"}

    prompt = f"""Please write a structured summary with:
- Overall sentiment
- Difficulty and workload
- Student advice
- Professor mentions
- Estimated A chance (e.g., “Estimated A Chance: 70%”)
- Tags (e.g., “Tags: 🔥 Hard, 👨‍🏫 Great prof”)

Respond naturally but include those elements. Keep it concise."""

    for p in posts[:15]:
        prompt += f"\n- Title: {p['title']}\n  Body: {p['body'][:300]}\n"
        if p['comments']:
            prompt += f"  Comment: {p['comments'][0][:100]}\n"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-specdec",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }

    try:
        res = httpx.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=45)
        data = res.json()

        # Log response for debugging
        print("Groq raw response:", json.dumps(data, indent=2))

        choices = data.get("choices", [])
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise ValueError("Groq returned no content. Try simplifying the prompt or checking your key.")

        message = choices[0].get("message", {})
        content = message.get("content", "").strip()
        if not content:
            raise ValueError("Groq response contained no usable content.")

        # Flexible parsing: collect all non-tag/non-chance lines
        summary_lines, tags, a_chance = [], [], "N/A"
        for line in content.splitlines():
            lower = line.lower()
            if lower.startswith("tags"):
                tags = []
                raw_tags = line.split(":", 1)[1].split(',')
                for t in raw_tags:
                    clean = t.strip().strip('[]')
                    if clean.lower().startswith("tags:"):
                        clean = clean[5:].strip()
                    if clean:
                        tags.append(clean)
            elif "chance" in lower:
                a_chance = line.split(":", 1)[1].strip()
            else:
                summary_lines.append(line)

        summary = "\n".join(summary_lines).strip()
        if not summary:
            summary = "⚠️ Summary could not be parsed. The model may have returned unexpected formatting."

        markdown_summary = markdown.markdown(summary)
        result = {"summary": markdown_summary, "tags": tags, "a_chance": a_chance}
        with open(cache_file, 'w') as f:
            json.dump(result, f)
        return result

    except Exception as e:
        return {"summary": f"❌ Groq error: {e}", "tags": [], "a_chance": "N/A"}


@app.route("/", methods=["GET", "POST"])
def index():
    summary = ""
    tags = []
    a_chance = ""
    sources = []
    query = ""
    if request.method == "POST":
        query = request.form["query"]
        search_query = optimize_search_query(query)
        urls = search_serpapi(search_query, max_results=25)
        sources = urls
        post_data = fetch_reddit_posts_data(urls)
        result = generate_summary(post_data, query)
        summary = result.get("summary", "")
        tags = result.get("tags", [])
        a_chance = result.get("a_chance", "")
    return render_template_string(HTML_TEMPLATE, query=query, summary=summary, tags=tags, a_chance=a_chance, sources=sources)

if __name__ == "__main__":
    app.run(debug=True)

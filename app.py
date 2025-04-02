from flask import Flask, render_template_string, request
import requests
import os
import time
import markdown
import sys
import praw
import concurrent.futures
import hashlib
import json

app = Flask(__name__)
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Reddit Class Summary Tool</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background-color: #f9fafb;
            padding: 2rem;
            max-width: 900px;
            margin: auto;
            color: #111;
        }
        h1 {
            font-size: 2rem;
            margin-bottom: 1rem;
        }
        input[type=text] {
            padding: 0.5rem;
            width: 60%;
            font-size: 1rem;
            margin-right: 0.5rem;
        }
        button {
            padding: 0.5rem 1rem;
            font-size: 1rem;
            background-color: #4f46e5;
            color: white;
            border: none;
            border-radius: 4px;
        }
        .post {
            background: white;
            border: 1px solid #e5e7eb;
            padding: 1rem;
            margin-top: 1rem;
            border-radius: 8px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        }
        .post h3 {
            margin-top: 0;
            font-size: 1.1rem;
            color: #1f2937;
        }
        .post .url a {
            font-size: 0.9rem;
            color: #3b82f6;
        }
        .summary {
            margin-top: 0.5rem;
        }
    </style>
</head>
<body>
    <h1>Reddit Class Summary Tool</h1>
    <form method="post">
        <input type="text" name="query" placeholder="Enter course code, e.g. CS577" required>
        <button type="submit">Search & Summarize</button>
    </form>
    <div class="results">
        {% if main_summary %}
            <h2>🧠 Main Insights from Reddit about {{ query }}</h2>
            <div class="post">
                <div class="summary">{{ main_summary|safe }}</div>
            </div>
        {% endif %}
        {% if results %}
            <h2>🔍 Per-post Summaries</h2>
            {% for r in results %}
                <div class="post">
                    <h3>{{ r.title }}</h3>
                    <div class="summary">{{ r.summary|safe }}</div>
                    <p class="url">🔗 <a href="{{ r.url }}" target="_blank">Original Post</a></p>
                </div>
            {% endfor %}
        {% endif %}
    </div>
</body>
</html>
"""

def search_serpapi(query, max_results=10):
    api_key = os.getenv("SERPAPI_KEY")
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": max_results,
    }
    url = "https://serpapi.com/search"
    res = requests.get(url, params=params)
    if res.status_code != 200:
        return []
    data = res.json()
    links = []
    for result in data.get("organic_results", []):
        link = result.get("link", "")
        if "reddit.com/r/UWMadison" in link:
            links.append(link.split("?")[0])
    return links[:max_results]

def fetch_reddit_post_data(url):
    cache_file = os.path.join(CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + ".json")
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            return json.load(f)

    reddit = praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent=os.getenv("REDDIT_USER_AGENT")
    )
    try:
        submission = reddit.submission(url=url)
        title = submission.title
        body = submission.selftext
        submission.comments.replace_more(limit=0)
        comments = [comment.body for comment in submission.comments[:5]]
        result = {"title": title, "body": body, "comments": comments, "url": url}
        with open(cache_file, 'w') as f:
            json.dump(result, f)
        return result
    except Exception as e:
        return {"title": "", "body": "", "comments": [], "url": url}

def build_prompt(title, body, comments):
    content = f"Reddit Post Title: {title}\n\nPost Body:\n{body}\n\nTop Comments:\n"
    for i, c in enumerate(comments):
        content += f"{i+1}. {c}\n"
    return ("Summarize the following Reddit discussion. Highlight advice, experience, or consensus about the course:\n\n" + content)

def summarize_with_groq(prompt):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "❌ GROQ_API_KEY not set."
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "gemma2-9b-it",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    try:
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content']
        else:
            return "❌ Groq API error."
    except Exception as e:
        return f"❌ Groq exception: {e}"

def rerank_posts_with_groq(posts, class_query):
    prompt = (
        f"You're helping a student decide whether to take the course {class_query}.\n"
        f"Below are Reddit posts about this course. Select and rank the 3 most useful ones based on relevance, clarity, and insight.\n"
        f"Return a list of URLs and brief reasons for each.\n\n"
    )
    for i, p in enumerate(posts):
        prompt += f"[{i+1}] Title: {p['title']}\nPost: {p['body'][:500]}\nURL: {p['url']}\n\n"

    response = summarize_with_groq(prompt)
    top_urls = []
    for p in posts:
        if p['url'] in response:
            top_urls.append(p['url'])
        if len(top_urls) == 3:
            break
    return top_urls

def generate_overall_summary(posts, class_query):
    mega_prompt = (
        f"You're summarizing opinions from Reddit about the course {class_query}.\n"
        f"Give 5–7 main takeaways. Include key advice, typical struggles, and recommended professors.\n\n"
    )
    for p in posts:
        mega_prompt += f"Title: {p['title']}\nBody: {p['body'][:500]}\nComments:\n"
        for c in p['comments'][:3]:
            mega_prompt += f"- {c}\n"
        mega_prompt += "\n"
    return summarize_with_groq(mega_prompt)

@app.route('/', methods=['GET', 'POST'])
def index():
    results = []
    query = ""
    main_summary = ""
    if request.method == 'POST':
        class_query = request.form['query']
        query = f"site:reddit.com/r/UWMadison {class_query}"
        links = search_serpapi(query)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            fetched_data = list(executor.map(fetch_reddit_post_data, links))

        main_summary_raw = generate_overall_summary(fetched_data, class_query)
        main_summary = markdown.markdown(main_summary_raw)

        top_urls = rerank_posts_with_groq(fetched_data, class_query)
        top_posts = [p for p in fetched_data if p['url'] in top_urls]

        for data in top_posts:
            title = data['title']
            body = data['body']
            comments = data['comments']
            url = data['url']
            if title:
                prompt = build_prompt(title, body, comments)
                summary = summarize_with_groq(prompt)
                html_summary = markdown.markdown(summary)
                results.append({"title": title, "summary": html_summary, "url": url})
                time.sleep(1.5)
    return render_template_string(HTML_TEMPLATE, results=results, query=query, main_summary=main_summary)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

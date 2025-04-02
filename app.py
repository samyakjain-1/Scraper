from flask import Flask, render_template_string, request
import requests
from bs4 import BeautifulSoup
import os
import time
import markdown
import sys
import praw

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reddit Class Summary Tool</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #f0f2f5;
            padding: 40px;
            margin: 0;
        }
        h1 {
            text-align: center;
            font-weight: 700;
            font-size: 2.5rem;
            color: #333;
        }
        form {
            max-width: 600px;
            margin: 20px auto;
            display: flex;
            gap: 10px;
        }
        input[type="text"] {
            flex: 1;
            padding: 12px;
            font-size: 1rem;
            border: 1px solid #ccc;
            border-radius: 8px;
        }
        button {
            background-color: #4f46e5;
            color: white;
            padding: 12px 20px;
            border: none;
            border-radius: 8px;
            font-size: 1rem;
            cursor: pointer;
        }
        button:hover {
            background-color: #4338ca;
        }
        .results {
            max-width: 800px;
            margin: 40px auto;
        }
        .post {
            background-color: #ffffff;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 10px rgba(0,0,0,0.05);
            margin-bottom: 30px;
        }
        .post h3 {
            margin-top: 0;
            color: #1f2937;
        }
        .url a {
            text-decoration: none;
            color: #4f46e5;
        }
        .url a:hover {
            text-decoration: underline;
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
        {% if results %}
            <h2>Results for "{{ query }}"</h2>
            {% for r in results %}
                <div class="post">
                    <h3>{{ r.title }}</h3>
                    <div>{{ r.summary|safe }}</div>
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
        print("❌ SerpAPI error:", res.status_code, res.text)
        return []

    data = res.json()
    links = []
    for result in data.get("organic_results", []):
        link = result.get("link", "")
        if "reddit.com/r/UWMadison" in link:
            links.append(link.split("?")[0])

    print("✅ SerpAPI returned the following Reddit links:")
    for l in links:
        print(" -", l)
    sys.stdout.flush()

    return links[:max_results]

def fetch_reddit_post_data(post_url):
    reddit = praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent=os.getenv("REDDIT_USER_AGENT")
    )

    try:
        submission = reddit.submission(url=post_url)
        title = submission.title
        body = submission.selftext
        submission.comments.replace_more(limit=0)
        comments = [comment.body for comment in submission.comments[:5]]
        return title, body, comments
    except Exception as e:
        print(f"❌ Error fetching post via PRAW: {e}")
        return '', '', []

def build_prompt(title, body, comments):
    content = f"Reddit Post Title: {title}\n\nPost Body:\n{body}\n\nTop Comments:\n"
    for i, c in enumerate(comments):
        content += f"{i+1}. {c}\n"
    return ("Summarize the following Reddit discussion. Highlight advice, experience, or consensus about the course:\n\n" + content)

def summarize_with_groq(prompt):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("❌ GROQ_API_KEY is missing!")
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
            summary = res.json()['choices'][0]['message']['content']
            print("✅ Summary received.")
            sys.stdout.flush()
            return summary
        else:
            print("❌ Groq API error:", res.status_code, res.text)
            sys.stdout.flush()
            return "❌ Groq API error."
    except Exception as e:
        print("❌ Exception during Groq summary:", e)
        sys.stdout.flush()
        return "❌ Exception occurred."

@app.route('/', methods=['GET', 'POST'])
def index():
    results = []
    query = ""
    if request.method == 'POST':
        class_query = request.form['query']
        query = f"site:reddit.com/r/UWMadison {class_query}"

        links = search_serpapi(query)
        print("🔎 Searching for:", class_query)
        print("✅ Reddit links received:", links)
        sys.stdout.flush()

        for url in links:
            title, body, comments = fetch_reddit_post_data(url)
            if title:
                prompt = build_prompt(title, body, comments)
                print(f"🧠 Prompt for {title[:50]}...")
                sys.stdout.flush()

                summary = summarize_with_groq(prompt)
                html_summary = markdown.markdown(summary)
                results.append({"title": title, "summary": html_summary, "url": url})
                time.sleep(1.5)
            else:
                print(f"⚠️ Skipped post: {url}")
                sys.stdout.flush()
    return render_template_string(HTML_TEMPLATE, results=results, query=query)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

from flask import Flask, render_template_string, request
import requests
from bs4 import BeautifulSoup
import os
import time
import markdown
import sys

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Reddit Class Summary</title>
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
</body>
</html>
"""

def search_serpapi(query, max_results=10):
    import os
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
    sys.stdout.flush()  # <--- Add this here

    return links[:max_results]



def fetch_reddit_json(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url + ".json", headers=headers)
        print(f"📥 Fetching JSON: {url}.json | Status: {response.status_code}")
        sys.stdout.flush()
        if response.status_code == 200:
            return response.json()
        else:
            print("❌ Reddit .json fetch failed")
    except Exception as e:
        print(f"❌ Exception fetching Reddit post: {e}")
        sys.stdout.flush()
    return None

def extract_post_content(json_data):
    try:
        post = json_data[0]['data']['children'][0]['data']
        comments = json_data[1]['data']['children']
        top_comments = [c['data']['body'] for c in comments if c['kind'] == 't1']
        return post.get('title', ''), post.get('selftext', ''), top_comments[:5]
    except:
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
        links = search_serpapi(f"site:reddit.com/r/UWMadison {class_query}")
        print(f"🔎 Searching for: {class_query}")
        print("✅ Reddit links received:", links)
        sys.stdout.flush()
        for url in links:
            data = fetch_reddit_json(url)
            if data:
                title, body, comments = extract_post_content(data)
                prompt = build_prompt(title, body, comments)
                summary = summarize_with_groq(prompt)
                html_summary = markdown.markdown(summary)
                results.append({"title": title, "summary": html_summary, "url": url})
                time.sleep(1.5)
    return render_template_string(HTML_TEMPLATE, results=results, query=query)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


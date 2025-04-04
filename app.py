from flask import Flask, render_template_string, request
import requests
import os
import time
import markdown
import sys
import praw
import asyncio
import httpx
import concurrent.futures
import hashlib
import json
import random

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
            cursor: pointer;
        }
        button:hover {
            background-color: #4338ca;
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
        #loading {
            display: none;
            margin-top: 1rem;
            font-size: 1rem;
            color: #4b5563;
        }
        .show-more-container {
            text-align: center;
            margin-top: 1.5rem;
            margin-bottom: 2rem;
        }
        .show-more-btn {
            background-color: #f3f4f6;
            color: #4b5563;
            border: 1px solid #d1d5db;
        }
        .show-more-btn:hover {
            background-color: #e5e7eb;
        }
        .additional-posts {
            display: none;
        }
        .post-count {
            margin-top: 1rem;
            color: #6b7280;
            font-size: 0.9rem;
        }
        .post-controls {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 0.5rem;
        }
    </style>
    <script>
        function showLoading() {
            document.getElementById('loading').style.display = 'block';
        }
        
        function toggleAdditionalPosts() {
            const additionalPosts = document.getElementById('additional-posts');
            const showMoreBtn = document.getElementById('show-more-btn');
            
            if (additionalPosts.style.display === 'none' || !additionalPosts.style.display) {
                additionalPosts.style.display = 'block';
                showMoreBtn.textContent = 'Show Less';
            } else {
                additionalPosts.style.display = 'none';
                showMoreBtn.textContent = 'Show More Posts';
                // Scroll back to main results
                document.getElementById('main-results').scrollIntoView({behavior: 'smooth'});
            }
        }
    </script>
</head>
<body>
    <h1>Reddit Class Summary Tool</h1>
    <form method="post" onsubmit="showLoading()">
        <input type="text" name="query" placeholder="Enter course code, e.g. CS577" required value="{{ query }}">
        <input type="hidden" name="max_posts" value="{{ max_posts }}">
        <button type="submit">Search & Summarize</button>
    </form>
    <div id="loading">🔄 Loading and summarizing Reddit posts, please wait...</div>
    <div class="results" id="main-results">
        {% if main_summary %}
            <h2>🧠 Main Insights from Reddit about {{ query }}</h2>
            <div class="post">
                <div class="summary">{{ main_summary|safe }}</div>
            </div>
        {% endif %}
        
        {% if results %}
            <h2>🔍 Per-post Summaries</h2>
            <div class="post-count">Showing {{ results_initial|length }} of {{ results|length }} posts</div>
            
            {% for r in results_initial %}
                <div class="post">
                    <h3>{{ r.title }}</h3>
                    <div class="summary">{{ r.summary|safe }}</div>
                    <div class="post-controls">
                        <p class="url">🔗 <a href="{{ r.url }}" target="_blank">Original Post</a></p>
                    </div>
                </div>
            {% endfor %}
            
            {% if results|length > results_initial|length %}
                <div class="show-more-container">
                    <button id="show-more-btn" class="show-more-btn" onclick="toggleAdditionalPosts()">Show More Posts</button>
                </div>
                
                <div id="additional-posts" class="additional-posts">
                    {% for r in results_additional %}
                        <div class="post">
                            <h3>{{ r.title }}</h3>
                            <div class="summary">{{ r.summary|safe }}</div>
                            <div class="post-controls">
                                <p class="url">🔗 <a href="{{ r.url }}" target="_blank">Original Post</a></p>
                            </div>
                        </div>
                    {% endfor %}
                </div>
            {% endif %}
        {% endif %}
    </div>
</body>
</html>
"""

MODEL_POOL = [
    "gemma2-9b-it", "llama-3.1-8b-instant", "llama-3.2-1b-preview", "llama-3.2-3b-preview",
    "llama-3.2-11b-vision-preview", "llama-3.2-90b-vision-preview", 
    "llama-3.3-70b-specdec", "llama-guard-3-8b", "mistral-saba-24b",
    "deepseek-r1-distill-llama-70b", "deepseek-r1-distill-qwen-32b", "qwen-2.5-32b",
    "qwen-2.5-coder-32b", "qwen-qwq-32b"
]

# Use smaller models by default to reduce rate limiting issues
PREFERRED_MODELS = ["llama-3.1-8b-instant", "llama-3.2-1b-preview", "llama-3.2-3b-preview"]

def get_next_model(preferred=True):
    """Get a model from either the preferred list or full pool"""
    if preferred:
        return random.choice(PREFERRED_MODELS)
    return random.choice(MODEL_POOL)

def search_serpapi(query, max_results=10):
    """Search for Reddit posts about the course using SerpAPI with caching"""
    cache_file = os.path.join(CACHE_DIR, f"search_{hashlib.md5(query.encode()).hexdigest()}.json")
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            return json.load(f)
            
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        print("Warning: SERPAPI_KEY environment variable not set")
        return []
        
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": max_results,
    }
    url = "https://serpapi.com/search"
    
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code != 200:
            print(f"SerpAPI error: {res.status_code}")
            return []
            
        data = res.json()
        links = []
        for result in data.get("organic_results", []):
            link = result.get("link", "")
            if "reddit.com/r/UWMadison" in link:
                links.append(link.split("?")[0])
        
        links = links[:max_results]
        with open(cache_file, 'w') as f:
            json.dump(links, f)
        return links
    except Exception as e:
        print(f"SerpAPI exception: {e}")
        return []

def fetch_reddit_post_data(url):
    """Fetch data from Reddit posts with caching"""
    cache_file = os.path.join(CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + ".json")
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            return json.load(f)

    # Check if Reddit credentials are available
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT")
    
    if not all([client_id, client_secret, user_agent]):
        print("Warning: Reddit API credentials not fully set")
        return {"title": "", "body": "", "comments": [], "url": url}

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent
        )
        
        submission = reddit.submission(url=url)
        title = submission.title
        body = submission.selftext
        submission.comments.replace_more(limit=0)
        comments = [comment.body for comment in submission.comments[:3]]  # Reduced from 5 to 3
        result = {"title": title, "body": body, "comments": comments, "url": url}
        
        with open(cache_file, 'w') as f:
            json.dump(result, f)
        return result
    except Exception as e:
        print(f"Reddit API exception: {e}")
        return {"title": "", "body": "", "comments": [], "url": url}

def get_cache_key(data_type, input_data):
    """Generate cache key based on input data type and content"""
    if isinstance(input_data, str):
        hash_input = input_data
    else:
        hash_input = str(input_data)
    return f"{data_type}_{hashlib.md5(hash_input.encode()).hexdigest()}.json"

async def summarize_with_groq_async(prompt, model, max_retries=3):
    """Make API call to Groq with retry logic and caching"""
    # Check cache first
    cache_file = os.path.join(CACHE_DIR, get_cache_key("summary", prompt + model))
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
                return cached_data.get("content")
        except (json.JSONDecodeError, KeyError):
            # Invalid cache, continue with API call
            pass
    
    # Check for API key
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "❌ GROQ_API_KEY environment variable not set"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    
    base_delay = 2  # seconds
    
    async with httpx.AsyncClient() as client:
        for attempt in range(max_retries):
            try:
                res = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions", 
                    headers=headers, 
                    json=data,
                    timeout=30.0
                )
                
                if res.status_code == 200:
                    try:
                        content = res.json()['choices'][0]['message']['content']
                        with open(cache_file, 'w') as f:
                            json.dump({"content": content}, f)
                        return content
                    except (KeyError, IndexError, json.JSONDecodeError) as e:
                        print(f"Error parsing Groq response: {e}")
                        return "❌ Error parsing Groq response"
                        
                elif res.status_code == 429:  # Rate limit error
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    print(f"Rate limited. Retrying in {delay:.2f} seconds...")
                    await asyncio.sleep(delay)
                else:
                    print(f"API error {res.status_code}: {res.text}")
                    return f"❌ Groq API error: {res.status_code}"
                    
            except Exception as e:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"Exception: {e}. Retrying in {delay:.2f} seconds...")
                await asyncio.sleep(delay)
                
        return "❌ Failed after multiple retries due to rate limiting"

def summarize_with_groq(prompt, model=None):
    """Synchronous wrapper for the async Groq API call"""
    if model is None:
        model = get_next_model()
    return asyncio.run(summarize_with_groq_async(prompt, model))

def select_relevant_posts(posts, class_query, max_posts=8):  # Increased from 3 to 8
    """Select the most relevant posts for summarization"""
    # Remove empty posts first
    posts = [p for p in posts if p.get('title')]
    if not posts:
        return []
        
    # If we have very few posts, just return them all
    if len(posts) <= max_posts:
        return posts
        
    # Check cache
    cache_key = get_cache_key("relevant_posts", class_query + str([p['url'] for p in posts]))
    cache_file = os.path.join(CACHE_DIR, cache_key)
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cached_posts = json.load(f)
                # Validate cache data
                if isinstance(cached_posts, list) and len(cached_posts) > 0:
                    return cached_posts[:max_posts]
        except Exception as e:
            print(f"Cache read error: {e}")
    
    # Prepare a condensed version of posts for the prompt
    condensed_posts = []
    for i, p in enumerate(posts):
        # Limit text lengths to keep prompt size reasonable
        title = p['title'][:100]
        body = p['body'][:300] if p['body'] else "No content"
        condensed_posts.append({
            "index": i, 
            "title": title,
            "body": body,
            "url": p['url']
        })
    
    # Create a prompt that asks for direct selection rather than explanation
    prompt = (
        f"From these Reddit posts about the course {class_query}, identify the {max_posts} most useful posts.\n"
        f"Return ONLY the indices of the selected posts, separated by commas (e.g., '1,4,7').\n\n"
    )
    
    for post in condensed_posts:
        prompt += f"[{post['index']}] Title: {post['title']}\nExcerpt: {post['body']}\n\n"
    
    small_model = get_next_model(preferred=True)
    response = summarize_with_groq(prompt, small_model)
    
    # Parse the response to get indices
    selected_indices = []
    for word in response.replace(',', ' ').split():
        try:
            idx = int(word.strip())
            if 0 <= idx < len(posts):
                selected_indices.append(idx)
        except ValueError:
            continue
    
    # Ensure we have the requested number of posts if possible
    while len(selected_indices) < max_posts and len(selected_indices) < len(posts):
        for i in range(len(posts)):
            if i not in selected_indices:
                selected_indices.append(i)
                break
    
    # Limit to requested max
    selected_indices = selected_indices[:max_posts]
    selected_posts = [posts[i] for i in selected_indices]
    
    # Cache the results
    with open(cache_file, 'w') as f:
        json.dump(selected_posts, f)
    
    return selected_posts

def generate_overall_summary(posts, class_query):
    """Generate an overall summary from multiple Reddit posts"""
    cache_file = os.path.join(CACHE_DIR, get_cache_key("main_summary", class_query + str([p['url'] for p in posts[:3]])))
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                return json.load(f).get("content")
        except Exception:
            pass
    
    # Use just the top 3 posts for the main summary to keep it focused
    top_posts = posts[:3]
    
    # Create a more concise prompt
    mega_prompt = (
        f"Summarize opinions from Reddit about course {class_query}.\n"
        f"Give 5 key takeaways with bullet points. Include key advice and professor recommendations if mentioned.\n\n"
    )
    
    # Limit the amount of content to keep prompt size reasonable
    for p in top_posts:
        mega_prompt += f"Post: {p['title']}\n"
        if p['body']:
            mega_prompt += f"Excerpt: {p['body'][:200]}...\n"
        
        # Include just 1-2 comments to keep prompt size small
        if p['comments']:
            mega_prompt += "Comment: " + p['comments'][0][:100] + "\n"
            if len(p['comments']) > 1:
                mega_prompt += "Comment: " + p['comments'][1][:100] + "\n"
        
        mega_prompt += "\n"
    
    model = get_next_model(preferred=False)  # Use a more capable model for the main summary
    content = summarize_with_groq(mega_prompt, model)
    
    # Cache the result
    with open(cache_file, 'w') as f:
        json.dump({"content": content}, f)
    
    return content

async def batch_summarize_posts(posts, class_query):
    """Summarize posts in efficient batches to minimize API calls"""
    # Create a list to store all summaries
    all_summaries = []
    
    # Process posts in batches of 3
    batch_size = 3
    for i in range(0, len(posts), batch_size):
        batch_posts = posts[i:i+batch_size]
        
        # Check if we have all summaries cached
        all_cached = True
        cached_summaries = []
        
        for post in batch_posts:
            cache_file = os.path.join(CACHE_DIR, get_cache_key("post_summary", post['url'] + class_query))
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r') as f:
                        cached_data = json.load(f)
                        cached_summaries.append({
                            "title": post['title'],
                            "summary": markdown.markdown(cached_data.get("content", "No summary available.")),
                            "url": post['url']
                        })
                except Exception:
                    all_cached = False
                    break
            else:
                all_cached = False
                break
        
        if all_cached:
            all_summaries.extend(cached_summaries)
            continue
        
        # Create a batch prompt that combines posts in this batch
        batch_prompt = (
            f"For each of these Reddit posts about {class_query}, provide a 2-3 sentence summary "
            f"highlighting key insights.\n\n"
        )
        
        for j, post in enumerate(batch_posts):
            batch_prompt += (
                f"POST {j+1}: {post['title']}\n"
                f"Content: {post['body'][:300]}\n"
                f"Comments: {'; '.join([c[:100] for c in post['comments'][:2]])}\n\n"
            )
        
        batch_prompt += (
            f"Format your response as:\n"
            f"POST 1: [2-3 sentence summary]\n"
            f"POST 2: [2-3 sentence summary]\n"
            f"POST 3: [2-3 sentence summary]"
        )
        
        model = get_next_model(preferred=True)  # Use smaller models for batch summaries
        result = await summarize_with_groq_async(batch_prompt, model)
        
        # Parse the batch results
        summaries = []
        current_post = None
        current_text = ""
        
        for line in result.split('\n'):
            if line.startswith('POST '):
                # Save previous post if exists
                if current_post is not None:
                    summaries.append({"post_index": current_post, "summary": current_text.strip()})
                
                # Start new post
                try:
                    current_post = int(line.split(' ')[1].replace(':', '')) - 1
                    current_text = line.split(':', 1)[1] if ':' in line else ""
                except:
                    current_post = len(summaries)
                    current_text = line
            else:
                current_text += " " + line
        
        # Add the last post
        if current_post is not None:
            summaries.append({"post_index": current_post, "summary": current_text.strip()})
        
        # Match summaries with posts and cache individual summaries
        for j, post in enumerate(batch_posts):
            summary_text = "No summary available."
            for s in summaries:
                if s["post_index"] == j:
                    summary_text = s["summary"]
                    # Cache individual summary
                    cache_file = os.path.join(CACHE_DIR, get_cache_key("post_summary", post['url'] + class_query))
                    with open(cache_file, 'w') as f:
                        json.dump({"content": summary_text}, f)
                    break
            
            all_summaries.append({
                "title": post['title'],
                "summary": markdown.markdown(summary_text),
                "url": post['url']
            })
    
    return all_summaries

@app.route('/', methods=['GET', 'POST'])
def index():
    results = []
    results_initial = []
    results_additional = []
    query = ""
    main_summary = ""
    max_posts = 8  # Default number of posts to fetch
    
    if request.method == 'POST':
        class_query = request.form['query']
        query = class_query  # Store original query for display
        
        # Get user preference for max posts (if provided)
        if 'max_posts' in request.form:
            try:
                max_posts = int(request.form['max_posts'])
            except ValueError:
                max_posts = 8
        
        # Construct search query
        search_query = f"site:reddit.com/r/UWMadison {class_query}"
        
        # Get cached results or search
        links = search_serpapi(search_query, max_results=15)  # Increased from 10 to 15
        
        # Fetch all posts in parallel
        with concurrent.futures.ThreadPoolExecutor() as executor:
            fetched_data = list(filter(lambda x: x["title"], executor.map(fetch_reddit_post_data, links)))
        
        if not fetched_data:
            return render_template_string(HTML_TEMPLATE, 
                                         results=[],
                                         results_initial=[],
                                         results_additional=[],
                                         query=class_query,
                                         max_posts=max_posts,
                                         main_summary="No Reddit posts found for this course.")
        
        # Select relevant posts
        top_posts = select_relevant_posts(fetched_data, class_query, max_posts=max_posts)
        
        # Generate main summary asynchronously using top 3 posts
        main_summary_raw = generate_overall_summary(top_posts, class_query)
        main_summary = markdown.markdown(main_summary_raw)
        
        # Generate per-post summaries efficiently
        results = asyncio.run(batch_summarize_posts(top_posts, class_query))
        
        # Split results for initial display and "show more"
        initial_count = min(3, len(results))  # Show first 3 by default
        results_initial = results[:initial_count]
        results_additional = results[initial_count:]
    
    return render_template_string(HTML_TEMPLATE, 
                                 results=results, 
                                 results_initial=results_initial,
                                 results_additional=results_additional,
                                 query=query,
                                 max_posts=max_posts,
                                 main_summary=main_summary)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
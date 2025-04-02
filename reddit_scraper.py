import requests
from bs4 import BeautifulSoup
import time
import os
import json

# 1. Bing Search for Reddit Posts
def search_bing(query, max_results=5):
    headers = {'User-Agent': 'Mozilla/5.0'}
    search_url = f"https://www.bing.com/search?q={query}"
    response = requests.get(search_url, headers=headers)

    if response.status_code != 200:
        print(f"❌ Bing search failed with status {response.status_code}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    results = []

    for li in soup.find_all('li', {'class': 'b_algo'}):
        a_tag = li.find('a')
        if a_tag and 'reddit.com/r/UWMadison/comments' in a_tag['href']:
            results.append(a_tag['href'].split('?')[0])

    return list(set(results))[:max_results]

# 2. Fetch Reddit JSON
def fetch_reddit_json(url):
    json_url = url + ".json"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(json_url, headers=headers)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"❌ Error fetching JSON from {url}: {e}")
    return None

# 3. Extract Post + Comments
def extract_post_content(json_data):
    try:
        post_data = json_data[0]["data"]["children"][0]["data"]
        title = post_data.get("title", "")
        selftext = post_data.get("selftext", "")
        comments = json_data[1]["data"]["children"]
        top_comments = [c["data"]["body"] for c in comments if c["kind"] == "t1"]
        return title, selftext, top_comments
    except Exception as e:
        print("❌ Error extracting content:", e)
        return "", "", []

# 4. Build Prompt for Groq
def build_prompt(title, body, comments):
    content = f"Reddit Post Title: {title}\n\nPost Body:\n{body}\n\nTop Comments:\n"
    for i, comment in enumerate(comments[:5], 1):
        content += f"{i}. {comment}\n"

    return (
        "Summarize the following Reddit discussion. Highlight advice, experience, or consensus about the course:\n\n"
        + content
    )

# 5. Call Groq API
def summarize_with_groq(prompt, groq_api_key):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {groq_api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            print(f"❌ Groq API error {response.status_code}: {response.text}")
    except Exception as e:
        print(f"❌ Exception calling Groq API: {e}")
    return "Error summarizing."

# 6. Main
def main():
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        print("❌ Please set the GROQ_API_KEY environment variable.")
        return

    class_query = input("Enter class name or number (e.g., CS577): ").strip()
    if not class_query:
        print("❌ Class query can't be empty.")
        return

    query = f'site:reddit.com/r/UWMadison {class_query}'
    print(f"\n🔍 Searching for: {query}")

    reddit_links = search_bing(query)
    print(f"🔗 Found {len(reddit_links)} Reddit links")

    if not reddit_links:
        print("❌ No Reddit posts found.")
        return

    for url in reddit_links:
        print(f"\n🔗 Fetching: {url}")
        data = fetch_reddit_json(url)
        if data:
            title, body, comments = extract_post_content(data)
            prompt = build_prompt(title, body, comments)
            summary = summarize_with_groq(prompt, groq_api_key)

            print(f"\n📌 Summary for: {title}")
            print(summary)
            print(f"\n🔗 Original Post: {url}")
            print("-" * 80)
            time.sleep(2)
        else:
            print("❌ Skipping post due to JSON error.")

if __name__ == "__main__":
    main()


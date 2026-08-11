import base64
import json
import os
import time
import urllib.error
import urllib.request

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]
FILE_PATH = "data/football_trials.json"

API = f"https://api.github.com/repos/{REPOSITORY}"


def github_request(method, url, payload=None):
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "FOOTURA-Trials-Bot",
    }

    data = None

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read().decode("utf-8")
        return json.loads(content) if content else {}


with open(FILE_PATH, "rb") as file:
    database = file.read()

print(f"Database prepared: {len(database):,} bytes")

encoded_database = base64.b64encode(database).decode("ascii")


for attempt in range(1, 6):
    print(f"GitHub database update: attempt {attempt}/5")

    try:
        # Get the current version of the file.
        try:
            current = github_request(
                "GET",
                f"{API}/contents/{FILE_PATH}?ref=main",
            )
            current_sha = current["sha"]
        except urllib.error.HTTPError as error:
            if error.code == 404:
                current_sha = None
            else:
                raise

        payload = {
            "message": "Update football trials database",
            "content": encoded_database,
            "branch": "main",
        }

        if current_sha:
            payload["sha"] = current_sha

        result = github_request(
            "PUT",
            f"{API}/contents/{FILE_PATH}",
            payload,
        )

        commit = result.get("commit", {})
        commit_sha = commit.get("sha", "unknown")

        print("Database successfully updated.")
        print(f"Commit: {commit_sha}")
        break

    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")

        print(f"GitHub API HTTP {error.code}")
        print(body)

        if attempt == 5:
            raise

        time.sleep(3)

else:
    raise RuntimeError("Database update failed after 5 attempts.")

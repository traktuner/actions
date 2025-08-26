import requests
import json
import os
from yaml import safe_load
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# GitHub API URL for creating an issue (Repo dynamisch aus Umgebungsvariable)
github_repo = os.getenv("GITHUB_REPOSITORY", "traktuner/actions")
issue_url = f"https://api.github.com/repos/{github_repo}/issues"

# Load application configurations from YAML file
with open("applications.yaml", "r") as f:
    applications = safe_load(f)

def build_session():
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "proton-version-check/1.0"})
    return session

def fetch_version_info(session, url):
    response = session.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    # Extract version information
    version_list = [item for item in data.get("Releases", []) if "Version" in item]
    version_data = version_list[0] if len(version_list) > 0 else {}
    parsed_data = parse_json(version_data, ["Version"])

    # Extract download URL
    download_url = extract_download_url(version_data)

    return parsed_data, download_url

def extract_download_url(release):
    if isinstance(release.get("File"), list):
        return release["File"][0].get("Url", "") if release["File"] else None
    elif isinstance(release.get("File"), dict):
        return release["File"].get("Url", "")
    return None

def read_last_version(file_path):
    try:
        with open(file_path, "r") as file:
            data = file.read()
            if data:
                return json.loads(data)
            else:
                return None
    except FileNotFoundError:
        return None

def write_current_version(file_path, version):
    with open(file_path, "w") as file:
        json.dump(version, file)

def issue_exists(session, token, title):
    url = f"https://api.github.com/repos/{github_repo}/issues"
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    try:
        resp = session.get(url, headers=headers, params={"state": "open", "per_page": 50}, timeout=15)
        if resp.status_code != 200:
            return False
        return any(i.get("title") == title for i in resp.json())
    except Exception:
        return False

def create_github_issue(session, token, app_name, new_version, download_url):
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    title = f'New version detected for {app_name}: {new_version}'
    if issue_exists(session, token, title):
        print(f"Issue bereits vorhanden: {title}")
        return
    data = {
        'title': title,
        'body': f'New Version of {app_name} detected: **{new_version}**\n\nDownload URL: {download_url}'
    }
    response = session.post(issue_url, headers=headers, json=data, timeout=15)

    if response.status_code == 201:
        print(f"GitHub issue created successfully for {app_name}.")
    else:
        print(f"Failed to create GitHub issue for {app_name}. Status Code:", response.status_code)
        try:
            print("Response:", response.json())
        except Exception:
            print("Response text:", response.text)

def parse_json(data, keys):
    result = {}

    if isinstance(data, list) and len(data) > 0:
        return parse_json(data[0], keys)

    if not isinstance(data, dict):
        return None

    for key in keys:
        if key in data:
            result[key] = data[key]

    return result

def main():
    github_token = os.getenv('GITHUB_TOKEN')
    session = build_session()

    for app in applications:
        try:
            current_version_info, download_url = fetch_version_info(session, app["version_url"])
        except Exception as e:
            print(f"Fehler beim Abrufen für {app['name']}: {e}")
            continue

        last_version_file_path = app.get('last_version_file') or f"{app['name']}.json"
        last_version_data = read_last_version(last_version_file_path)

        if isinstance(last_version_data, str):
            try:
                last_version_data = json.loads(last_version_data)
            except Exception:
                last_version_data = None

        last_version = last_version_data.get("Version") if isinstance(last_version_data, dict) else None

        if current_version_info and (last_version is None or current_version_info != last_version_data):
            print(f"Version geändert für {app['name']}: {current_version_info}")

            if github_token:
                create_github_issue(session, github_token, app["name"], current_version_info.get("Version"), download_url)
            else:
                print("Warnung: Kein GITHUB_TOKEN gesetzt, überspringe Issue-Erstellung.")

            write_current_version(last_version_file_path, current_version_info)
        else:
            print(f"Keine Änderung der Version für {app['name']}.")

if __name__ == "__main__":
    main()

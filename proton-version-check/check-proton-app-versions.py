import requests
import json
import os
from yaml import safe_load

# GitHub API URL for creating an issue
issue_url = "https://api.github.com/repos/traktuner/actions/issues"

# Load application configurations from YAML file
with open("applications.yaml", "r") as f:
    applications = safe_load(f)

def fetch_version_info(url):
    response = requests.get(url)
    data = json.loads(response.content)

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

def create_github_issue(token, app_name, new_version, download_url):
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    data = {
        'title': f'New version detected for {app_name}: {new_version}',
        'body': f'New Version of {app_name} detected: **{new_version}**\n\nDownload URL: {download_url}'
    }
    response = requests.post(issue_url, headers=headers, json=data)

    if response.status_code == 201:
        print(f"GitHub issue created successfully for {app_name}.")
    else:
        print(f"Failed to create GitHub issue for {app_name}. Status Code:", response.status_code)
        print("Response:", response.json())

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

    for app in applications:
        current_version_info, download_url = fetch_version_info(app["version_url"])

        last_version_file_path = f"{app['name']}.json"
        last_version_data = read_last_version(last_version_file_path)

        if isinstance(last_version_data, str):
            last_version_data = json.loads(last_version_data)

        last_version = last_version_data.get("Version") if last_version_data else None

        if current_version_info and (last_version is None or json.dumps(current_version_info) != json.dumps(last_version_data)):
            print(f"Version has changed for {app['name']}! New version: {current_version_info}")

            create_github_issue(github_token, app["name"], current_version_info.get("Version"), download_url)

            write_current_version(last_version_file_path, current_version_info)
        else:
            print(f"No change in version for {app['name']}.")

if __name__ == "__main__":
    main()

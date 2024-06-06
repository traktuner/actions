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

    # Extract relevant information using a generic parser function (e.g., parse_json)
    version_list = [item for item in data["Releases"] if "Version" in item]
    parsed_data, download_url = None, None
    if len(version_list) > 0:
        parsed_data = parse_json([version_list[0]], ["Version"])
        for release in data["Releases"]:
            if "File" in release:
                if isinstance(release["File"], list) and release["File"]:
                    download_url = release["File"][0]["Url"]
                    break
                elif isinstance(release["File"], dict):
                    download_url = release["File"].get("Url")
                    break

    return parsed_data, download_url

def read_last_version(file_path):
    try:
        with open(file_path, "r") as file:
            return json.loads(file.read())
    except FileNotFoundError:
        return None

def write_current_version(file_path, version, issue_number=None):
    data = {
        "version": version,
        "issue_number": issue_number
    }
    with open(file_path, "w") as file:
        json.dump(data, file)

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
        return response.json()["number"]
    else:
        print(f"Failed to create GitHub issue for {app_name}. Status Code:", response.status_code)
        print("Response:", response.json())
        return None

def parse_json(data, keys):
    result = {}

    if isinstance(data, list) and len(data) > 0:  # Check if data is a list
        return parse_json(data[0], keys)  # Recursively call parse_json on the first item in the list

    elif not isinstance(data, dict):  # If data is neither a list nor a dictionary, return None
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
        last_version = last_version_data.get("version") if last_version_data else None
        last_issue_number = last_version_data.get("issue_number") if last_version_data else None

        if current_version_info and (last_version is None or json.dumps(current_version_info) != json.dumps(last_version)):
            print(f"Version has changed for {app['name']}! New version: {current_version_info}")

            issue_number = create_github_issue(github_token, app["name"], current_version_info.get("Version"), download_url)

            if issue_number:
                write_current_version(last_version_file_path, current_version_info, issue_number)
        else:
            print(f"No change in version for {app['name']}.")

if __name__ == "__main__":
    main()

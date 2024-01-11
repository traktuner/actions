import requests
import json
import os

# URL of the version.json file
version_url = "https://proton.me/download/drive/macos/version.json"

# GitHub API URL for creating an issue
issue_url = "https://api.github.com/repos/traktuner/actions/issues"

# Your GitHub username
assignee = "traktuner"

def fetch_current_version(url):
    response = requests.get(url)
    data = response.json()
    return data['Releases'][0]['Version']

def read_last_version(file_path):
    try:
        with open(file_path, 'r') as file:
            return file.read().strip()
    except FileNotFoundError:
        return None

def write_current_version(file_path, version):
    with open(file_path, 'w') as file:
        file.write(version)

def create_github_issue(token, new_version):
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    data = {
        'title': f'New version detected: {new_version}',
        'body': f'A new version of the file has been detected: {new_version}',
        'assignees': [assignee]
    }
    response = requests.post(issue_url, headers=headers, json=data)
    if response.status_code == 201:
        print("GitHub issue created successfully.")
    else:
        print("Failed to create GitHub issue.")

def main():
    current_version = fetch_current_version(version_url)
    last_version = read_last_version('last_version.txt')

    if current_version != last_version:
        print(f"Version has changed! New version: {current_version}")
        github_token = os.getenv('GITHUB_TOKEN')
        create_github_issue(github_token, current_version)
        write_current_version('last_version.txt', current_version)
    else:
        print("No change in version.")

if __name__ == "__main__":
    main()

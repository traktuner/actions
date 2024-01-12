import requests
import json
import os

# URL of the version.json file
version_url = "https://proton.me/download/drive/windows/version.json"

# GitHub API URL for creating an issue
issue_url = "https://api.github.com/repos/traktuner/actions/issues"

# Your GitHub username
assignee = "traktuner"

def fetch_version_info(url):
    response = requests.get(url)
    data = response.json()
    version = data['Releases'][0]['Version']
    download_url = data['Releases'][0]['File']['Url']
    return version, download_url

def read_last_version(file_path):
    try:
        with open(file_path, 'r') as file:
            return file.read().strip()
    except FileNotFoundError:
        return None

def write_current_version(file_path, version):
    with open(file_path, 'w') as file:
        file.write(version)

def create_github_issue(token, new_version, download_url):
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    data = {
        'title': f'New Proton Drive Windows version detected: {new_version}',
        'body': f'New Proton Drive Windows Version detected: **{new_version}**\n\nDownload URL: {download_url}',
        'assignees': [assignee]
    }
    response = requests.post(issue_url, headers=headers, json=data)
    if response.status_code == 201:
        print("GitHub issue created successfully.")
    else:
        print("Failed to create GitHub issue. Status Code:", response.status_code)
        print("Response:", response.json())

def main():
    current_version, download_url = fetch_version_info(version_url)
    last_version = read_last_version('last_version_windows.txt')

    if current_version != last_version:
        print(f"Version has changed! New version: {current_version}")
        github_token = os.getenv('GITHUB_TOKEN')
        create_github_issue(github_token, current_version, download_url)
        write_current_version('last_version_windows.txt', current_version)
        print("::set-output name=version_changed::true")
        print(f"::set-output name=current_version::{current_version}")
    else:
        print("No change in version.")
        print("::set-output name=version_changed::false")

if __name__ == "__main__":
    main()
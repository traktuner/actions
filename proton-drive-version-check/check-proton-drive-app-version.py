import requests
import json
import os

# List of applications with their version URLs and names
applications = [
    {
        "name": "Proton Drive macOS",
        "version_url": "https://proton.me/download/drive/macos/version.json",
        "last_version_file": "last_version_drive_macos.txt"
    },
    {
        "name": "Proton Drive Windows",
        "version_url": "https://proton.me/download/drive/windows/version.json",
        "last_version_file": "last_version_drive_windows.txt"
    }
    # Add more applications here
]

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

def create_github_issue(token, app_name, new_version, download_url):
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    data = {
        'title': f'New version detected for {app_name}: {new_version}',
        'body': f'New Version of {app_name} detected: **{new_version}**\n\nDownload URL: {download_url}',
        'assignees': [assignee]
    }
    response = requests.post(issue_url, headers=headers, json=data)
    if response.status_code == 201:
        print(f"GitHub issue created successfully for {app_name}.")
    else:
        print(f"Failed to create GitHub issue for {app_name}. Status Code:", response.status_code)
        print("Response:", response.json())

def main():
    github_token = os.getenv('GITHUB_TOKEN')

    for app in applications:
        current_version, download_url = fetch_version_info(app['version_url'])
        last_version = read_last_version(app['last_version_file'])

        if current_version != last_version:
            print(f"Version has changed for {app['name']}! New version: {current_version}")
            create_github_issue(github_token, app['name'], current_version, download_url)
            write_current_version(app['last_version_file'], current_version)
            print(f"::set-output name=version_changed_{app['name']}::true")
        else:
            print(f"No change in version for {app['name']}.")
            print(f"::set-output name=version_changed_{app['name']}::false")

if __name__ == "__main__":
    main()

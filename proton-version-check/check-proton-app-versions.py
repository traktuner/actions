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
    return parse_json(version_list[0] or {}, ["Version"]) if len(version_list) > 0 else None  
  
def read_last_version(file_path):  
    try:  
        with open(file_path, "r") as file:  
            return json.loads(file.read())  
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
      
    if isinstance(data, list) and len(data) > 0:   # Check if data is a list  
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
        current_version_info = fetch_version_info(app["version_url"])  
          
        last_version_file_path = f"{app['name']}.json"  
        last_version = read_last_version(last_version_file_path)  
          
        if current_version_info and (last_version is None or json.dumps(current_version_info) != json.dumps(last_version)):  
            print(f"Version has changed for {app['name']}! New version: {current_version_info}")  
              
            create_github_issue(github_token, app["name"], current_version_info.get("Version"), current_version_info.get("File", {}).get("Url"))  
              
            write_current_version(last_version_file_path, json.dumps(current_version_info))  
        else:  
            print(f"No change in version for {app['name']}.")  
          
if __name__ == "__main__":  
    main()  
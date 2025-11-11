import requests

url = "http://localhost/api/tools/add"
headers = {
    "Content-Type": "application/json",
    "X-Api-Key": "prod-key-123"
}
payload = {"a": 1, "b": 2}

resp = requests.post(url, json=payload, headers=headers)
print(resp.status_code, resp.text)

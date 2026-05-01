import httpx
from kiro.core import get_accounts, get_access_token
import json

accounts = get_accounts()
if accounts:
    acc = accounts[0]
    token = get_access_token(acc['profile_arn'])
    
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': 'aws-sdk-js/3.0.0'
    }
    
    endpoints = [
        "https://codewhisperer.us-east-1.amazonaws.com/GetEntitlement",
        "https://codewhisperer.us-east-1.amazonaws.com/GetSubscription",
        "https://codewhisperer.us-east-1.amazonaws.com/GetUsage",
        "https://codewhisperer.us-east-1.amazonaws.com/GetProfile",
        "https://codewhisperer.us-east-1.amazonaws.com/GetIdentity",
        "https://codewhisperer.us-east-1.amazonaws.com/ListAvailableModels",
        "https://codewhisperer.us-east-1.amazonaws.com/GetCodeWhispererUsage",
        "https://codewhisperer.us-east-1.amazonaws.com/GetCodeWhispererEntitlement"
    ]
    
    for url in endpoints:
        try:
            r = httpx.post(url, headers=headers, json={"profileArn": acc['profile_arn']})
            print(f"POST {url} -> {r.status_code}")
            if r.status_code == 200:
                print(r.text)
        except Exception as e:
            pass
        try:
            r = httpx.get(url, headers=headers)
            print(f"GET {url} -> {r.status_code}")
            if r.status_code == 200:
                print(r.text)
        except Exception as e:
            pass

import random
import httpx
import time
import re
import json
from flask import Flask, jsonify, request
import os
import asyncio
import aiohttp
from byte import encrypt_api, Encrypt_ID
from visit_count_pb2 import Info
import jwt as pyjwt
import subprocess
import threading

app = Flask(__name__)

# Vercel/GitHub AUTO UPDATE CONFIG
GITHUB_REPO = "hackervaibhav-dot/Visit-UID"  # Change this!
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Vercel Environment Variable
UPDATE_BRANCH = "main"

# YOUR RIZER JWT API
RIZER_JWT_API = "https://rizerxguestaccountacceee.vercel.app/rizer"
TOKEN_CACHE = {}

def git_push_update():
    """AUTO GIT PUSH TO GITHUB"""
    try:
        if not GITHUB_TOKEN:
            print("⚠️ No GITHUB_TOKEN - skipping git push")
            return False
        
        cmd = [
            "git", "add", ".",
            "&&", "git", "commit", "-m", f"Auto token update {time.strftime('%Y-%m-%d %H:%M')}",
            "&&", "git", "push", f"https://{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git", UPDATE_BRANCH
        ]
        result = subprocess.run(" && ".join(cmd), shell=True, capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ GIT PUSH SUCCESS!")
            return True
        else:
            print(f"❌ Git error: {result.stderr}")
            return False
    except:
        return False

def save_token_cache(server, tokens):
    """SAVE TOKENS TO /tmp (Vercel writable)"""
    cache_file = f"/tmp/token_cache_{server.lower()}.json"
    data = {
        "server": server,
        "tokens": len(tokens),
        "timestamp": time.time(),
        "updated": time.strftime('%Y-%m-%d %H:%M:%S')
    }

    with open(cache_file, "w") as f:
        json.dump(data, f, indent=2)
    
    # AUTO GIT COMMIT every 10 updates
    if random.random() < 0.1:  # 10% chance
        threading.Thread(target=git_push_update, daemon=True).start()

def get_jwt_token_from_rizer(uid, password):
    try:
        url = f"{RIZER_JWT_API}?uid={uid}&password={password}"
        resp = httpx.get(url, timeout=10.0)
        token = None
        
        try:
            j = resp.json()
            for k in ["token", "jwt", "access_token"]:
                if j.get(k) and j[k].startswith("ey"):
                    token = j[k]
                    break
        except:
            pass
        
        if not token:
            m = re.search(r'(eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,})', resp.text)
            if m:
                token = m.group(1)
        
        return token
    except:
        return None

def get_region_from_jwt(jwt_token):
    try:
        decoded = pyjwt.decode(jwt_token, options={"verify_signature": False})
        return decoded.get('lock_region', 'IND').upper()
    except:
        return 'IND'

def load_fresh_tokens(server):
    """AUTO REFRESH 5HR + VERCEL/GITHUB SAVE"""
    now = time.time()
    cache_key = f"{server}_tokens"
    
    if cache_key not in TOKEN_CACHE or (now - TOKEN_CACHE[cache_key]['timestamp']) > 18000:
        print(f"🔄 REFRESH {server} TOKENS...")
        
        filename = f"account_{server.lower()}.json"
        tokens = []
        
        if os.path.exists(filename):
            with open(filename, "r") as f:
                accounts = json.load(f)
            
            for account in accounts:
                uid = account.get("uid")
                password = account.get("password")
                if uid and password:
                    token = get_jwt_token_from_rizer(uid, password)
                    if token:
                        tokens.append({
                            "uid": uid,
                            "token": token,
                            "region": get_region_from_jwt(token)
                        })
        
        TOKEN_CACHE[cache_key] = {
            "tokens": tokens,
            "timestamp": now,
            "count": len(tokens)
        }
        
        save_token_cache(server, tokens)
        print(f"✅ {len(tokens)} FRESH TOKENS")
    
    return TOKEN_CACHE[cache_key]["tokens"]

def get_region_url(region):
    region = region.upper()
    if region == "IND":
        return "https://client.ind.freefiremobile.com"
    elif region in ["BR", "US", "SAC", "NA"]:
        return "https://client.us.freefiremobile.com"
    else:
        return "https://clientbp.ggblueshark.com"

def parse_protobuf_response(data):
    try:
        info = Info()
        info.ParseFromString(data)
        return {
            "nickname": getattr(info.AccountInfo, 'PlayerNickname', '') or '',
            "level": getattr(info.AccountInfo, 'Levels', 0) or 0,
            "likes": getattr(info.AccountInfo, 'Likes', 0) or 0
        }
    except:
        return {}

async def send_single_visit(session, base_url, token, uid, visit_data):
    headers = {
        "Expect": "100-continue",
        "Authorization": f"Bearer {token}",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": "OB52",
        "Content-Type": "application/octet-stream",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; SM-A305F Build/RP1A.200720.012)",
        "Host": base_url.replace("https://", ""),
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip"
    }
    
    try:
        url = f"{base_url}/GetPlayerPersonalShow"
        async with session.post(url, headers=headers, data=visit_data, timeout=20) as resp:
            return resp.status == 200, await resp.read()
    except:
        return False, None

async def send_until_10000_success(tokens, uid, server):
    base_url = get_region_url(server)
    visit_data = bytes.fromhex(encrypt_api("08" + Encrypt_ID(str(uid)) + "1801"))
    
    total_success = 0
    player_info = None
    
    connector = aiohttp.TCPConnector(limit=100, limit_per_host=20, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        while total_success < 10000:
            batch_size = min(10000 - total_success, 300)
            tasks = []
            
            for i in range(batch_size):
                token_data = tokens[i % len(tokens)]
                if token_data["region"] == server:
                    task = send_single_visit(session, base_url, token_data["token"], uid, visit_data)
                    tasks.append(task)
            
            results = await asyncio.gather(*tasks)
            
            batch_success = sum(1 for success, _ in results if success)
            total_success += batch_success
            
            if not player_info:
                for success, data in results:
                    if success and data:
                        player_info = parse_protobuf_response(data)
                        break
            
            print(f"⚡ {total_success}/10000")
    
    return total_success, player_info

@app.route('/<string:server>/<int:uid>')
def visit_route(server, uid):
    tokens = load_fresh_tokens(server)
    
    if not tokens:
        return jsonify({"error": "No tokens"}), 500
    
    total, info = asyncio.run(send_until_10000_success(tokens, uid, server))
    
    return jsonify({
        "success": total,
        "tokens": len(tokens),
        "player": info
    })

# HEALTH CHECK FOR VERCEL
@app.route('/health')
def health():
    return jsonify({"status": "OK", "tokens": len(TOKEN_CACHE)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

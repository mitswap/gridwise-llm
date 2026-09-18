import json
import urllib.request
import urllib.error
import time

# --- EDIT THIS TO YOUR EXACT RENDER URL ---
RENDER_URL = "https://gridwise-llm-i91c.onrender.com"
# ------------------------------------------

JSON_FILE = r"e:\BUP\BUP_CSE_FEST_2026_Participant_Docs\BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"

def run_tests():
    print(f"Loading test cases from {JSON_FILE}...")
    with open(JSON_FILE, "r") as f:
        data = json.load(f)
        
    cases = data.get("cases", [])
    print(f"Found {len(cases)} test cases. Sending to {RENDER_URL}...\n")
    
    endpoint = f"{RENDER_URL}/optimize-energy"
    
    for case in cases:
        print(f"Testing {case['id']}: {case['label']}")
        payload = json.dumps(case["input"]).encode("utf-8")
        
        req = urllib.request.Request(
            endpoint, 
            data=payload, 
            headers={"Content-Type": "application/json"}
        )
        
        start_time = time.time()
        try:
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode("utf-8"))
                latency = time.time() - start_time
                print(f"  ✅ Success! Status 200. (Latency: {latency:.2f}s)")
                print(f"  💰 Optimized Cost: {result['metadata']['total_cost_bdt']} BDT")
                
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            print(f"  ❌ FAILED! Status {e.code}")
            print(f"  Reason: {error_body}")
        except Exception as e:
            print(f"  ❌ FAILED! Error: {str(e)}")
            
        print("-" * 50)

if __name__ == "__main__":
    run_tests()

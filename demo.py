#!/usr/bin/env python3
"""
Demo script to showcase the API Scheduler functionality.

Run this after starting the server:
    uvicorn app.main:app --reload

Then in another terminal:
    python demo.py
"""
import httpx
import time
import json
from datetime import datetime

BASE_URL = "http://localhost:8000/api/v1"


def print_json(data):
    """Pretty print JSON data."""
    print(json.dumps(data, indent=2, default=str))


def main():
    print("=" * 60)
    print("API Scheduler Demo")
    print("=" * 60)
    print()
    
    with httpx.Client(timeout=30) as client:
        # 1. Health Check
        print("1. Checking health...")
        response = client.get(f"{BASE_URL}/health")
        print(f"   Status: {response.json()['status']}")
        print()
        
        # 2. Create Target
        print("2. Creating a target (httpbin.org endpoint)...")
        target_data = {
            "name": "HTTPBin POST",
            "url": "https://httpbin.org/post",
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "X-Custom-Header": "api-scheduler-demo"
            },
            "body_template": json.dumps({
                "message": "Hello from API Scheduler!",
                "timestamp": "{{timestamp}}"
            }),
            "timeout_seconds": 30.0
        }
        response = client.post(f"{BASE_URL}/targets", json=target_data)
        target = response.json()
        target_id = target["id"]
        print(f"   Created target: {target['name']} (ID: {target_id})")
        print()
        
        # 3. Create Interval Schedule
        print("3. Creating an interval schedule (every 5 seconds, max 3 runs)...")
        schedule_data = {
            "name": "Demo Schedule",
            "target_id": target_id,
            "schedule_type": "interval",
            "interval_seconds": 5,
            "max_runs": 3
        }
        response = client.post(f"{BASE_URL}/schedules", json=schedule_data)
        schedule = response.json()
        schedule_id = schedule["id"]
        print(f"   Created schedule: {schedule['name']} (ID: {schedule_id})")
        print(f"   Status: {schedule['status']}")
        print(f"   Next run: {schedule.get('next_run_at', 'N/A')}")
        print()
        
        # 4. Wait and watch runs
        print("4. Waiting for scheduled runs...")
        print("   (This will take about 15 seconds)")
        print()
        
        for i in range(6):
            time.sleep(5)
            
            # Check runs
            response = client.get(f"{BASE_URL}/runs", params={"schedule_id": schedule_id})
            runs = response.json()
            
            # Check schedule status
            response = client.get(f"{BASE_URL}/schedules/{schedule_id}")
            schedule = response.json()
            
            print(f"   [{datetime.now().strftime('%H:%M:%S')}] "
                  f"Runs: {len(runs)}, "
                  f"Schedule status: {schedule['status']}, "
                  f"Run count: {schedule['run_count']}")
            
            if schedule["status"] == "expired":
                print("   Schedule expired (max runs reached)")
                break
        
        print()
        
        # 5. Inspect runs
        print("5. Inspecting run details...")
        response = client.get(f"{BASE_URL}/runs", params={"schedule_id": schedule_id})
        runs = response.json()
        
        for run in runs[:3]:
            print(f"   Run {run['id'][:8]}...")
            print(f"     Status: {run['status']}")
            print(f"     Scheduled: {run['scheduled_at']}")
            print(f"     Attempts: {run['attempt_count']}")
            
            if run['final_status_code']:
                print(f"     Status Code: {run['final_status_code']}")
            
            # Get attempt details
            response = client.get(f"{BASE_URL}/runs/{run['id']}")
            run_detail = response.json()
            
            for attempt in run_detail.get("attempts", []):
                print(f"     Attempt {attempt['attempt_number']}: "
                      f"{attempt['status_code']} in {attempt.get('latency_ms', 'N/A')}ms")
            print()
        
        # 6. View Metrics
        print("6. Viewing metrics...")
        response = client.get(f"{BASE_URL}/metrics")
        metrics = response.json()
        print(f"   Total targets: {metrics['total_targets']}")
        print(f"   Total schedules: {metrics['total_schedules']}")
        print(f"   Active schedules: {metrics['active_schedules']}")
        print(f"   Total runs: {metrics['total_runs']}")
        print(f"   24h success rate: {metrics['success_rate_24h']}%")
        print()
        
        # 7. Prometheus metrics
        print("7. Prometheus metrics available at:")
        print(f"   {BASE_URL}/metrics/prometheus")
        print()
        
        # 8. Create another schedule and pause/resume it
        print("8. Demonstrating pause/resume...")
        schedule_data = {
            "name": "Pausable Schedule",
            "target_id": target_id,
            "schedule_type": "interval",
            "interval_seconds": 10,
        }
        response = client.post(f"{BASE_URL}/schedules", json=schedule_data)
        schedule2 = response.json()
        schedule2_id = schedule2["id"]
        print(f"   Created schedule: {schedule2['name']}")
        print(f"   Status: {schedule2['status']}")
        
        # Pause
        response = client.post(f"{BASE_URL}/schedules/{schedule2_id}/pause")
        schedule2 = response.json()
        print(f"   After pause: {schedule2['status']}")
        
        # Resume
        response = client.post(f"{BASE_URL}/schedules/{schedule2_id}/resume")
        schedule2 = response.json()
        print(f"   After resume: {schedule2['status']}")
        print()
        
        # 9. Cleanup
        print("9. Cleaning up...")
        client.delete(f"{BASE_URL}/schedules/{schedule2_id}")
        client.delete(f"{BASE_URL}/schedules/{schedule_id}")
        client.delete(f"{BASE_URL}/targets/{target_id}")
        print("   Deleted all demo resources")
        print()
        
        print("=" * 60)
        print("Demo complete!")
        print("=" * 60)
        print()
        print("Explore more at: http://localhost:8000/docs")


if __name__ == "__main__":
    main()

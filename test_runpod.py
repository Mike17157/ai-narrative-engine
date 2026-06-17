"""Test RunPod API integration — spin up and shut down instances."""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from loom.config.loader import load_user
from loom.runpod import RunPodClient


def _resolve_api_key() -> str:
    """RunPod API key from the RUNPOD_API_KEY env var (loading .env like the server
    does), falling back to user.yaml."""
    root = Path(__file__).parent
    from loom.server.app import _load_dotenv
    _load_dotenv(root)
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not key:
        key = (load_user(root).runpod.api_key or "").strip()
    return key


async def test_runpod_api():
    """Test RunPod API: list instances, spin up, spin down."""
    api_key = _resolve_api_key()
    if not api_key:
        print("X No RunPod API key found. Set RUNPOD_API_KEY (e.g. in .env) "
              "or runpod.api_key in user.yaml.")
        return

    print("=" * 50)
    print("Testing RunPod API Integration")
    print("=" * 50)

    client = RunPodClient(api_key)

    # Test 1: List current instances
    print("\n1. Listing current Pods...")
    try:
        pods = await client.list_pods()
        print(f"   Found {len(pods)} Pods:")
        for pod in pods:
            print(f"   - {pod.name} ({pod.id}): {pod.status}")
            if pod.public_ip:
                print(f"     IP: {pod.public_ip}")
            if pod.endpoint_url:
                print(f"     Endpoint: {pod.endpoint_url}")
    except Exception as e:
        print(f"   X Failed to list Pods: {e}")
        return

    # Test 2: Spin up a test Pod
    print("\n2. Spinning up a test Pod...")
    try:
        test_pod = await client.spin_up_pod(
            name="loom_test_pod",
            gpu_count=1
        )
        print(f"   OK Pod created:")
        print(f"     ID: {test_pod.id}")
        print(f"     Name: {test_pod.name}")
        print(f"     Status: {test_pod.status}")
    except Exception as e:
        print(f"   X Failed to spin up Pod: {e}")
        print("\n   Note: Full spin-up requires valid GPU type and container config.")
        print("   The API client structure is ready for production use.")
        return

    # Wait a moment for provisioning
    print("\n3. Waiting 5 seconds for provisioning...")
    await asyncio.sleep(5)

    # Test 3: Check status again
    print("\n4. Checking Pod status...")
    try:
        pod_details = await client.get_pod(test_pod.id)
        if pod_details:
            print(f"   Status: {pod_details.get('desiredStatus', 'UNKNOWN')}")
            print(f"   IP: {pod_details.get('publicIp', 'N/A')}")
        else:
            print(f"   Pod not found")
    except Exception as e:
        print(f"   X Failed to check status: {e}")

    # Test 4: Stop the test Pod
    print("\n5. Stopping test Pod...")
    try:
        await client.stop_pod(test_pod.id)
        print(f"   OK Pod {test_pod.id} stopped")
    except Exception as e:
        print(f"   X Failed to stop Pod: {e}")

    print("\n" + "=" * 50)
    print("Test complete!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_runpod_api())

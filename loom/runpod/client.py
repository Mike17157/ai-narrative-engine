"""RunPod GPU scaling — dynamic instance management for image generation.

The RunPod API allows spinning up and down GPU instances on demand. This module
provides:
- Instance lifecycle (spin up / down / list)
- Queue-based image job allocation
- Auto-scaling based on batch size (images_per_instance threshold)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class RunPodInstance:
    """A running RunPod GPU instance (Pod)."""
    id: str
    name: str
    endpoint_url: str
    status: str = "RUNNING"  # RUNNING, EXITED, TERMINATED, PROVISIONING
    gpu: str = ""  # GPU type name
    public_ip: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class ImageJob:
    """A single image generation job in the queue."""
    id: str
    workflow: dict  # ComfyUI workflow JSON
    params: dict  # Generation parameters (prompt, negative, etc.)
    status: str = "pending"  # pending, assigned, running, completed, failed
    instance_id: str | None = None
    result: bytes | None = None
    error: str | None = None


class RunPodClient:
    """RunPod API client for GPU instance (Pod) management."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://rest.runpod.io"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    async def list_pods(self) -> list[RunPodInstance]:
        """List all GPU Pods in the account."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/v1/pods",
                headers=self.headers,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            return [
                RunPodInstance(
                    id=inst.get("id", ""),
                    name=inst.get("name", ""),
                    endpoint_url=f"http://{inst.get('publicIp', '')}:{inst.get('portMappings', {}).get('8888', 8888)}" if inst.get('publicIp') else "",
                    status=inst.get("desiredStatus", "UNKNOWN"),
                    gpu=inst.get("gpu", {}).get("displayName", ""),
                    public_ip=inst.get("publicIp", "")
                )
                for inst in data
            ]

    async def spin_up_pod(
        self,
        name: str,
        template_id: str | None = None,
        gpu_count: int = 1,
        gpu_type_ids: list[str] | None = None,
        image_name: str = "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04",
        data_center_ids: list[str] | None = None,
        support_public_ip: bool = True
    ) -> RunPodInstance:
        """Spin up a new GPU Pod. Returns the Pod once created.

        Args:
            name: Pod name
            template_id: Optional template ID (uses defaults if None)
            gpu_count: Number of GPUs
            gpu_type_ids: List of preferred GPU types (RTX 4090, etc.)
            image_name: Docker image to run
            data_center_ids: Preferred data centers
            support_public_ip: Whether to support public IP
        """
        async with httpx.AsyncClient() as client:
            payload = {
                "name": name,
                "imageName": image_name,
                "gpuCount": gpu_count,
                "computeType": "GPU",
                "cloudType": "COMMUNITY",
                "containerDiskInGb": 50,
                "volumeInGb": 50,
                "minVCPUPerGPU": 2,
                "minRAMPerGPU": 8,
                "ports": ["8888/http", "22/tcp"],
                "supportPublicIp": support_public_ip,
                "interruptible": True,  # Spot instance (cheaper)
            }

            if gpu_type_ids:
                payload["gpuTypeIds"] = gpu_type_ids
            if data_center_ids:
                payload["dataCenterIds"] = data_center_ids
            if template_id:
                payload["templateId"] = template_id

            response = await client.post(
                f"{self.base_url}/v1/pods",
                headers=self.headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            data = response.json()
            return RunPodInstance(
                id=data.get("id", ""),
                name=name,
                endpoint_url=f"http://{data.get('publicIp', '')}:8888" if data.get('publicIp') else "",
                status=data.get("desiredStatus", "PROVISIONING"),
                public_ip=data.get("publicIp", ""),
                gpu=data.get("gpu", {}).get("displayName", "")
            )

    async def stop_pod(self, pod_id: str) -> None:
        """Stop a GPU Pod."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/pods/{pod_id}/stop",
                headers=self.headers,
                timeout=30
            )
            response.raise_for_status()

    async def get_pod(self, pod_id: str) -> dict | None:
        """Get details for a specific Pod."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/v1/pods/{pod_id}",
                headers=self.headers,
                timeout=30
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    # Backwards compatibility aliases
    async def list_instances(self) -> list[RunPodInstance]:
        """Alias for list_pods."""
        return await self.list_pods()

    async def spin_up_instance(self, *args, **kwargs) -> RunPodInstance:
        """Alias for spin_up_pod."""
        return await self.spin_up_pod(*args, **kwargs)

    async def spin_down_instance(self, instance_id: str) -> None:
        """Alias for stop_pod."""
        await self.stop_pod(instance_id)


class ImageQueue:
    """Queue-based image job allocation with auto-scaling.

    Jobs are queued and automatically allocated to GPU instances:
    - Small batches (< images_per_instance): single instance
    - Large batches: spin up additional instances, allocate N jobs per instance
    """

    def __init__(
        self,
        runpod_client: RunPodClient,
        images_per_instance: int = 10,
        min_instances: int = 1,
        max_instances: int = 10
    ):
        self.client = runpod_client
        self.images_per_instance = images_per_instance
        self.min_instances = min_instances
        self.max_instances = max_instances

        self._queue: list[ImageJob] = []
        self._instances: dict[str, RunPodInstance] = {}
        self._job_counter = 0

    def enqueue(self, workflow: dict, params: dict) -> ImageJob:
        """Add an image job to the queue."""
        self._job_counter += 1
        job = ImageJob(
            id=f"job_{self._job_counter}",
            workflow=workflow,
            params=params
        )
        self._queue.append(job)
        return job

    async def allocate(self) -> dict[str, list[ImageJob]]:
        """Allocate queued jobs to instances, spinning up new ones as needed.

        Returns {instance_id: [jobs]} mapping.
        """
        if not self._queue:
            return {}

        # Calculate needed instances
        pending_count = len([j for j in self._queue if j.status == "pending"])
        needed_instances = max(
            self.min_instances,
            min(
                self.max_instances,
                (pending_count + self.images_per_instance - 1) // self.images_per_instance
            )
        )

        # Get current running instances
        current_instances = await self.client.list_instances()
        running = [i for i in current_instances if i.status == "RUNNING"]

        # Spin up more if needed
        while len(running) < needed_instances:
            new_instance = await self.client.spin_up_instance(
                name=f"loom_worker_{len(running) + 1}"
            )
            running.append(new_instance)

        # Allocate jobs to instances (round-robin)
        allocation: dict[str, list[ImageJob]] = {i.id: [] for i in running}
        pending_jobs = [j for j in self._queue if j.status == "pending"]

        for idx, job in enumerate(pending_jobs):
            instance_id = running[idx % len(running)].id
            job.instance_id = instance_id
            job.status = "assigned"
            allocation[instance_id].append(job)

        return allocation

    async def process_batch(
        self,
        workflow: dict,
        prompts: list[dict],
        comfyui_provider_class,
        workflow_options: dict
    ) -> list[bytes]:
        """Process a batch of images with auto-scaling.

        Args:
            workflow: ComfyUI workflow JSON
            prompts: List of generation params [{prompt, negative, ...}]
            comfyui_provider_class: ComfyUIProvider class (to create per-instance providers)
            workflow_options: Options dict for ComfyUIProvider (workflow path, etc.)

        Returns:
            List of generated image bytes
        """
        # Get current running pods
        pods = await self.client.list_pods()
        running = [p for p in pods if p.status == "RUNNING"]

        # If no pods running, just use the local provider
        if not running:
            # Fallback: generate serially
            results = []
            provider = comfyui_provider_class(workflow_options)
            for p in prompts:
                try:
                    result = provider.generate_image(
                        prompt=p.get("prompt", ""),
                        negative_prompt=p.get("negative_prompt"),
                        init_image=p.get("init_image"),
                        out_prefix=p.get("out_prefix"),
                        latent=p.get("latent")
                    )
                    results.append(result.images[0] if result.images else None)
                except Exception as e:
                    print(f"Generation failed: {e}")
                    results.append(None)
            return results

        # Allocate jobs across instances (10 per instance)
        allocation = {}
        for idx, p in enumerate(prompts):
            instance_idx = idx // self.images_per_instance
            if instance_idx >= len(running):
                # More jobs than instances - some will handle extra
                instance_idx = len(running) - 1
            pod_id = running[instance_idx].id
            allocation.setdefault(pod_id, []).append((idx, p))

        # Process each instance's jobs in parallel
        async def process_instance(pod_id: str, jobs_with_idx: list[tuple[int, dict]]):
            pod = next((p for p in running if p.id == pod_id), None)
            if not pod or not pod.endpoint_url:
                return [(idx, None) for idx, _ in jobs_with_idx]

            # Create provider for this pod's endpoint
            options = {**workflow_options, "base_url": pod.endpoint_url}
            provider = comfyui_provider_class(options)

            results = []
            for idx, p in jobs_with_idx:
                try:
                    result = provider.generate_image(
                        prompt=p.get("prompt", ""),
                        negative_prompt=p.get("negative_prompt"),
                        init_image=p.get("init_image"),
                        out_prefix=p.get("out_prefix"),
                        latent=p.get("latent")
                    )
                    results.append((idx, result.images[0] if result.images else None))
                except Exception as e:
                    print(f"Generation failed on {pod.name}: {e}")
                    results.append((idx, None))
            return results

        # Process all instances in parallel
        import asyncio
        tasks = [process_instance(pod_id, jobs) for pod_id, jobs in allocation.items()]
        all_results = await asyncio.gather(*tasks)

        # Merge results in order
        merged = [None] * len(prompts)
        for instance_results in all_results:
            for idx, png in instance_results:
                merged[idx] = png

        return merged

    async def scale_down(self, keep_count: int | None = None) -> None:
        """Spin down excess instances.

        Args:
            keep_count: How many instances to keep (defaults to min_instances)
        """
        keep_count = keep_count if keep_count is not None else self.min_instances

        current_instances = await self.client.list_instances()
        running = [i for i in current_instances if i.status == "RUNNING"]

        # Stop excess instances
        for instance in running[keep_count:]:
            await self.client.spin_down_instance(instance.id)


# Singleton instance factory
_client: RunPodClient | None = None
_queue: ImageQueue | None = None


def get_runpod_client(api_key: str) -> RunPodClient:
    """Get or create the RunPod client singleton."""
    global _client
    if _client is None:
        _client = RunPodClient(api_key)
    return _client


def get_image_queue(config: dict) -> ImageQueue:
    """Get or create the ImageQueue singleton."""
    global _queue
    if _queue is None:
        client = get_runpod_client(config["api_key"])
        _queue = ImageQueue(
            client,
            images_per_instance=config.get("images_per_instance", 10),
            min_instances=config.get("min_instances", 1),
            max_instances=config.get("max_instances", 10)
        )
    return _queue

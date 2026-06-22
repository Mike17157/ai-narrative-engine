"""RunPod integration.

Network-volume model sync (``volume``) is the live piece, imported directly where
used. Serverless rendering goes through ``loom.providers.runpod_serverless_provider``
and the fan-out in ``loom.server.services.batch_images`` — there is no per-pod
spin-up/scaling layer here anymore (it was unused once the serverless endpoint
became the path).
"""

"""Build-time diagnostic: test imports Impact-Pack needs at runtime."""
import sys
import traceback

sys.path.insert(0, "/comfyui")

TESTS = [
    ("cv2",                    "import cv2; print('cv2', cv2.__version__)"),
    ("segment_anything",       "from segment_anything import sam_model_registry; print('segment_anything OK')"),
    ("comfy_api.latest.io",    "from comfy_api.latest import io; print('comfy_api.latest.io OK')"),
    ("nodes_diff_diffusion",   "from comfy_extras import nodes_differential_diffusion; print('nodes_differential_diffusion OK')"),
    ("skimage",                "from skimage.measure import label, regionprops; print('skimage OK')"),
    ("piexif",                 "import piexif; print('piexif OK')"),
    ("dill",                   "import dill; print('dill OK')"),
    ("ultralytics",            "import ultralytics; print('ultralytics', ultralytics.__version__)"),
]

failed = []
for name, code in TESTS:
    try:
        exec(code)
    except Exception as e:
        print(f"FAIL [{name}]: {e}")
        traceback.print_exc()
        failed.append(name)

if failed:
    print(f"\nFAILED imports: {failed}")
    sys.exit(1)
print("\nAll diagnostic imports OK")

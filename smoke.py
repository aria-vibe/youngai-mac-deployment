"""Send one small request to an already running server; never load another model."""
import argparse
import base64
import json
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="e.g. http://127.0.0.1:9000/v1")
    parser.add_argument("--image", type=Path, help="optional local PNG or JPEG")
    args = parser.parse_args()
    base = args.base_url
    if not base:
        state = json.loads((Path(__file__).resolve().parent / "run/service.json").read_text())
        base = state["base_url"].replace("0.0.0.0", "127.0.0.1")
    base = base.rstrip("/")
    with urlopen(base + "/models", timeout=30) as response:
        model = json.load(response)["data"][0]["id"]
    content = "Reply with OK."
    if args.image:
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(args.image.suffix.lower())
        if not mime or args.image.stat().st_size > 16 * 1024**2:
            parser.error("use a PNG/JPEG no larger than 16 MiB")
        encoded = base64.b64encode(args.image.read_bytes()).decode()
        content = [{"type": "text", "text": "Describe the image and transcribe any visible text."},
                   {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}]
    body = {"model": model, "messages": [{"role": "user", "content": content}],
            "max_tokens": 128, "temperature": 0, "seed": 42, "reasoning_effort": "none"}
    request = Request(base + "/chat/completions", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=300) as response:
        result = json.load(response)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.image and result["choices"][0]["message"]["content"].strip() != "OK":
        raise SystemExit("API responded, but the text smoke assertion failed")


if __name__ == "__main__":
    main()

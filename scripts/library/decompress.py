"""Decompress Draco-compressed library sources in place (the packer reads plain glTF only).

Runs three's own Draco decoder and glTF exporter in the overlay's browser preview, through the
dev-only ``window.__pyxiDecompress`` hook (desktop/src/renderer/src/scene/assets.ts):

    cd desktop && npx vite --config vite.web.config.ts        (or set PYXI_PREVIEW)
    python -I scripts/library/decompress.py [ids...]

The compressed original is kept beside it as <id>.draco.glb.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import sys
from pathlib import Path

from playwright.async_api import async_playwright

SRC = Path(__file__).resolve().parents[2] / ".pyxi" / "library_src"
URL = os.environ.get("PYXI_PREVIEW", "http://localhost:5199") + "/overlay.html?backdrop=1&live=1"


def compressed(path: Path) -> bool:
    data = path.read_bytes()[:20]
    if data[:4] != b"glTF":
        return False
    n = struct.unpack_from("<I", data, 12)[0]
    head = json.loads(path.read_bytes()[20 : 20 + n])
    return "KHR_draco_mesh_compression" in head.get("extensionsUsed", [])


async def main() -> None:
    want = set(sys.argv[1:])
    todo = [p for p in sorted(SRC.glob("*.glb")) if not p.name.endswith(".draco.glb") and (not want or p.stem in want) and compressed(p)]
    if not todo:
        print("nothing compressed")
        return
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        # The overlay forbids WebAssembly (rightly); this build-time tool runs in its own browser.
        page = await (await browser.new_context(bypass_csp=True)).new_page()
        await page.goto(URL)
        await page.wait_for_function("() => typeof window.__pyxiDecompress === 'function'", timeout=30000)
        for path in todo:
            b64 = base64.b64encode(path.read_bytes()).decode()
            try:
                out = await page.evaluate("(b) => window.__pyxiDecompress(b)", b64)
            except Exception as exc:
                print(f"FAILED {path.stem}: {str(exc)[:200]}")
                continue
            path.rename(path.with_suffix(".draco.glb"))
            path.write_bytes(base64.b64decode(out))
            print(f"{path.stem}: {path.with_suffix('.draco.glb').stat().st_size // 1024} KB -> {path.stat().st_size // 1024} KB")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

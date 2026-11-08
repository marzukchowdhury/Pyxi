/**
 * Real models from Pyxi's library (.glb files the brain serves from its data folder). Loaded
 * once and shared: every part that uses a model clones its own pieces from the one copy.
 */
import { useLoader } from "@react-three/fiber";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { brainUrl } from "../shared/bridge";

export function assetUrl(id: string): string {
  return brainUrl(`/library/${encodeURIComponent(id)}.glb`);
}

export function useAsset(id: string) {
  return useLoader(GLTFLoader, assetUrl(id));
}

/** Development only (scripts/library/decompress.py): turn a Draco-compressed .glb into a plain
 * one the library packer can read, using three's own decoder and exporter. Never in a build. */
if (import.meta.env.DEV) {
  (window as unknown as { __pyxiDecompress: (b64: string) => Promise<string> }).__pyxiDecompress = async (b64: string) => {
    const { DRACOLoader } = await import("three/examples/jsm/loaders/DRACOLoader.js");
    const { GLTFExporter } = await import("three/examples/jsm/exporters/GLTFExporter.js");
    const wrapper = (await import("three/examples/jsm/libs/draco/gltf/draco_wasm_wrapper.js?url")).default as string;
    const draco = new DRACOLoader().setDecoderPath(wrapper.slice(0, wrapper.lastIndexOf("/") + 1));
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const gltf = await new GLTFLoader().setDRACOLoader(draco).parseAsync(bytes.buffer, "");
    const out = (await new GLTFExporter().parseAsync(gltf.scene, { binary: true })) as ArrayBuffer;
    let s = "";
    const u = new Uint8Array(out);
    for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000));
    draco.dispose();
    return btoa(s);
  };
}

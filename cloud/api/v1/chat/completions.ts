// POST /api/v1/chat/completions: OpenAI-style chat, proxied to OpenRouter within the beta's limits.
import { chat, configFromEnv } from "../../../lib/beta.js";
import { storeFromEnv } from "../../../lib/store.js";

export async function POST(req: Request): Promise<Response> {
  return chat(storeFromEnv(), configFromEnv(), req);
}

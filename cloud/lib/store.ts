// Where the server keeps its few facts: who has a token, each account, and what was spent.
// Production uses Upstash Redis over its REST API (Vercel's marketplace "Upstash for Redis" sets
// KV_REST_API_URL and KV_REST_API_TOKEN); tests use the in-memory store.

export interface Store {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, ttlSeconds?: number): Promise<void>;
  /** Add to a number (cents as floats are fine) and return the new total. */
  incr(key: string, by: number, ttlSeconds?: number): Promise<number>;
  del(key: string): Promise<void>;
  /** Set only if the key doesn't exist yet; true when this call created it. */
  add(key: string, value: string, ttlSeconds?: number): Promise<boolean>;
}

export class MemoryStore implements Store {
  data = new Map<string, string>();
  async get(key: string) {
    return this.data.get(key) ?? null;
  }
  async set(key: string, value: string) {
    this.data.set(key, value);
  }
  async incr(key: string, by: number) {
    const next = Number(this.data.get(key) ?? 0) + by;
    this.data.set(key, String(next));
    return next;
  }
  async del(key: string) {
    this.data.delete(key);
  }
  async add(key: string, value: string) {
    if (this.data.has(key)) return false;
    this.data.set(key, value);
    return true;
  }
}

export class UpstashStore implements Store {
  private url: string;
  private token: string;
  constructor(url: string, token: string) {
    this.url = url;
    this.token = token;
  }

  private async call(...command: (string | number)[]): Promise<unknown> {
    const res = await fetch(this.url, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(command),
    });
    if (!res.ok) throw new Error(`store ${command[0]} failed: ${res.status}`);
    return ((await res.json()) as { result: unknown }).result;
  }

  async get(key: string) {
    return (await this.call("GET", key)) as string | null;
  }
  async set(key: string, value: string, ttlSeconds?: number) {
    await (ttlSeconds ? this.call("SET", key, value, "EX", ttlSeconds) : this.call("SET", key, value));
  }
  async incr(key: string, by: number, ttlSeconds?: number) {
    const total = Number(await this.call("INCRBYFLOAT", key, by));
    if (ttlSeconds) await this.call("EXPIRE", key, ttlSeconds);
    return total;
  }
  async del(key: string) {
    await this.call("DEL", key);
  }
  async add(key: string, value: string, ttlSeconds?: number) {
    const done = await (ttlSeconds ? this.call("SET", key, value, "NX", "EX", ttlSeconds) : this.call("SET", key, value, "NX"));
    return done !== null;
  }
}

export function storeFromEnv(env: Record<string, string | undefined> = process.env): Store {
  const url = env.KV_REST_API_URL ?? env.UPSTASH_REDIS_REST_URL;
  const token = env.KV_REST_API_TOKEN ?? env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) throw new Error("No store configured: add Upstash for Redis to the Vercel project.");
  return new UpstashStore(url, token);
}

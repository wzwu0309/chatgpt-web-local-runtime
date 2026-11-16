// dsh-mcp-gateway public facade for Cloudflare Workers.
//
// Deploy once:
//   1. Create Worker "dsh-gw" at workers.dev
//   2. Create KV namespace, bind it as  UPSTREAM
//   3. Add secret  ADMIN_SECRET  (any random string; keep it in env.conf too)
//
// The local start.sh publishes the current cloudflared quick-tunnel origin
// through PUT /_upstream (Bearer ADMIN_SECRET); every other request is
// proxied verbatim to that origin. OAuth issuer URLs stay self-consistent
// because the gateway is started with --public-base-url=<this worker URL>.
//
// Optional: attach a Custom Domain (Workers -> Settings -> Domains) if the
// launcher's network cannot reach *.workers.dev — then point WORKER_PUBLIC_URL
// at the custom domain instead of the workers.dev URL.
const ADMIN_PATH = "/_upstream";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!env.UPSTREAM) {
      return new Response("KV binding UPSTREAM is not configured on this Worker", { status: 500 });
    }

    if (url.pathname === ADMIN_PATH) {
      if (request.method !== "PUT") return new Response("method not allowed", { status: 405 });
      if (request.headers.get("authorization") !== `Bearer ${env.ADMIN_SECRET}`) {
        return new Response("forbidden", { status: 403 });
      }
      const upstream = (await request.text()).trim().replace(/\/+$/, "");
      if (!/^https:\/\/[a-z0-9.-]+$/.test(upstream)) {
        return new Response("bad upstream origin", { status: 400 });
      }
      await env.UPSTREAM.put("url", upstream);
      return Response.json({ ok: true, upstream });
    }

    const base = await env.UPSTREAM.get("url");
    if (!base) {
      return new Response("upstream not registered yet", { status: 503 });
    }

    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.delete("cf-connecting-ip");
    headers.delete("cf-ipcountry");
    headers.delete("cf-ray");
    headers.delete("cf-visitor");
    headers.delete("cdn-loop");

    const upstream = await fetch(base + url.pathname + url.search, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
    });
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: upstream.headers,
    });
  },
};

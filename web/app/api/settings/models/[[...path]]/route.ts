export const runtime = "nodejs";

async function forward(request: Request, { params }: { params: { path?: string[] } }) {
  const serverBase = process.env.PY_SERVER_URL || "http://localhost:8001";
  const path = (params.path || []).map(encodeURIComponent).join("/");
  try {
    const response = await fetch(`${serverBase.replace(/\/$/, "")}/api/v1/settings/models${path ? `/${path}` : ""}`, {
      method: request.method,
      headers: { "Content-Type": "application/json" },
      body: ["GET", "DELETE"].includes(request.method) ? undefined : await request.text(),
      cache: "no-store",
      signal: AbortSignal.timeout(30000),
    });
    return new Response(await response.text(), {
      status: response.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    return Response.json({ error: "Unable to reach the settings service. Try again." }, { status: 502 });
  }
}

export { forward as GET, forward as POST, forward as PUT, forward as DELETE };

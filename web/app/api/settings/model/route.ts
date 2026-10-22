export const runtime = "nodejs";

async function forward(request?: Request) {
  const serverBase = process.env.PY_SERVER_URL || "http://localhost:8001";
  try {
    const response = await fetch(`${serverBase.replace(/\/$/, "")}/api/v1/settings/model`, {
      method: request ? "PUT" : "GET",
      headers: { "Content-Type": "application/json" },
      body: request ? await request.text() : undefined,
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

export async function GET() {
  return forward();
}

export async function PUT(request: Request) {
  return forward(request);
}

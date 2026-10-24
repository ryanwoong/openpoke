export async function DELETE(_request: Request, { params }: { params: { requestId: string } }) {
  const serverBase = process.env.PY_SERVER_URL || "http://localhost:8001";
  try {
    const response = await fetch(`${serverBase.replace(/\/$/, "")}/api/v1/chat/requests/${encodeURIComponent(params.requestId)}`, { method: "DELETE", cache: "no-store", signal: AbortSignal.timeout(10000) });
    return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json" } });
  } catch {
    return Response.json({ error: "Could not reach the task service." }, { status: 502 });
  }
}

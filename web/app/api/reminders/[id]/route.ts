export async function PATCH(request: Request, { params }: { params: { id: string } }) {
  if (!/^\d+$/.test(params.id)) return Response.json({ error: "Invalid reminder ID" }, { status: 400 });
  const serverBase = process.env.PY_SERVER_URL || "http://localhost:8001";
  try {
    const response = await fetch(`${serverBase.replace(/\/$/, "")}/api/v1/reminders/${params.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: await request.text(), cache: "no-store" });
    return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ error: "Reminders cannot reach the server" }, { status: 502 });
  }
}

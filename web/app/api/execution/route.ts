export const dynamic = "force-dynamic";

export async function GET() {
  const serverBase = process.env.PY_SERVER_URL || "http://localhost:8001";
  try {
    const response = await fetch(`${serverBase.replace(/\/$/, "")}/api/v1/chat/execution`, { cache: "no-store" });
    return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ error: "Execution monitor cannot reach the server" }, { status: 502 });
  }
}

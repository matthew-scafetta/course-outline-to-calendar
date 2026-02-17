import { NextResponse } from "next/server";

export const runtime = "nodejs";

export async function POST(req: Request) {
  try {
    const base = process.env.FASTAPI_BASE_URL;

    if (!base) {
      return NextResponse.json(
        { error: "FASTAPI_BASE_URL is not set" },
        { status: 500 }
      );
    }

    // Read JSON body from frontend
    const events = await req.json();

    if (!Array.isArray(events)) {
      return NextResponse.json(
        { error: "Invalid events payload" },
        { status: 400 }
      );
    }

    // Forward JSON to FastAPI
    const res = await fetch(`${base}/calendar-from-events`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(events),
    });

    // Forward ICS bytes directly back to browser
    const bytes = await res.arrayBuffer();

    const filename =
      res.headers
        .get("content-disposition")
        ?.match(/filename="?([^"]+)"?/i)?.[1] ||
      "course_schedule.ics";

    return new NextResponse(bytes, {
      status: res.status,
      headers: {
        "content-type": res.headers.get("content-type") || "text/calendar",
        "content-disposition": `attachment; filename="${filename}"`,
      },
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: err?.message || "Proxy error" },
      { status: 500 }
    );
  }
}

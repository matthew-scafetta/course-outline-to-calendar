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

    const incoming = await req.formData();
    const file = incoming.get("file");

    if (!file || !(file instanceof File)) {
      return NextResponse.json(
        { error: "Missing 'file' in multipart/form-data" },
        { status: 400 }
      );
    }

    const out = new FormData();
    out.append("file", file, file.name);

    const res = await fetch(`${base}/upload-calendar`, {
      method: "POST",
      body: out,
    });

    // Forward the ICS bytes as-is
    const bytes = await res.arrayBuffer();

    const filename =
      res.headers
        .get("content-disposition")
        ?.match(/filename="?([^"]+)"?/i)?.[1] || "course_calendar.ics";

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

import { NextResponse } from "next/server";

export const runtime = "nodejs"; // needed for FormData + file forwarding

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

    const res = await fetch(`${base}/upload-json`, {
      method: "POST",
      body: out,
    });

    const contentType = res.headers.get("content-type") || "";
    const body = contentType.includes("application/json")
      ? await res.json()
      : await res.text();

    return NextResponse.json(body, { status: res.status });
  } catch (err: any) {
    return NextResponse.json(
      { error: err?.message || "Proxy error" },
      { status: 500 }
    );
  }
}

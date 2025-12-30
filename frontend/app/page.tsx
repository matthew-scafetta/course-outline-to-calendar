"use client";

import { useMemo, useRef, useState } from "react";

type CourseEvent = {
  date: string | null;
  title: string;
  description?: string | null;
  event_type: string;
  time?: string | null;
  recurrence?: string | null;
  byday?: string[] | null;
  until?: string | null;
};

type ParseResponse = {
  events: CourseEvent[];
  success: boolean;
  message: string;
};

function formatEvent(ev: CourseEvent) {
  const date = ev.date ?? "—";
  const time = ev.time ? ` · ${ev.time}` : "";
  const type = ev.event_type ? ev.event_type.toUpperCase() : "OTHER";
  return `${date}${time} · ${type}`;
}

export default function Page() {
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [data, setData] = useState<ParseResponse | null>(null);
  const [showRaw, setShowRaw] = useState(false);

  const unresolvedCount = useMemo(() => {
    if (!data?.events) return 0;
    return data.events.filter((e) => !e.date).length;
  }, [data]);

  function reset() {
    setFile(null);
    setData(null);
    setStatus("");
    setError("");
    setShowRaw(false);
    if (fileRef.current) fileRef.current.value = "";
  }

  async function uploadJson() {
    try {
      setError("");
      setStatus("");
      setData(null);
      setShowRaw(false);

      if (!file) throw new Error("Select a PDF or image first.");

      setBusy(true);
      setStatus("Uploading… extracting events…");

      const form = new FormData();
      form.append("file", file);

      const res = await fetch("/api/upload-json", {
        method: "POST",
        body: form,
      });

      const body = await res.json();
      if (!res.ok) {
        // proxy returns json on errors too
        throw new Error(body?.error || JSON.stringify(body));
      }

      const parsed = body as ParseResponse;
      setData(parsed);
      setStatus(`Extracted ${parsed.events?.length ?? 0} events.`);
    } catch (e: any) {
      setError(e?.message || "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function downloadIcs() {
    try {
      setError("");
      setStatus("");

      if (!file) throw new Error("Select a PDF or image first.");

      setBusy(true);
      setStatus("Uploading… generating calendar…");

      const form = new FormData();
      form.append("file", file);

      const res = await fetch("/api/upload-calendar", {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        // try to parse error json/text
        const ct = res.headers.get("content-type") || "";
        const text = ct.includes("application/json")
          ? JSON.stringify(await res.json())
          : await res.text();
        throw new Error(text || `Download failed (${res.status})`);
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);

      const a = document.createElement("a");
      a.href = url;
      a.download = `${file.name}_calendar.ics`;
      document.body.appendChild(a);
      a.click();
      a.remove();

      URL.revokeObjectURL(url);
      setStatus("Downloaded calendar (.ics). Import it into Google Calendar.");
    } catch (e: any) {
      setError(e?.message || "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#0b0b0f] text-white">
      {/* soft background glow */}
      <div className="pointer-events-none fixed inset-0 opacity-60">
        <div className="absolute -top-40 left-1/2 h-[520px] w-[820px] -translate-x-1/2 rounded-full bg-white/10 blur-3xl" />
        <div className="absolute top-24 left-1/3 h-[260px] w-[260px] rounded-full bg-white/5 blur-3xl" />
      </div>

      <div className="relative mx-auto max-w-5xl px-6 py-12">
        {/* header */}
        <header className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-2xl border border-white/10 bg-white/5 shadow-sm">
              <span className="text-lg"></span>
            </div>
            <div>
              <div className="text-sm text-white/60">Course Outline Parser</div>
              <div className="text-lg font-semibold tracking-tight">
                Import your syllabus
              </div>
            </div>
          </div>

          <a
            href="/"
            onClick={(e) => {
              e.preventDefault();
              reset();
            }}
            className="text-sm text-white/60 hover:text-white transition"
          >
            Reset
          </a>
        </header>

        {/* hero */}
        <section className="mt-10 grid gap-6 md:grid-cols-[1.25fr_0.75fr]">
          <div className="rounded-3xl border border-white/10 bg-white/[0.06] p-6 shadow-[0_12px_40px_rgba(0,0,0,0.35)] backdrop-blur-xl">
            <h1 className="text-3xl font-semibold tracking-tight">
              Upload a PDF or image.
              <span className="text-white/70"> Get events in seconds.</span>
            </h1>

            <p className="mt-3 text-sm leading-6 text-white/65">
              Preview extracted events (JSON) or download an Apple Calendar /
              Google Calendar compatible <span className="text-white/80">.ics</span>{" "}
              file. Uses your Next.js proxy routes (no direct browser-to-FastAPI
              calls).
            </p>

            {/* file picker */}
            <div className="mt-6">
              <label className="block text-xs text-white/60 mb-2">
                Course outline file
              </label>

              <div className="flex flex-col gap-3 rounded-2xl border border-white/10 bg-black/20 p-4">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".pdf,.png,.jpg,.jpeg"
                  disabled={busy}
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    setData(null);
                    setStatus("");
                    setError("");
                    setShowRaw(false);
                  }}
                  className="block w-full text-sm file:mr-4 file:rounded-xl file:border-0 file:bg-white/10 file:px-4 file:py-2 file:font-semibold file:text-white hover:file:bg-white/15 file:transition"
                />

                <div className="flex flex-wrap gap-3">
                  <button
                    onClick={uploadJson}
                    disabled={busy || !file}
                    className="rounded-2xl border border-white/10 bg-white/10 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-white/15 disabled:opacity-40"
                  >
                    {busy ? "Working…" : "Extract JSON"}
                  </button>

                  <button
                    onClick={downloadIcs}
                    disabled={busy || !file}
                    className="rounded-2xl border border-white/10 bg-white/10 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-white/15 disabled:opacity-40"
                  >
                    Download .ics
                  </button>

                  <button
                    onClick={reset}
                    disabled={busy}
                    className="rounded-2xl border border-white/10 bg-transparent px-4 py-2 text-sm font-semibold text-white/80 transition hover:bg-white/5 disabled:opacity-40"
                  >
                    Clear
                  </button>
                </div>

                {/* status */}
                {status && (
                  <div className="text-sm text-emerald-300">{status}</div>
                )}
                {error && <div className="text-sm text-rose-300">{error}</div>}
              </div>
            </div>
          </div>

          {/* side card */}
          <div className="rounded-3xl border border-white/10 bg-white/[0.05] p-6 backdrop-blur-xl">
            <div className="text-xs text-white/60">Tips</div>
            <div className="mt-2 text-sm text-white/70 leading-6">
              <ul className="list-disc pl-5 space-y-2">
                <li>
                  Best results come from a clear PDF (not a blurry scan).
                </li>
                <li>
                  If the outline uses “Week 3” dates, include “Classes start
                  Jan X” somewhere in the document.
                </li>
                <li>
                  If a date cannot be resolved, it will show as “—” in the
                  preview.
                </li>
              </ul>
            </div>

            <div className="mt-5 rounded-2xl border border-white/10 bg-black/20 p-4">
              <div className="text-xs text-white/60">Backend</div>
              <div className="mt-1 text-sm text-white/80">
                Next.js proxies requests to FastAPI.
              </div>
              <div className="mt-3 text-xs text-white/60">Endpoints</div>
              <div className="mt-1 text-xs text-white/75">
                <div>/api/upload-json → FastAPI /upload-json</div>
                <div>/api/upload-calendar → FastAPI /upload-calendar</div>
              </div>
            </div>
          </div>
        </section>

        {/* results */}
        {data && (
          <section className="mt-8 rounded-3xl border border-white/10 bg-white/[0.06] p-6 shadow-[0_12px_40px_rgba(0,0,0,0.35)] backdrop-blur-xl">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm text-white/60">Results</div>
                <div className="text-xl font-semibold tracking-tight">
                  {data.events.length} events extracted
                </div>
              </div>

              <div className="flex items-center gap-3">
                {unresolvedCount > 0 && (
                  <div className="rounded-full border border-amber-400/20 bg-amber-400/10 px-3 py-1 text-xs text-amber-200">
                    {unresolvedCount} unresolved (date=null)
                  </div>
                )}

                <button
                  onClick={() => setShowRaw((v) => !v)}
                  className="rounded-2xl border border-white/10 bg-white/10 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/15"
                >
                  {showRaw ? "Hide Raw JSON" : "Show Raw JSON"}
                </button>
              </div>
            </div>

            {/* table */}
            <div className="mt-5 overflow-hidden rounded-2xl border border-white/10">
              <table className="min-w-full text-sm">
                <thead className="bg-white/[0.05] text-white/70">
                  <tr>
                    <th className="p-3 text-left font-semibold">When</th>
                    <th className="p-3 text-left font-semibold">Title</th>
                    <th className="p-3 text-left font-semibold">Type</th>
                  </tr>
                </thead>
                <tbody>
                  {data.events.map((ev, idx) => (
                    <tr key={idx} className="border-t border-white/10">
                      <td className="p-3 text-white/80">{formatEvent(ev)}</td>
                      <td className="p-3">
                        <div className="font-semibold text-white/90">
                          {ev.title}
                        </div>
                        {ev.description && (
                          <div className="mt-0.5 text-xs text-white/60">
                            {ev.description}
                          </div>
                        )}
                      </td>
                      <td className="p-3 text-white/70">{ev.event_type}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* raw json */}
            {showRaw && (
              <pre className="mt-5 max-h-[420px] overflow-auto rounded-2xl border border-white/10 bg-black/30 p-4 text-xs text-white/80">
                {JSON.stringify(data, null, 2)}
              </pre>
            )}
          </section>
        )}

        {/* footer */}
        <footer className="mt-10 text-center text-xs text-white/45">
          Built with Next.js (proxy routes) + FastAPI (Python)
        </footer>
      </div>
    </main>
  );
}

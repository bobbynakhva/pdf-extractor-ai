"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";

type Bbox = { x0: number; y0: number; x1: number; y1: number };
type Annotation = {
  kind: string;
  color?: string | null;
  text?: string | null;
  contents?: string | null;
  bbox?: Bbox | null;
  page: number;
};
type ExtractedImage = {
  page: number;
  index: number;
  ext: string;
  width: number;
  height: number;
  bbox?: Bbox | null;
  data_url: string;
};
type ExtractedTable = {
  page: number;
  rows: string[][];
  confidence: number;
  flagged: boolean;
  note?: string | null;
};
type PageBlock = {
  kind: string;
  content: string;
  html?: string | null;
  table?: ExtractedTable | null;
  image?: ExtractedImage | null;
  annotation?: Annotation | null;
  confidence: number;
  flagged: boolean;
  note?: string | null;
};
type Page = {
  number: number;
  width: number;
  height: number;
  blocks: PageBlock[];
  plain_text: string;
  used_ocr: boolean;
  layout_html?: string;
  render_png?: string;
  render_width?: number;
  render_height?: number;
};
type ExtractionResult = {
  filename: string;
  page_count: number;
  metadata: Record<string, string>;
  pages: Page[];
  images: ExtractedImage[];
  tables: ExtractedTable[];
  annotations: Annotation[];
  engines_used: string[];
  warnings: string[];
  plain_text: string;
  sha256: string;
  pdf_sha256?: string;
  overall_confidence: number;
  layout_html?: string;
};
type ExtractResponse = { result: ExtractionResult; raw_markdown: string };

function resolveApiBase(): string {
  const env = process.env.NEXT_PUBLIC_API_BASE;
  if (env) return env.replace(/\/+$/, "");
  if (typeof window === "undefined") return "";
  const h = window.location.hostname;
  if (h === "localhost" || h === "127.0.0.1" || h === "0.0.0.0") {
    return "http://localhost:8000";
  }
  return "";
}
const API_BASE = resolveApiBase();

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [filePreviewUrl, setFilePreviewUrl] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [result, setResult] = useState<ExtractResponse | null>(null);
  const [mode, setMode] = useState<"layout" | "raw">("layout");
  const [showOriginal, setShowOriginal] = useState(true);
  const [zoom, setZoom] = useState(16);
  const [search, setSearch] = useState("");
  const [dark, setDark] = useState(false);
  const outputRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const cls = document.documentElement.classList;
    if (dark) cls.add("dark");
    else cls.remove("dark");
  }, [dark]);

  const onFiles = useCallback((files: FileList | null) => {
    if (!files || files.length === 0) return;
    const f = files[0];
    if (!f.name.toLowerCase().endsWith(".pdf")) {
      setStatus("Please select a .pdf file");
      return;
    }
    setFile(f);
    if (filePreviewUrl) URL.revokeObjectURL(filePreviewUrl);
    setFilePreviewUrl(URL.createObjectURL(f));
    setStatus(`Selected ${f.name} (${(f.size / 1024 / 1024).toFixed(2)} MB)`);
    setResult(null);
    setMode("layout");
  }, [filePreviewUrl]);

  const extractNow = useCallback(async () => {
    if (!file) return;
    setUploading(true);
    setStatus("Uploading and extracting…");
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${API_BASE}/extract`, {
        method: "POST",
        body: fd,
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(txt || `Extraction failed (${r.status})`);
      }
      const data = (await r.json()) as ExtractResponse;
      setResult(data);
      setStatus(
        `Extracted ${data.result.page_count} page${
          data.result.page_count === 1 ? "" : "s"
        } using ${data.result.engines_used.join(" + ")}. ` +
          `${data.result.tables.length} table(s), ${data.result.images.length} image(s), ${data.result.annotations.length} annotation(s).`
      );
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setStatus(`Error: ${msg}`);
    } finally {
      setUploading(false);
    }
  }, [file]);

  const currentMarkdown = useMemo(() => {
    if (!result) return "";
    return result.raw_markdown;
  }, [result]);

  const renderedHtml = useMemo(() => {
    if (!result) return "";
    if (mode === "layout") return ""; // layout renders from pages[], not HTML
    marked.setOptions({ breaks: false, gfm: true });
    const rawHtml = marked.parse(currentMarkdown, { async: false }) as string;
    let html = DOMPurify.sanitize(rawHtml, {
      ADD_ATTR: ["target", "rel"],
      ADD_TAGS: ["u"],
    });
    if (search.trim().length > 1) {
      const s = search.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&");
      const re = new RegExp(`(${s})`, "gi");
      // Highlight only outside of tags
      html = html.replace(
        />([^<]+)</g,
        (_, inner) => `>${inner.replace(re, "<mark class='search-hit'>$1</mark>")}<`
      );
    }
    return html;
  }, [result, mode, currentMarkdown, search]);

  const layoutPages = useMemo(() => {
    if (!result || mode !== "layout") return null;
    return result.result.pages.filter((p) => p.render_png);
  }, [result, mode]);

  const copyAll = useCallback(async () => {
    if (!currentMarkdown) return;
    try {
      await navigator.clipboard.writeText(currentMarkdown);
      setStatus("Copied to clipboard.");
    } catch {
      setStatus("Clipboard copy failed — select and copy manually.");
    }
  }, [currentMarkdown]);

  const download = useCallback(
    async (format: "md" | "html" | "txt") => {
      if (!currentMarkdown || !result) return;
      const base = result.result.filename.replace(/\.pdf$/i, "");
      const r = await fetch(`${API_BASE}/download`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          content: currentMarkdown,
          format,
          filename: base,
        }),
      });
      if (!r.ok) {
        setStatus(`Download failed: ${r.status}`);
        return;
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${base}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
    [currentMarkdown, result]
  );

  const [docxLoading, setDocxLoading] = useState<"editable" | "layout" | null>(null);
  const downloadDocx = useCallback(
    async (engineMode: "editable" | "layout") => {
      if (!result?.result.pdf_sha256) return;
      const base = result.result.filename.replace(/\.pdf$/i, "") || "extracted";
      const sha = result.result.pdf_sha256;
      setDocxLoading(engineMode);
      setStatus(
        engineMode === "editable"
          ? "Queuing editable Word conversion…"
          : "Queuing layout Word conversion…"
      );
      try {
        const startRes = await fetch(
          `${API_BASE}/export-docx/${sha}/jobs?engine=${engineMode}`,
          { method: "POST" }
        );
        if (!startRes.ok) {
          const txt = await startRes.text();
          setStatus(`Word export failed: ${txt || startRes.status}`);
          return;
        }
        const startJson: {
          job_id: string;
          state: string;
          phase: string;
          progress: number;
          total: number;
          error?: string | null;
        } = await startRes.json();

        const jobId = startJson.job_id;
        const startedAt = Date.now();
        // Poll for up to 20 minutes. Each poll returns the same shape as
        // the POST response, and the /result endpoint returns 202 until
        // the job is done then 200 with the docx bytes.
        const POLL_MS = 1500;
        const MAX_MS = 20 * 60 * 1000;
        let lastSeenProgress = -1;
        while (true) {
          if (Date.now() - startedAt > MAX_MS) {
            setStatus("Word export timed out after 20 min. Try splitting the PDF.");
            return;
          }
          await new Promise((r) => setTimeout(r, POLL_MS));
          const statusRes = await fetch(`${API_BASE}/export-docx/jobs/${jobId}`);
          if (!statusRes.ok) {
            const txt = await statusRes.text();
            setStatus(`Word export failed: ${txt || statusRes.status}`);
            return;
          }
          const js: {
            state: string;
            phase: string;
            progress: number;
            total: number;
            error?: string | null;
          } = await statusRes.json();
          if (js.state === "error") {
            setStatus(`Word export failed: ${js.error || "unknown error"}`);
            return;
          }
          if (js.progress !== lastSeenProgress || js.state !== "running") {
            lastSeenProgress = js.progress;
            const pct =
              js.total > 0
                ? ` (${Math.round((js.progress / js.total) * 100)}%)`
                : "";
            const phaseLabel =
              js.phase === "parsing"
                ? "Parsing"
                : js.phase === "writing"
                  ? "Writing"
                  : js.phase === "analyzing"
                    ? "Analyzing"
                    : js.phase === "done"
                      ? "Finalizing"
                      : js.state;
            setStatus(
              `${phaseLabel} ${engineMode === "editable" ? "editable" : "layout"} Word doc${
                js.total ? ` — page ${js.progress}/${js.total}${pct}` : `…${pct}`
              }`
            );
          }
          if (js.state === "done") break;
        }

        // Download via direct anchor click rather than fetch()+blob().
        // For large DOCX files (>10 MB) the blob path can fail with
        // "Failed to fetch" on memory-tight tabs or proxies that don't
        // play well with JS-buffered downloads. The browser's native
        // downloader handles streaming + Content-Disposition reliably.
        const suffix = engineMode === "layout" ? "-layout" : "";
        const dlUrl = `${API_BASE}/export-docx/jobs/${jobId}/result?filename=${encodeURIComponent(base)}`;
        const a = document.createElement("a");
        a.href = dlUrl;
        a.download = `${base}${suffix}.docx`;
        a.rel = "noopener";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setStatus(
          engineMode === "editable"
            ? `Saved ${base}.docx — text, tables and images are selectable and editable in Word or Google Docs.`
            : `Saved ${base}-layout.docx — pixel-perfect layout. Text lives in frames so it's not freely editable; use the editable mode for editing.`
        );
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        setStatus(`Word export failed: ${msg}`);
      } finally {
        setDocxLoading(null);
      }
    },
    [result]
  );

  return (
    <main className="min-h-screen">
      {/* Header */}
      <header className="px-6 py-4 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between sticky top-0 bg-slate-50/90 dark:bg-slate-950/90 backdrop-blur z-10">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            📄 PDF Extractor AI
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Zero hallucinations. Every character preserved.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs">
            <span className="text-slate-500">Zoom</span>
            <input
              type="range"
              min={12}
              max={24}
              value={zoom}
              onChange={(e) => setZoom(parseInt(e.target.value))}
            />
          </label>
          <button
            onClick={() => setDark((d) => !d)}
            className="text-xs px-3 py-1.5 rounded-md border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            {dark ? "☀️ Light" : "🌙 Dark"}
          </button>
        </div>
      </header>

      <div className="px-6 py-6 max-w-[1600px] mx-auto">
        {/* Upload area */}
        {!result && (
          <section
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              onFiles(e.dataTransfer.files);
            }}
            className={`rounded-xl border-2 border-dashed p-10 text-center transition-colors ${
              dragOver
                ? "border-blue-500 bg-blue-50 dark:bg-blue-900/20"
                : "border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900"
            }`}
          >
            <div className="text-5xl mb-3">📥</div>
            <p className="text-lg font-medium mb-1">
              Drag & drop a PDF here
            </p>
            <p className="text-sm text-slate-500 mb-4">
              or click to browse — any size, any language
            </p>
            <label className="inline-block cursor-pointer px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
              Choose PDF
              <input
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                onChange={(e) => onFiles(e.target.files)}
              />
            </label>
            {file && (
              <div className="mt-4 text-sm">
                <strong>{file.name}</strong> · {(file.size / 1024 / 1024).toFixed(2)} MB
                <div className="mt-3">
                  <button
                    disabled={uploading}
                    onClick={extractNow}
                    className="px-5 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-60"
                  >
                    {uploading ? "Extracting…" : "Extract Now"}
                  </button>
                </div>
              </div>
            )}
          </section>
        )}

        {/* Status bar */}
        {status && (
          <div className="mt-4 text-xs text-slate-600 dark:text-slate-400">
            {status}
          </div>
        )}

        {/* Results */}
        {result && (
          <>
            {/* Control bar */}
            <div className="mt-2 mb-4 flex flex-wrap gap-3 items-center">
              <div className="flex items-center rounded-lg border border-slate-300 dark:border-slate-700 overflow-hidden">
                <button
                  onClick={() => setMode("layout")}
                  disabled={!result.result.pages.some((p) => p.render_png)}
                  title={
                    result.result.pages.some((p) => p.render_png)
                      ? "Pixel-perfect PDF rendering with highlight overlays"
                      : "Page rendering unavailable (fallback extractor used)"
                  }
                  className={`px-3 py-1.5 text-sm disabled:opacity-40 ${
                    mode === "layout"
                      ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                      : ""
                  }`}
                >
                  🖼 PDF Layout
                </button>
                <button
                  onClick={() => setMode("raw")}
                  className={`px-3 py-1.5 text-sm border-l border-slate-300 dark:border-slate-700 ${
                    mode === "raw"
                      ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                      : ""
                  }`}
                >
                  📄 Raw MD
                </button>
              </div>
              <button
                onClick={() => setShowOriginal((v) => !v)}
                className="text-sm px-3 py-1.5 rounded-md border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                {showOriginal ? "Hide original PDF" : "Show original PDF"}
              </button>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="🔍 Search extracted content…"
                className="px-3 py-1.5 rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-sm min-w-[220px]"
              />
              <button
                onClick={copyAll}
                className="text-sm px-3 py-1.5 rounded-md border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                📋 Copy all
              </button>
              <div className="flex items-center rounded-lg border border-slate-300 dark:border-slate-700 overflow-hidden">
                <button
                  onClick={() => download("md")}
                  className="px-3 py-1.5 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
                >
                  ⬇︎ MD
                </button>
                <button
                  onClick={() => download("html")}
                  className="px-3 py-1.5 text-sm border-l border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
                >
                  ⬇︎ HTML
                </button>
                <button
                  onClick={() => download("txt")}
                  className="px-3 py-1.5 text-sm border-l border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
                >
                  ⬇︎ TXT
                </button>
              </div>
              <div className="flex items-center rounded-lg border border-blue-300 dark:border-blue-800 overflow-hidden bg-blue-50 dark:bg-blue-950">
                <button
                  onClick={() => downloadDocx("editable")}
                  disabled={!!docxLoading || !result?.result.pdf_sha256}
                  title="Editable Word document — flowing paragraphs and real Word tables, fully selectable and editable in Word / Google Docs."
                  className="px-3 py-1.5 text-sm text-blue-700 hover:bg-blue-100 dark:text-blue-300 dark:hover:bg-blue-900 disabled:opacity-60"
                >
                  {docxLoading === "editable"
                    ? "⏳ Converting…"
                    : "📝 Word (editable)"}
                </button>
                <button
                  onClick={() => downloadDocx("layout")}
                  disabled={!!docxLoading || !result?.result.pdf_sha256}
                  title="Layout Word document — pixel-perfect match of the PDF. Text lives in frames, so it's a print replica, not freely editable."
                  className="px-3 py-1.5 text-sm border-l border-blue-300 dark:border-blue-800 text-blue-700 hover:bg-blue-100 dark:text-blue-300 dark:hover:bg-blue-900 disabled:opacity-60"
                >
                  {docxLoading === "layout"
                    ? "⏳ Converting…"
                    : "🖼 Word (layout)"}
                </button>
              </div>
              <button
                onClick={() => {
                  setFile(null);
                  setResult(null);
                  setStatus("");
                }}
                className="ml-auto text-sm px-3 py-1.5 rounded-md border border-slate-300 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                Upload another
              </button>
            </div>

            {result.result.warnings.length > 0 && (
              <div className="mb-3 rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-900/20 dark:border-amber-700 p-3 text-xs">
                <strong>⚠️ Extraction flags</strong>
                <ul className="list-disc ml-5 mt-1">
                  {result.result.warnings.slice(0, 10).map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Main view */}
            <div
              className={`grid gap-4 ${
                showOriginal && filePreviewUrl
                  ? "grid-cols-1 lg:grid-cols-2"
                  : "grid-cols-1"
              }`}
            >
              {showOriginal && filePreviewUrl && (
                <div className="rounded-lg border border-slate-200 dark:border-slate-800 overflow-hidden bg-white dark:bg-slate-900 h-[calc(100vh-260px)] min-h-[500px]">
                  <object
                    data={filePreviewUrl}
                    type="application/pdf"
                    className="w-full h-full"
                  >
                    <p className="p-4 text-sm">
                      PDF preview unavailable.{" "}
                      <a
                        className="text-blue-600 underline"
                        href={filePreviewUrl}
                      >
                        Open PDF
                      </a>
                    </p>
                  </object>
                </div>
              )}
              <div
                ref={outputRef}
                className="rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-100 dark:bg-slate-950 overflow-auto h-[calc(100vh-260px)] min-h-[500px]"
              >
                {mode === "layout" && layoutPages ? (
                  <div className="pdf-doc">
                    {layoutPages.map((p) => (
                      <div
                        key={p.number}
                        className="pdf-page"
                        style={{
                          position: "relative",
                          width: `${p.width}pt`,
                          height: `${p.height}pt`,
                        }}
                      >
                        {p.render_png && (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={
                              p.render_png.startsWith("http") ||
                              p.render_png.startsWith("data:")
                                ? p.render_png
                                : `${API_BASE}${p.render_png}`
                            }
                            alt={`Page ${p.number}`}
                            width={p.render_width}
                            height={p.render_height}
                            loading="lazy"
                            decoding="async"
                            style={{
                              width: "100%",
                              height: "100%",
                              display: "block",
                            }}
                          />
                        )}
                        {result.result.annotations
                          .filter(
                            (a) =>
                              a.page === p.number &&
                              a.bbox &&
                              [
                                "highlight",
                                "underline",
                                "squiggly",
                                "strikeout",
                              ].includes(a.kind)
                          )
                          .map((a, i) => {
                            const b = a.bbox!;
                            const w = Math.max(0, b.x1 - b.x0);
                            const h = Math.max(0, b.y1 - b.y0);
                            const color = a.color || "#ffff00";
                            const base: React.CSSProperties = {
                              position: "absolute",
                              left: `${b.x0}pt`,
                              top: `${b.y0}pt`,
                              width: `${w}pt`,
                              height: `${h}pt`,
                              pointerEvents: "none",
                            };
                            const style: React.CSSProperties =
                              a.kind === "highlight"
                                ? {
                                    ...base,
                                    background: color,
                                    opacity: 0.35,
                                    mixBlendMode: "multiply",
                                    borderRadius: 1,
                                  }
                                : a.kind === "underline"
                                ? {
                                    ...base,
                                    top: `${b.y1 - 1}pt`,
                                    height: "1.2pt",
                                    background: color,
                                  }
                                : a.kind === "strikeout"
                                ? {
                                    ...base,
                                    top: `${(b.y0 + b.y1) / 2}pt`,
                                    height: "1.2pt",
                                    background: color,
                                  }
                                : {
                                    ...base,
                                    top: `${b.y1 - 2}pt`,
                                    height: "2pt",
                                    borderBottom: `1.2pt wavy ${color}`,
                                  };
                            return (
                              <div
                                key={i}
                                className={`pdf-annot pdf-annot-${a.kind}`}
                                title={a.text || a.contents || a.kind}
                                style={style}
                              />
                            );
                          })}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="p-6">
                    <div
                      className="prose-pdf"
                      style={{ fontSize: `${zoom}px` }}
                      dangerouslySetInnerHTML={{ __html: renderedHtml }}
                    />
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </main>
  );
}



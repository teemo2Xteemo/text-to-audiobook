import { useEffect, useState } from "react";

import { getChapters } from "../api/client";
import type { ChapterAudioStatus, ChapterListItem, JobStatusResponse } from "../api/types";
import { apiBaseUrl } from "../lib/env";
import { formatBytes, formatClock, playerSrc } from "../lib/jobUi";

const PAGE_SIZE = 100;

type ChapterListProps = {
  job: JobStatusResponse;
};

type Filter = "all" | ChapterAudioStatus;

export function ChapterList({ job }: ChapterListProps) {
  const total = job.chapters_total ?? 0;
  const ready = job.chapters_ready ?? 0;
  const failed = job.chapters_failed ?? 0;
  const [items, setItems] = useState<ChapterListItem[]>([]);
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [matched, setMatched] = useState(total);
  const [playing, setPlaying] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handle = window.setTimeout(() => setAppliedQuery(query.trim()), 300);
    return () => window.clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    setPage(0);
  }, [filter, appliedQuery, job.job_id]);

  useEffect(() => {
    if (total <= 1 || !job.chapters_url) {
      return;
    }
    let cancelled = false;
    void getChapters(job.job_id, {
      offset: page * PAGE_SIZE,
      limit: PAGE_SIZE,
      status: filter === "all" ? undefined : filter,
      q: appliedQuery || undefined,
    })
      .then((response) => {
        if (!cancelled) {
          setItems(response.items);
          setMatched(response.matched);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("Could not load chapters.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [job.job_id, job.chapters_url, total, ready, failed, page, filter, appliedQuery]);

  if (total <= 1) {
    return null;
  }

  const partial = ready < total;
  const zipHref = job.zip_url ? playerSrc(apiBaseUrl(), job.zip_url) : null;
  const pageCount = Math.max(1, Math.ceil(matched / PAGE_SIZE));

  return (
    <section className="panel chapter-panel" aria-label="Chapters">
      <div className="panel-header">
        <div>
          <span className="section-eyebrow">Chapters</span>
          <h2>
            {ready} / {total} ready
            {failed > 0 ? ` · ${failed} failed` : ""}
          </h2>
        </div>
        {zipHref ? (
          <a className="btn-download" href={zipHref} download>
            {partial ? `Download ready chapters (${ready}/${total}) as ZIP` : "Download all (ZIP)"}
          </a>
        ) : null}
      </div>

      <div className="chapter-toolbar">
        <label>
          Filter
          <select
            aria-label="Filter chapters"
            value={filter}
            onChange={(event) => setFilter(event.target.value as Filter)}
          >
            <option value="all">All</option>
            <option value="ready">Ready</option>
            <option value="failed">Failed</option>
            <option value="pending">Pending</option>
            <option value="generating">Generating</option>
          </select>
        </label>
        <label>
          Search
          <input
            aria-label="Search chapters"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Title or number"
          />
        </label>
      </div>

      {error ? (
        <p role="alert" className="alert-banner alert-error">
          {error}
        </p>
      ) : null}

      <div className="chapter-table-wrap">
        <table className="chapter-table">
          <caption className="sr-only">Chapter audio</caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Title</th>
              <th scope="col">Duration</th>
              <th scope="col">Size</th>
              <th scope="col">Status</th>
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const href = item.download_url
                ? playerSrc(apiBaseUrl(), `${item.download_url}?download=1`)
                : null;
              const playHref = item.download_url
                ? playerSrc(apiBaseUrl(), item.download_url)
                : null;
              return (
                <tr key={item.ordinal}>
                  <td>{String(item.ordinal).padStart(4, "0")}</td>
                  <td>{item.title || item.title_source || `Chapter ${item.ordinal}`}</td>
                  <td>{item.duration_ms ? formatClock(item.duration_ms) : "—"}</td>
                  <td>{item.size_bytes ? formatBytes(item.size_bytes) : "—"}</td>
                  <td>
                    {item.status}
                    {item.tts_engine ? ` · ${item.tts_engine}` : ""}
                  </td>
                  <td className="chapter-actions">
                    {playHref ? (
                      <button
                        type="button"
                        className="btn-download"
                        onClick={() => setPlaying(playHref)}
                      >
                        Play
                      </button>
                    ) : null}
                    {href ? (
                      <a className="btn-download" href={href} download={item.filename}>
                        Download
                      </a>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {pageCount > 1 ? (
        <div className="chapter-pager">
          <button type="button" disabled={page === 0} onClick={() => setPage((current) => current - 1)}>
            Previous
          </button>
          <span>
            Page {page + 1} / {pageCount}
          </span>
          <button
            type="button"
            disabled={page + 1 >= pageCount}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
          </button>
        </div>
      ) : null}

      {playing ? (
        <audio controls src={playing} aria-label="Chapter audio" />
      ) : null}
    </section>
  );
}

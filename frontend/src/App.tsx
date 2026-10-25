import { AudioPlayer } from "./components/AudioPlayer";
import { JobForm } from "./components/JobForm";
import { JobProgress } from "./components/JobProgress";
import { useCapabilities } from "./hooks/useCapabilities";
import { jobFromView, useJob } from "./hooks/useJob";
import { apiBaseUrl } from "./lib/env";
import { playerSrc } from "./lib/jobUi";

export default function App() {
  const capabilities = useCapabilities();
  const job = useJob();
  const busy = job.status === "loading";
  const currentJob = jobFromView(job);
  const audioUrl = job.status === "success" ? job.job.audio_url : null;

  return (
    <div className="page">
      <header>
        <h1>Story to audiobook</h1>
        <p className="lede">
          Paste or upload a story, choose languages from the live API, and generate narration
          audio.
        </p>
      </header>
      {capabilities.status === "loading" || capabilities.status === "idle" ? (
        <p>Loading languages and voices…</p>
      ) : null}
      {capabilities.status === "error" ? (
        <p role="alert">
          {capabilities.error.error_type}: {capabilities.error.message}
        </p>
      ) : null}
      {capabilities.status === "success" ? (
        <JobForm
          capabilities={capabilities.data}
          disabled={busy}
          onSubmit={(input) => void job.submit(input)}
        />
      ) : null}
      {currentJob ? <JobProgress job={currentJob} /> : null}
      {job.status === "error" && !job.job ? (
        <p role="alert">
          {job.error.error_type}: {job.error.message}
        </p>
      ) : null}
      {audioUrl ? <AudioPlayer src={playerSrc(apiBaseUrl(), audioUrl)} /> : null}
    </div>
  );
}

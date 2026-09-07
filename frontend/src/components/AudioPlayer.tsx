import { useEffect, useState } from "react";

type AudioPlayerProps = {
  src: string;
};

export function AudioPlayer({ src }: AudioPlayerProps) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [src]);

  return (
    <section className="panel">
      <h2>Audiobook</h2>
      <audio
        controls
        src={src}
        aria-label="Generated audiobook"
        onError={() => setFailed(true)}
      />
      {failed ? (
        <p role="alert">The browser could not play this audio file.</p>
      ) : null}
    </section>
  );
}

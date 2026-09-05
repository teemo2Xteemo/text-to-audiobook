type AudioPlayerProps = {
  src: string;
};

export function AudioPlayer({ src }: AudioPlayerProps) {
  return (
    <section className="panel">
      <h2>Audiobook</h2>
      <audio controls src={src} aria-label="Generated audiobook" />
    </section>
  );
}

import { s } from "../strings";

interface Props {
  jobId: string;
  kind: "video" | "audio" | null | undefined;
}

/** The plain browser player on the job's small copy of the media; it only reads from the app, so seeking uses ranges. */
export function PreviewPlayer({ jobId, kind }: Props) {
  const S = s();
  const src = `/api/jobs/${jobId}/media`;
  return kind === "audio" ? (
    <audio className="player player-audio" controls preload="metadata" src={src} aria-label={S.preview.audioLabel} />
  ) : (
    <video className="player" controls preload="metadata" src={src} aria-label={S.preview.videoLabel} />
  );
}

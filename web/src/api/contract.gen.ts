/* Generated from the server's pydantic models by scripts/gen_types.py. Do not edit; regenerate. */

/**
 * One WebSocket frame from the server (named, so the generated types have a single union to switch on).
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "ServerMessage".
 */
export type ServerMessage =
  | SnapshotEvent
  | JobEvent
  | ProgressEvent
  | PreviewEvent
  | PausedEvent
  | RemovedEvent
  | MachineEvent
  | ModelsEvent
  | CardPackEvent;

/**
 * Every shape the server and the window exchange.
 */
export interface ApiContract {}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "AddJobRequest".
 */
export interface AddJobRequest {
  path: string;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "AddLinkRequest".
 */
export interface AddLinkRequest {
  url: string;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "CardPackEvent".
 */
export interface CardPackEvent {
  type: "card_pack";
  seq: number;
  card_pack: ModelsInfo;
}
/**
 * The speech model's files: downloaded once, on the first start; the queue waits until they are in.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "ModelsInfo".
 */
export interface ModelsInfo {
  state: "checking" | "absent" | "downloading" | "ready" | "failed";
  done_bytes: number;
  total_bytes: number;
  eta_s: number | null;
  error: string | null;
  error_kind: ("no_connection" | "dropped" | "secure" | "site" | "disk_full" | "no_space" | "damaged" | "other") | null;
  need_bytes: number | null;
  free_bytes: number | null;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "CheckRequest".
 */
export interface CheckRequest {
  cues: Cue[];
}
/**
 * One subtitle: its own text (one string per line) and times on the track's frame grid.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Cue-Input".
 */
export interface Cue {
  /**
   * Stable within its track; new cues take the track's next_cue_id
   */
  id: number;
  start_ms: number;
  end_ms: number;
  lines: string[];
  /**
   * The cue's translation, one string per line; None while not translated
   */
  translation?: string[] | null;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "CheckResult".
 */
export interface CheckResult {
  violations: Violation[];
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Violation".
 */
export interface Violation {
  cue_id: number;
  cue_index: number;
  rule:
    | "order"
    | "overlap"
    | "off_grid"
    | "empty"
    | "past_media_end"
    | "chars_per_line"
    | "lines"
    | "max_duration"
    | "min_duration"
    | "cps"
    | "min_gap"
    | "break_quality";
  severity: "hard" | "soft";
  value: number;
  limit: number;
  message: string;
}
/**
 * One subtitle: its own text (one string per line) and times on the track's frame grid.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Cue-Output".
 */
export interface Cue1 {
  /**
   * Stable within its track; new cues take the track's next_cue_id
   */
  id: number;
  start_ms: number;
  end_ms: number;
  lines: string[];
  /**
   * The cue's translation, one string per line; None while not translated
   */
  translation: string[] | null;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "DialogRequest".
 */
export interface DialogRequest {
  kind: "files" | "folder";
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "DialogResult".
 */
export interface DialogResult {
  paths: string[];
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Document".
 */
export interface Document {
  schema_version: 3;
  app_version: string;
  media: Media;
  run_config: RunConfig;
  segments: Segment[];
  /**
   * At most one track per preset
   */
  subtitles: SubtitleTrack[];
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Media".
 */
export interface Media {
  /**
   * Absolute path of the media file when it was transcribed
   */
  source: string;
  name: string;
  size_bytes: number;
  /**
   * Container duration reported by ffprobe
   */
  duration_ms: number;
  /**
   * Length of the decoded audio actually recognised
   */
  decoded_ms: number;
  has_video: boolean;
  /**
   * None for audio-only files
   */
  frame_rate: FrameRate | null;
  /**
   * Which audio track was used (0 = first)
   */
  audio_stream_index: number;
  audio_streams: number;
  /**
   * Original sample rate of that track
   */
  sample_rate_hz: number;
  /**
   * Original channel count of that track
   */
  channels: number;
}
/**
 * Exact video frame rate as a fraction, e.g. 30000/1001.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "FrameRate".
 */
export interface FrameRate {
  num: number;
  den: number;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "RunConfig".
 */
export interface RunConfig {
  engine: string;
  /**
   * Model repository and exact revision
   */
  model: string;
  /**
   * Execution provider the model actually ran on
   */
  provider: string;
  /**
   * Engine-specific settings that were used
   */
  engine_options: {
    [k: string]: string | number | boolean | undefined;
  };
  vad: VadConfig;
  decode_sample_rate_hz: number;
  decode_channels: number;
  /**
   * App, git commit, libraries, tools, GPU and driver
   */
  versions: {
    [k: string]: string | undefined;
  };
  created_utc: string;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "VadConfig".
 */
export interface VadConfig {
  model: string;
  threshold: number;
  min_speech_duration_ms: number;
  min_silence_duration_ms: number;
  speech_pad_ms: number;
  max_speech_duration_s: number;
}
/**
 * One stretch of speech as the engine recognised it (a pause or the window length bounds it).
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Segment".
 */
export interface Segment {
  start_ms: number;
  end_ms: number;
  words: Word[];
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Word".
 */
export interface Word {
  text: string;
  start_ms: number;
  end_ms: number;
  /**
   * Engine confidence 0..1, if it gives one
   */
  conf: number | null;
}
/**
 * The cues one preset produced (and the user may have edited).
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "SubtitleTrack".
 */
export interface SubtitleTrack {
  preset_id: string;
  preset: Preset;
  /**
   * Fingerprint of the preset, its word lists and the segmenter version
   */
  digest: string;
  segmenter_version: number;
  frame_rate: FrameRate1;
  created_utc: string;
  /**
   * True once a person changed a cue; never re-segmented silently
   */
  edited: boolean;
  /**
   * The id the next new cue gets; ids are never reused after a delete
   */
  next_cue_id: number;
  /**
   * Language code of the cues' translations (e.g. cs)
   */
  translation_language: string | null;
  cues: Cue1[];
}
/**
 * The rule values used, kept so the track reads the same if the preset changes
 */
export interface Preset {
  id: string;
  name: string;
  description: string;
  language: string;
  limits: Limits;
  timing: Timing;
  segmenter: SegmenterWeights;
  severity: Severities;
  text: TextRules;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Limits".
 */
export interface Limits {
  max_chars_per_line: number;
  max_lines: number;
  max_cps: number;
  cps_counts_spaces: boolean;
  min_duration_ms: number;
  max_duration_ms: number;
  min_gap_frames: number;
  close_gaps_up_to_frames: number;
  /**
   * Frame grid for audio-only media, e.g. 25/1
   */
  default_fps: string;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Timing".
 */
export interface Timing {
  lead_in_ms: number;
  end_hold_ms: number;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "SegmenterWeights".
 */
export interface SegmenterWeights {
  block_pause_ms: number;
  pause_hint_ms: number;
  cost_sentence_end: number;
  cost_clause: number;
  cost_pause: number;
  cost_before_conjunction: number;
  cost_plain: number;
  cost_forbidden: number;
  cost_per_cue: number;
  cost_cps_over: number;
  cost_too_short: number;
  cost_sentence_piece: number;
  sentence_piece_chars: number;
  cost_line_imbalance: number;
  cost_top_heavy: number;
  cost_forced: number;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Severities".
 */
export interface Severities {
  chars_per_line: "hard" | "soft";
  lines: "hard" | "soft";
  max_duration: "hard" | "soft";
  min_gap: "hard" | "soft";
  cps: "hard" | "soft";
  min_duration: "hard" | "soft";
  break_quality: "hard" | "soft";
}
/**
 * How the recognised words are tidied for the cues (tidy.py); the document keeps them as recognised.
 */
export interface TextRules {
  drop_fillers: boolean;
  window_capitals: boolean;
}
/**
 * Exact video frame rate as a fraction, e.g. 30000/1001.
 */
export interface FrameRate1 {
  num: number;
  den: number;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "ErrorResponse".
 */
export interface ErrorResponse {
  error: string;
}
/**
 * 409 from export: these files exist; ask the user, then send the request again with replace = true.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "ExportConflict".
 */
export interface ExportConflict {
  error: string;
  existing: string[];
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "ExportRequest".
 */
export interface ExportRequest {
  presets: string[];
  formats: string[];
  replace?: boolean;
  /**
   * @minItems 1
   */
  texts?: ["original" | "translation", ...("original" | "translation")[]];
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "ExportResult".
 */
export interface ExportResult {
  folder: string;
  files: string[];
  skipped: string[];
}
/**
 * A media file on this machine, used where it is.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "FileSource".
 */
export interface FileSource {
  kind: "file";
  path: string;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Job".
 */
export interface Job {
  id: string;
  name: string;
  source: FileSource | UploadSource | LinkSource;
  state: "fetching" | "queued" | "running" | "done" | "failed" | "cancelled";
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  attempts: number;
  error: string | null;
  note: string | null;
  project_file: string | null;
  progress: JobProgress | null;
  engine: string;
  exported: string[];
  preview: JobPreview | null;
  media_s: number | null;
}
/**
 * A file dropped onto the window: the page cannot say where it lives, so the app keeps its own copy.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "UploadSource".
 */
export interface UploadSource {
  kind: "upload";
  upload_id: string;
  path: string;
  original_name: string;
  had_video: boolean;
  frame_rate: string | null;
}
/**
 * A recording behind a download link. Only its sound is fetched and kept (sealed, in the upload folder); the
 * link itself lives sealed beside it and never appears in a message - a presigned link's signature is a secret.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "LinkSource".
 */
export interface LinkSource {
  kind: "link";
  upload_id: string;
  path: string;
  shown: string;
  had_video: boolean;
  frame_rate: string | null;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "JobProgress".
 */
export interface JobProgress {
  phase: "fetch" | ("load" | "decode" | "speech" | "recognise" | "done");
  fraction: number;
  media_done_s: number;
  media_total_s: number;
  eta_s: number | null;
}
/**
 * The small copy of the media the window plays, and its waveform. Live only: rebuilt from disk on every start.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "JobPreview".
 */
export interface JobPreview {
  state: "waiting" | "making" | "ready" | "failed";
  kind: ("video" | "audio") | null;
  fraction: number;
  media_done_s: number;
  media_total_s: number;
  error: string | null;
  peaks_per_s: number | null;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "JobEvent".
 */
export interface JobEvent {
  type: "job";
  seq: number;
  job: Job;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "MachineEvent".
 */
export interface MachineEvent {
  type: "machine";
  seq: number;
  machine: MachineInfo;
}
/**
 * What this PC runs the model on, and how fast: a job takes about fixed_s + media_s * per_s seconds.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "MachineInfo".
 */
export interface MachineInfo {
  device: "cuda" | "cpu";
  reason:
    | "card"
    | "no_driver"
    | "no_card"
    | "card_too_old"
    | "card_too_small"
    | "no_card_support"
    | "card_pack_missing"
    | "driver_too_old";
  card: string | null;
  cpu_threads: number | null;
  fixed_s: number | null;
  per_s: number | null;
  measured_by: ("test" | "jobs") | null;
  measuring: boolean;
  measure_waiting: boolean;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "ModelsEvent".
 */
export interface ModelsEvent {
  type: "models";
  seq: number;
  models: ModelsInfo;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "PausedEvent".
 */
export interface PausedEvent {
  type: "paused";
  seq: number;
  reason: string | null;
  kind: ("gpu_busy" | "gpu_memory" | "disk_full") | null;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Preset".
 */
export interface Preset1 {
  id: string;
  name: string;
  description: string;
  language: string;
  limits: Limits;
  timing: Timing;
  segmenter: SegmenterWeights;
  severity: Severities;
  text: TextRules;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "PresetInfo".
 */
export interface PresetInfo {
  id: string;
  kind: "builtin" | "custom" | "retired";
  name: string;
  description: string;
  max_chars_per_line: number;
  max_lines: number;
  max_cps: number;
  max_duration_ms: number;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "PreviewEvent".
 */
export interface PreviewEvent {
  type: "preview";
  seq: number;
  job_id: string;
  preview: JobPreview | null;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "ProgressEvent".
 */
export interface ProgressEvent {
  type: "progress";
  seq: number;
  job_id: string;
  progress: JobProgress;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "RemovedEvent".
 */
export interface RemovedEvent {
  type: "removed";
  seq: number;
  job_id: string;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "ResegmentRequest".
 */
export interface ResegmentRequest {
  base_revision: string;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "SaveTrackRequest".
 */
export interface SaveTrackRequest {
  base_revision: string;
  cues: Cue[];
  next_cue_id: number;
  translation_language?: string | null;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "SnapshotEvent".
 */
export interface SnapshotEvent {
  type: "snapshot";
  seq: number;
  status: Status;
}
/**
 * The whole state, sent on every (re)connect and from GET /api/status.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Status".
 */
export interface Status {
  app: "parakeet-stt";
  app_version: string;
  boot_id: string;
  seq: number;
  paused_reason: string | null;
  paused_kind: ("gpu_busy" | "gpu_memory" | "disk_full") | null;
  jobs: Job[];
  storage_locked: boolean;
  machine: MachineInfo | null;
  models: ModelsInfo | null;
  card_pack: ModelsInfo | null;
}
/**
 * The user's choices, kept in <data>/settings.json (not in the browser, which can be reset).
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "Settings".
 */
export interface Settings {
  schema_version: number;
  engine: string;
  export_presets: string[];
  export_formats: string[];
  output: "beside_source" | "folder";
  output_dir: string | null;
  language: "en" | "cs";
  look: "light" | "dark";
  glossary_text: string;
  custom_presets: Preset1[];
  retired_presets: Preset1[];
  /**
   * @maxItems 200
   */
  type_order: string[];
}
/**
 * GET /api/settings: the stored choices plus what can be chosen.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "SettingsView".
 */
export interface SettingsView {
  settings: Settings;
  output_dir_default: string;
  engines: string[];
  presets: PresetInfo[];
  formats: string[];
  account: string;
  glossary_builtin: string;
  standards: Preset1[];
  builtin_presets: Preset1[];
}
/**
 * How the recognised words are tidied for the cues (tidy.py); the document keeps them as recognised.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "TextRules".
 */
export interface TextRules1 {
  drop_fillers: boolean;
  window_capitals: boolean;
}
/**
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "TrackSaved".
 */
export interface TrackSaved {
  revision: string;
  next_cue_id: number;
  violations: Violation[];
}
/**
 * GET /api/jobs/<id>/tracks/<preset>: one subtitle track to edit, with what the rule checker finds in it.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "TrackView".
 */
export interface TrackView {
  track: SubtitleTrack;
  revision: string;
  media_end_ms: number;
  violations: Violation[];
  preset_changed: boolean;
}
/**
 * GET /api/jobs/<id>/words: every recognised word, in order; GET /api/jobs/<id>/tracks/<preset>/words: the
 * words as that type's cues have them (fillers dropped, capitals tidied), so a split finds where a word starts.
 *
 * This interface was referenced by `ApiContract`'s JSON-Schema
 * via the `definition` "WordsView".
 */
export interface WordsView {
  words: Word[];
}
